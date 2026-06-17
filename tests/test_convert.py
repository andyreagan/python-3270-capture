"""Tests for the converter (HAR/JSONL -> events / text / rebuilt HAR)."""

from __future__ import annotations

import json

from py3270cap.capture import Recorder, screen_obj
from py3270cap.convert import _har_from_jsonl, _load, _write_events, _write_text


def _session(tmp_path):
    rec = Recorder(
        str(tmp_path / "s.jsonl"),
        str(tmp_path / "s.har"),
        {"host": "mainframe", "connect": "mainframe:23", "model": "3279-2-E", "tracefile": None},
    )
    blank = ["".ljust(80) for _ in range(24)]
    logon = list(blank)
    logon[0] = "WELCOME".ljust(80)
    rec.begin_transaction("Connect", {"row": 0, "col": 0}, screen_obj(blank, {"row": 0, "col": 0}))
    rec.complete_transaction(screen_obj(logon, {"row": 21, "col": 19}), [], "unlocked")
    for ch in "3.4":
        rec.keystroke("Char", ch, 21, 19)
    after = list(blank)
    after[0] = "DATA SET LIST".ljust(80)
    rec.begin_transaction(
        "Enter", {"row": 21, "col": 22}, screen_obj(logon, {"row": 21, "col": 22})
    )
    rec.complete_transaction(screen_obj(after, {"row": 3, "col": 0}), [], "unlocked")
    rec.finalize()
    return rec


def test_har_from_jsonl(tmp_path):
    _session(tmp_path)
    log = _har_from_jsonl(str(tmp_path / "s.jsonl"))
    assert [e["request"]["aid"] for e in log["entries"]] == ["Connect", "Enter"]
    typed = "".join(
        k["value"] for k in log["entries"][1]["request"]["keystrokes"] if k["key"] == "Char"
    )
    assert typed == "3.4"


def test_load_accepts_har_and_jsonl(tmp_path):
    _session(tmp_path)
    from_har = _load(str(tmp_path / "s.har"))
    from_jsonl = _load(str(tmp_path / "s.jsonl"))
    assert len(from_har["log"]["entries"]) == len(from_jsonl["log"]["entries"]) == 2


def test_write_events(tmp_path):
    _session(tmp_path)
    har = _load(str(tmp_path / "s.har"))
    out = tmp_path / "events.jsonl"
    _write_events(har, str(out))
    events = [json.loads(line) for line in out.read_text().splitlines()]
    kinds = [e["event"] for e in events]
    assert kinds[0] == "session"
    assert "keystroke" in kinds and "aid" in kinds and "screen" in kinds


def test_write_text_includes_screens(tmp_path):
    _session(tmp_path)
    har = _load(str(tmp_path / "s.har"))
    out = tmp_path / "out.txt"
    _write_text(har, str(out))
    text = out.read_text()
    assert "AID=Connect" in text
    assert "WELCOME" in text  # the actual screen content is rendered
    assert "DATA SET LIST" in text
    assert "typed: 3.4" in text
