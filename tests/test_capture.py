"""Tests for the HAR/JSONL recorder."""

from __future__ import annotations

import json

from py3270cap.capture import Recorder, screen_obj


def _blank():
    return ["".ljust(80) for _ in range(24)]


def _make(tmp_path):
    return Recorder(
        str(tmp_path / "s.jsonl"),
        str(tmp_path / "s.har"),
        {"host": "h", "connect": "h:23", "model": "3279-2-E", "tracefile": None},
    )


def test_full_transaction_roundtrip(tmp_path):
    rec = _make(tmp_path)
    blank = _blank()
    rec.begin_transaction("Connect", {"row": 0, "col": 0}, screen_obj(blank, {"row": 0, "col": 0}))
    rec.complete_transaction(screen_obj(blank, {"row": 0, "col": 0}), [], "unlocked")

    rec.keystroke("Char", "a", 1, 1)
    rec.keystroke("Char", "•", 1, 2)  # a masked char
    rec.begin_transaction("Enter", {"row": 1, "col": 3}, screen_obj(blank, {"row": 1, "col": 3}))
    rec.complete_transaction(screen_obj(blank, {"row": 2, "col": 0}), [], "unlocked")
    rec.finalize()

    har = json.loads((tmp_path / "s.har").read_text())
    entries = har["log"]["entries"]
    assert [e["request"]["aid"] for e in entries] == ["Connect", "Enter"]
    typed = "".join(k["value"] for k in entries[1]["request"]["keystrokes"] if k["key"] == "Char")
    assert typed == "a•"
    assert har["log"]["session"]["host"] == "h"


def test_jsonl_is_written_live(tmp_path):
    rec = _make(tmp_path)
    rec.keystroke("Char", "x", 0, 0)
    rec.begin_transaction("Enter", {"row": 0, "col": 1}, screen_obj(_blank(), {"row": 0, "col": 1}))
    # before finalize, the live transcript already has session + keystroke + request
    lines = [json.loads(line) for line in (tmp_path / "s.jsonl").read_text().splitlines()]
    types = [r["type"] for r in lines]
    assert types[0] == "session"
    assert "keystroke" in types
    assert "request" in types


def test_finalize_closes_open_transaction(tmp_path):
    rec = _make(tmp_path)
    rec.begin_transaction("Enter", {"row": 0, "col": 0}, screen_obj(_blank(), {"row": 0, "col": 0}))
    # no complete_transaction -> finalize must not drop it
    rec.finalize()
    har = json.loads((tmp_path / "s.har").read_text())
    entries = har["log"]["entries"]
    assert len(entries) == 1
    assert entries[0]["response"] is None


def test_note(tmp_path):
    rec = _make(tmp_path)
    rec.note(event="viewport", term_rows=30, term_cols=100)
    rec.finalize()
    lines = [json.loads(line) for line in (tmp_path / "s.jsonl").read_text().splitlines()]
    notes = [r for r in lines if r["type"] == "note"]
    assert notes and notes[0]["term_rows"] == 30
