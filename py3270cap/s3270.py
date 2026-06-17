"""Driver for the s3270 scripting engine.

s3270 reads one action per line on stdin and answers on stdout with zero or more
`data: ` lines, then a single status line, then `ok` or `error`. We talk to it
through a background reader thread + queue so action calls get clean timeouts and
we never deadlock on a half-read response.

Status line is 12 whitespace-separated fields (x3270 scripting protocol):
  0 keyboard-state   U=unlocked L=locked E=error-locked
  1 screen-format    F=formatted U=unformatted
  2 field-protect    U/P at cursor
  3 connection       C(host)=connected  N=not connected
  4 emulator-mode    I=3270  L=linemode  C=char  P=unnegotiated  N=disconnected
  5 model number     2-5
  6 rows
  7 cols
  8 cursor row (0-origin)
  9 cursor col (0-origin)
  10 window id
  11 command exec time
"""

from __future__ import annotations

import queue
import subprocess
import threading
from dataclasses import dataclass


class S3270Error(RuntimeError):
    pass


@dataclass
class Status:
    raw: str
    keyboard: str = "U"
    formatted: str = "U"
    protect: str = "U"
    connection: str = "N"
    mode: str = "N"
    model: str = "2"
    rows: int = 24
    cols: int = 80
    cursor_row: int = 0
    cursor_col: int = 0

    @classmethod
    def parse(cls, line: str) -> "Status":
        f = line.split()
        if len(f) < 12:
            # Not a well-formed status line; return defaults but keep raw.
            return cls(raw=line)
        try:
            return cls(
                raw=line,
                keyboard=f[0],
                formatted=f[1],
                protect=f[2],
                connection=f[3],
                mode=f[4],
                model=f[5],
                rows=int(f[6]),
                cols=int(f[7]),
                cursor_row=int(f[8]),
                cursor_col=int(f[9]),
            )
        except ValueError:
            return cls(raw=line)

    @property
    def connected(self) -> bool:
        return self.connection.startswith("C(")

    @property
    def locked(self) -> bool:
        return self.keyboard != "U"


@dataclass
class Result:
    ok: bool
    data: list[str]
    status: Status


class S3270:
    def __init__(
        self,
        model: str = "3279-2-E",
        trace_file: str | None = None,
        utf8: bool = True,
        extra_args: list[str] | None = None,
    ):
        args = ["s3270", "-model", model]
        if utf8:
            args.append("-utf8")
        if trace_file:
            args += ["-trace", "-tracefile", trace_file]
        if extra_args:
            args += extra_args
        try:
            self.proc = subprocess.Popen(
                args,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
        except FileNotFoundError as e:
            raise S3270Error(
                "s3270 not found on PATH. Install the x3270 suite "
                "(macOS: `brew install x3270`)."
            ) from e

        self._q: queue.Queue[str | None] = queue.Queue()
        self._stderr: list[str] = []
        self._reader = threading.Thread(target=self._read_stdout, daemon=True)
        self._reader.start()
        self._errreader = threading.Thread(target=self._read_stderr, daemon=True)
        self._errreader.start()

    def _read_stdout(self) -> None:
        assert self.proc.stdout
        for line in self.proc.stdout:
            self._q.put(line.rstrip("\n"))
        self._q.put(None)  # EOF sentinel

    def _read_stderr(self) -> None:
        assert self.proc.stderr
        for line in self.proc.stderr:
            self._stderr.append(line.rstrip("\n"))

    def exec(self, action: str, timeout: float = 30.0) -> Result:
        if self.proc.poll() is not None:
            raise S3270Error(
                f"s3270 exited (code {self.proc.returncode}). "
                f"stderr: {' / '.join(self._stderr[-5:])}"
            )
        assert self.proc.stdin
        try:
            self.proc.stdin.write(action + "\n")
            self.proc.stdin.flush()
        except BrokenPipeError as e:
            raise S3270Error("s3270 pipe broke (process gone).") from e

        data: list[str] = []
        status: Status | None = None
        while True:
            try:
                line = self._q.get(timeout=timeout)
            except queue.Empty:
                raise S3270Error(f"timeout waiting for response to {action!r}")
            if line is None:
                raise S3270Error(
                    f"s3270 closed its output during {action!r}. "
                    f"stderr: {' / '.join(self._stderr[-5:])}"
                )
            if line.startswith("data: "):
                data.append(line[6:])
            elif line in ("ok", "error"):
                if status is None:
                    status = Status(raw="")
                return Result(ok=(line == "ok"), data=data, status=status)
            else:
                status = Status.parse(line)

    # -- convenience wrappers ------------------------------------------------

    def connect(self, host: str) -> Result:
        # Quote in case the host string contains characters s3270 parses.
        return self.exec(f'Connect("{host}")', timeout=60.0)

    def ascii_screen(self) -> tuple[list[str], Status]:
        """Return the screen as plain text rows plus current status."""
        r = self.exec("Ascii")
        return r.data, r.status

    def read_fields(self) -> list[dict]:
        """Best-effort field map from ReadBuffer(Ebcdic).

        In Ebcdic mode every buffer position is a clean space-separated token:
        a 2-hex EBCDIC byte, or SF(...) / SA(...) / GE(...). Field attribute
        bits (basic attr `c0`): 0x20 protected, 0x10 numeric, 0x0C intensity
        (0x08 nondisplay, 0x0C intensified), 0x01 modified.
        """
        try:
            r = self.exec("ReadBuffer(Ebcdic)")
        except S3270Error:
            return []
        fields: list[dict] = []
        for row, line in enumerate(r.data):
            col = 0
            for tok in line.split():
                if tok.startswith("SF(") or tok.startswith("SFE("):
                    attr = _basic_attr(tok)
                    fields.append(
                        {
                            "row": row,
                            "col": col,
                            "protected": bool(attr & 0x20),
                            "numeric": bool(attr & 0x10),
                            "nondisplay": (attr & 0x0C) == 0x08,
                            "intensified": (attr & 0x0C) == 0x0C,
                            "modified": bool(attr & 0x01),
                        }
                    )
                    col += 1
                elif tok.startswith("SA(") or tok.startswith("MF("):
                    continue  # no buffer position consumed
                else:
                    col += 1  # ordinary char or GE(xx)
        return fields

    def close(self) -> None:
        try:
            if self.proc.poll() is None:
                self.exec("Quit", timeout=5.0)
        except S3270Error:
            pass
        try:
            self.proc.terminate()
        except ProcessLookupError:
            pass


def _basic_attr(tok: str) -> int:
    """Extract the basic field-attribute byte from an SF(...)/SFE(...) token.

    Token looks like SF(c0=e8) or SFE(c0=e8,41=f4,...). We want the value paired
    with the `c0` attribute type.
    """
    inner = tok[tok.index("(") + 1 : tok.rindex(")")]
    for pair in inner.split(","):
        if "=" in pair:
            k, v = pair.split("=", 1)
            if k.strip().lower() == "c0":
                try:
                    return int(v, 16)
                except ValueError:
                    return 0
    return 0
