"""Curses front-end that renders s3270's screen and forwards every keystroke.

Field editing is left entirely to s3270 (protection, numeric lock, autoskip,
insert mode all behave correctly) — each keystroke is forwarded immediately and
the screen is re-read. AID keys (Enter / PFn / PAn / Clear) open a capture
transaction; it closes when the keyboard unlocks, i.e. when the host has painted
its reply.
"""

from __future__ import annotations

import curses
import time

from .capture import Recorder, screen_obj
from .s3270 import S3270, S3270Error, Status

POLL_MS = 100  # input tick: how often get_wch wakes to render / check state
IDLE_POLL_S = 1.0  # when idle, re-read the host screen at most this often


class Emulator:
    def __init__(
        self,
        stdscr,
        s: S3270,
        recorder: Recorder,
        host_label: str,
        connect_str: str,
        mask_hidden: bool = True,
    ):
        self.scr = stdscr
        self.s = s
        self.rec = recorder
        self.host_label = host_label
        self.connect_str = connect_str
        self.mask_hidden = mask_hidden  # mask keystrokes typed into non-display fields
        self.rows = 24
        self.cols = 80
        self.cur_rows: list[str] = []
        self.cursor = {"row": 0, "col": 0}
        self.fields: list[dict] = []  # field map of the current screen (from s3270)
        self.status = Status(raw="")
        self.message = "^] menu  |  F1-F12 = PF1-12  |  Enter/Tab/arrows as usual"
        self.quit = False

    def _hidden_at(self, row: int, col: int) -> bool:
        """Is (row,col) inside a non-display field? Used to mask password input.

        A field attribute governs the cells *after* it until the next field start
        (wrapping around the buffer). Best-effort: if field data is unavailable we
        return False (no masking) rather than guessing.
        """
        if not self.fields:
            return False
        target = row * self.cols + col
        gov = None
        best = -1
        last = None
        last_pos = -1
        for f in self.fields:
            pos = f["row"] * self.cols + f["col"]
            if pos > last_pos:
                last_pos, last = pos, f
            if pos <= target and pos > best:
                best, gov = pos, f
        if gov is None:  # cursor precedes the first field -> wraps to the last field
            gov = last
        return bool(gov and gov.get("nondisplay"))

    # -- s3270 plumbing ------------------------------------------------------

    def _refresh_state(self) -> bool:
        """Re-read the screen + status from s3270. Returns True if anything changed."""
        rows, st = self.s.ascii_screen()
        rows = [r[: self.cols].ljust(self.cols) for r in rows]
        changed = rows != self.cur_rows or (
            st.cursor_row != self.cursor["row"] or st.cursor_col != self.cursor["col"]
        ) or st.keyboard != self.status.keyboard
        self.cur_rows = rows
        self.cursor = {"row": st.cursor_row, "col": st.cursor_col}
        self.status = st
        self.rows, self.cols = st.rows or self.rows, st.cols or self.cols
        return changed

    def _screen_snapshot(self) -> dict:
        return screen_obj(list(self.cur_rows), dict(self.cursor))

    def _send_aid(self, aid: str, action: str) -> None:
        if self.status.locked:
            # Can't submit while the host holds input control; don't open a transaction.
            self.message = f"{aid} ignored — keyboard locked (X SYSTEM)"
            return
        # Capture the request against the screen as it stands *before* submitting.
        self.rec.begin_transaction(aid, dict(self.cursor), self._screen_snapshot())
        try:
            self.s.exec(action)
        except S3270Error as e:
            self.message = f"error: {e}"
        self._refresh_state()  # show the (now likely locked) screen immediately

    def _maybe_complete(self) -> None:
        if self.rec.awaiting and not self.status.locked:
            fields = self.s.read_fields()
            self.fields = fields  # cache for hidden-field detection while typing
            self.rec.complete_transaction(self._screen_snapshot(), fields, "unlocked")

    # -- rendering -----------------------------------------------------------

    def _too_small(self, max_y: int, max_x: int) -> bool:
        # We need one extra line below the screen for the status/OIA bar.
        return max_y < self.rows + 1 or max_x < self.cols

    def _render_frame(self) -> bool:
        """Draw the appropriate frame for the current window size.

        Returns True if the window is too small (resize prompt shown instead of
        the screen), so the caller can gate input.
        """
        max_y, max_x = self.scr.getmaxyx()
        if self._too_small(max_y, max_x):
            self._render_too_small(max_y, max_x)
            return True
        self._render()
        return False

    def _render_too_small(self, max_y: int, max_x: int) -> None:
        self.scr.erase()
        w = max(0, max_x - 1)
        msg = [
            "Terminal too small to show the full 3270 screen.",
            f"  have:  {max_y} rows x {max_x} cols",
            f"  need:  {self.rows + 1} rows x {self.cols} cols  ({self.rows}x{self.cols} screen + 1 status line)",
            "",
            "Resize this window — it redraws automatically.",
            "Or press  ^]  then  q  to quit.",
        ]
        for i, m in enumerate(msg):
            if i < max_y:
                try:
                    self.scr.addnstr(i, 0, m[:w], w)
                except curses.error:
                    pass
        self.scr.refresh()

    def _render(self) -> None:
        self.scr.erase()
        max_y, max_x = self.scr.getmaxyx()
        width = min(self.cols, max_x)
        nrows = min(self.rows, max_y)
        for r in range(nrows):
            text = (self.cur_rows[r] if r < len(self.cur_rows) else "").ljust(self.cols)[:width]
            try:
                self.scr.addnstr(r, 0, text, width)
            except curses.error:
                pass  # bottom-right cell write is allowed to fail
        # status/OIA bar on the line *below* the screen (only if there's room)
        status_row = self.rows
        if status_row <= max_y - 1:
            lock = "X SYSTEM" if self.status.locked else "READY   "
            conn = "connected" if self.status.connected else "DISCONNECTED"
            bar = (
                f" {self.host_label} [{conn}] {lock} "
                f"row {self.cursor['row'] + 1} col {self.cursor['col'] + 1}  "
                f"REC  {self.message}"
            )
            bar = bar[: max_x - 1].ljust(max_x - 1)
            try:
                self.scr.attron(curses.A_REVERSE)
                self.scr.addnstr(status_row, 0, bar, max_x - 1)
                self.scr.attroff(curses.A_REVERSE)
            except curses.error:
                pass
        # place hardware cursor
        cy, cx = self.cursor["row"], self.cursor["col"]
        if 0 <= cy < max_y and 0 <= cx < max_x:
            try:
                self.scr.move(cy, cx)
            except curses.error:
                pass
        self.scr.refresh()

    # -- input ---------------------------------------------------------------

    def _handle_key(self, ch) -> None:
        # While the keyboard is locked (X SYSTEM) the host has input control: a real
        # 3270 inhibits all input except Reset/Attn. Honoring that here also prevents
        # firing a second AID over an in-flight one (which corrupts the transaction
        # log). Only the menu (Ctrl-]) and Reset (Ctrl-R) get through.
        if self.status.locked and ch not in ("\x1d", 29, "\x12", 18):
            self.message = "X SYSTEM — input inhibited; wait, or Ctrl-R to reset, ^] for menu"
            return
        # Special / navigation keys -------------------------------------------------
        if ch in ("\n", "\r", curses.KEY_ENTER, 10, 13):
            self._send_aid("Enter", "Enter")
        elif ch == "\t" or ch == 9:
            self._send_edit("Tab", "Tab")
        elif ch == curses.KEY_BTAB:
            self._send_edit("BackTab", "BackTab")
        elif ch in (curses.KEY_BACKSPACE, "\x7f", "\x08", 127, 8):
            self._send_edit("BackSpace", "BackSpace")
        elif ch == curses.KEY_DC:
            self._send_edit("Delete", "Delete")
        elif ch == curses.KEY_LEFT:
            self._send_edit("Left", "Left")
        elif ch == curses.KEY_RIGHT:
            self._send_edit("Right", "Right")
        elif ch == curses.KEY_UP:
            self._send_edit("Up", "Up")
        elif ch == curses.KEY_DOWN:
            self._send_edit("Down", "Down")
        elif ch == curses.KEY_HOME:
            self._send_edit("Home", "Home")
        elif ch == curses.KEY_END:
            self._send_edit("FieldEnd", "FieldEnd")
        elif ch == curses.KEY_IC:
            self._send_edit("Insert", "Insert")
        elif isinstance(ch, int) and curses.KEY_F1 <= ch <= curses.KEY_F12:
            n = ch - curses.KEY_F1 + 1
            self._send_aid(f"PF{n}", f"PF({n})")
        elif ch in ("\x1d", 29):  # Ctrl-]
            self._menu()
        elif ch in ("\x12", 18):  # Ctrl-R = Reset (unlock keyboard)
            self._send_edit("Reset", "Reset")
        elif isinstance(ch, str) and len(ch) == 1 and (ch.isprintable()):
            cp = ord(ch)
            row, col = self.cursor["row"], self.cursor["col"]
            hidden = self.mask_hidden and self._hidden_at(row, col)
            # Mask characters typed into non-display (password) fields so they don't
            # land in the keystroke log in plaintext. Length is preserved.
            self.rec.keystroke("Char", "•" if hidden else ch, row, col)
            try:
                self.s.exec(f"Key(U+{cp:04X})")
            except S3270Error as e:
                self.message = f"error: {e}"
            self._refresh_state()
        # anything else is ignored

    def _send_edit(self, name: str, action: str) -> None:
        self.rec.keystroke(name, None, self.cursor["row"], self.cursor["col"])
        try:
            self.s.exec(action)
        except S3270Error as e:
            self.message = f"error: {e}"
        self._refresh_state()

    def _menu(self) -> None:
        self.message = "(q)uit (r)eset (c)lear (a)ttn (p)f# pa(1/2/3) (h)elp  ESC=cancel"
        self._render_frame()
        self.scr.timeout(-1)
        try:
            ch = self.scr.get_wch()
        except curses.error:
            ch = None
        self.scr.timeout(POLL_MS)
        if ch in ("q", "Q"):
            self.quit = True
        elif ch in ("r", "R"):
            self._send_edit("Reset", "Reset")
        elif ch in ("c", "C"):
            self._send_aid("Clear", "Clear")
        elif ch in ("a", "A"):
            self._send_edit("Attn", "Attn")
        elif ch in ("1", "2", "3"):
            self._send_aid(f"PA{ch}", f"PA({ch})")
        elif ch in ("p", "P"):
            self._prompt_pf()
        elif ch in ("h", "H", "?"):
            self._help()
        else:
            self.message = "cancelled"

    def _prompt_pf(self) -> None:
        digits = ""
        self.scr.timeout(-1)
        while True:
            self.message = f"PF number: {digits}  (Enter to send)"
            self._render_frame()
            try:
                ch = self.scr.get_wch()
            except curses.error:
                continue
            if isinstance(ch, str) and ch.isdigit():
                digits += ch
            elif ch in ("\n", "\r", curses.KEY_ENTER, 10, 13):
                break
            else:
                break
        self.scr.timeout(POLL_MS)
        if digits and 1 <= int(digits) <= 24:
            n = int(digits)
            self._send_aid(f"PF{n}", f"PF({n})")
        else:
            self.message = "no PF sent"

    def _help(self) -> None:
        lines = [
            "py3270cap — keys",
            "",
            "  Enter            submit (AID Enter)",
            "  Tab / Shift-Tab  next / previous field",
            "  arrows, Home     move cursor",
            "  Backspace, Del   edit field",
            "  Insert           toggle insert mode",
            "  F1..F12          PF1..PF12",
            "  Ctrl-R           Reset (unlock keyboard)",
            "  Ctrl-]           menu: quit, clear, attn, PA1-3, PF13-24",
            "",
            "Everything you do is being captured. Press any key.",
        ]
        self.scr.erase()
        for i, ln in enumerate(lines):
            try:
                self.scr.addnstr(i, 2, ln, self.cols)
            except curses.error:
                pass
        self.scr.refresh()
        self.scr.timeout(-1)
        try:
            self.scr.get_wch()
        except curses.error:
            pass
        self.scr.timeout(POLL_MS)
        self.message = ""

    # -- main loop -----------------------------------------------------------

    def run(self) -> None:
        try:
            curses.curs_set(1)
        except curses.error:
            pass  # some terminals can't set cursor visibility
        self.scr.keypad(True)
        self.scr.timeout(POLL_MS)

        # Initial connect is itself a transaction: blank screen -> first host screen.
        self._refresh_state()
        self.rec.begin_transaction("Connect", dict(self.cursor), self._screen_snapshot())
        self.message = f"connecting to {self.host_label} ..."
        self._render_frame()
        try:
            r = self.s.connect(self.connect_str)
            if not r.ok:
                self.message = "connect failed (see status); ^] q to quit"
        except S3270Error as e:
            self.message = f"connect error: {e}; ^] q to quit"
        self._refresh_state()
        self.message = "^] menu  |  F1-F12 = PF1-12  |  Enter/Tab/arrows as usual"
        last_poll = time.monotonic()
        my, mx = self.scr.getmaxyx()
        self.rec.note(event="viewport", term_rows=my, term_cols=mx,
                      screen_rows=self.rows, screen_cols=self.cols)
        last_viewport = (my, mx)

        while not self.quit:
            # Only talk to s3270 when it can matter: while awaiting a host reply
            # (poll fast until the keyboard unlocks) or on a slow idle tick to catch
            # rare unsolicited host writes. This keeps s3270's trace and CPU sane —
            # 3270 is request/response, so the screen is otherwise static.
            now = time.monotonic()
            if self.rec.awaiting or (now - last_poll) >= IDLE_POLL_S:
                try:
                    self._refresh_state()
                except S3270Error as e:
                    self.message = f"s3270 gone: {e}"
                    self._render_frame()
                    break
                self._maybe_complete()
                last_poll = now

            max_y, max_x = self.scr.getmaxyx()
            if (max_y, max_x) != last_viewport:
                self.rec.note(event="viewport", term_rows=max_y, term_cols=max_x,
                              screen_rows=self.rows, screen_cols=self.cols)
                last_viewport = (max_y, max_x)
            # Render every tick from cache. curses.refresh() diffs against the
            # physical screen, so unchanged frames cost nothing.
            too_small = self._render_frame()

            try:
                ch = self.scr.get_wch()
            except curses.error:
                continue  # input tick timeout, no key
            if ch == curses.KEY_RESIZE:
                continue  # re-render on next tick at the new size
            if too_small:
                # Only allow the menu (to quit) until the window is big enough.
                if ch in ("\x1d", 29):
                    self._menu()
                continue
            self._handle_key(ch)  # this re-reads + renders after the action
            try:
                self._refresh_state()
            except S3270Error:
                pass
            self._maybe_complete()
            last_poll = time.monotonic()
            self._render_frame()
