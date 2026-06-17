"""HAR-style session recorder.

Design: a durable JSON Lines transcript is written live (one record per event, so
a crash loses nothing), and a single assembled HAR file is emitted on clean exit.
`py3270-convert` can rebuild the HAR from the JSONL if a session crashed, and can
project either file down to a flat event log or plain-text screen dumps.

A "transaction" is the mainframe analog of a HAR entry: an AID submission
(Enter / PFn / PAn / Clear / the initial Connect) is the *request*, and the screen
the host paints in response — observed when the keyboard unlocks — is the *response*.
Keystrokes typed into fields before the AID are recorded under the request.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone

from . import __version__


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class Recorder:
    def __init__(self, jsonl_path: str, har_path: str, session: dict):
        self.jsonl_path = jsonl_path
        self.har_path = har_path
        self.session = dict(session)
        self.session["startedDateTime"] = _now_iso()
        self.entries: list[dict] = []
        self._pending_keystrokes: list[dict] = []
        self._cur: dict | None = None
        self._jsonl = open(jsonl_path, "a", buffering=1, encoding="utf-8")
        self._emit({"type": "session", **self.session})

    def _emit(self, rec: dict) -> None:
        self._jsonl.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def note(self, **kw) -> None:
        """Emit a diagnostic note (e.g. terminal viewport size)."""
        self._emit({"type": "note", "t": _now_iso(), **kw})

    # -- events --------------------------------------------------------------

    def keystroke(self, key: str, value: str | None, row: int, col: int) -> None:
        rec = {
            "type": "keystroke",
            "t": _now_iso(),
            "key": key,
            "value": value,
            "row": row,
            "col": col,
        }
        self._pending_keystrokes.append(rec)
        self._emit(rec)

    def begin_transaction(self, aid: str, cursor: dict, screen_before: dict) -> None:
        idx = len(self.entries)
        self._cur = {
            "index": idx,
            "startedDateTime": _now_iso(),
            "_t0": time.monotonic(),
            "request": {
                "aid": aid,
                "cursor": cursor,
                "keystrokes": self._pending_keystrokes,
                "screenBefore": screen_before,
            },
        }
        self._pending_keystrokes = []
        self._emit(
            {
                "type": "request",
                "index": idx,
                "t": self._cur["startedDateTime"],
                "aid": aid,
                "cursor": cursor,
                "keystrokes": self._cur["request"]["keystrokes"],
            }
        )

    def complete_transaction(self, screen_after: dict, fields: list[dict], keyboard: str) -> None:
        if self._cur is None:
            return
        e = self._cur
        e["time"] = int((time.monotonic() - e.pop("_t0")) * 1000)
        e["response"] = {
            "receivedDateTime": _now_iso(),
            "keyboard": keyboard,
            "screen": screen_after,
            "fields": fields,
        }
        self.entries.append(e)
        self._emit(
            {
                "type": "response",
                "index": e["index"],
                "t": e["response"]["receivedDateTime"],
                "time": e["time"],
                "keyboard": keyboard,
                "screen": screen_after,
                "fields": fields,
            }
        )
        self._cur = None

    @property
    def awaiting(self) -> bool:
        return self._cur is not None

    # -- teardown ------------------------------------------------------------

    def finalize(self) -> None:
        # If a transaction was still open (e.g. user quit mid-wait), close it out
        # with whatever the request captured so it isn't silently dropped.
        if self._cur is not None:
            self._cur.pop("_t0", None)
            self._cur["time"] = None
            self._cur["response"] = None
            self.entries.append(self._cur)
            self._cur = None
        har = {
            "log": {
                "version": "1.0",
                "creator": {"name": "py3270cap", "version": __version__},
                "session": self.session,
                "entries": self.entries,
            }
        }
        with open(self.har_path, "w", encoding="utf-8") as f:
            json.dump(har, f, indent=2, ensure_ascii=False)
        self._emit({"type": "end", "t": _now_iso(), "entries": len(self.entries)})
        self._jsonl.close()


def screen_obj(rows: list[str], cursor: dict) -> dict:
    return {"rows": rows, "cursor": cursor}
