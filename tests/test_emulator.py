"""Tests for the emulator's hidden-field detection (drives password masking).

The emulator stores the field map of the current screen; _hidden_at maps a cursor
position to its governing field and reports whether that field is non-display.
"""

from __future__ import annotations

from py3270cap.emulator import Emulator


def _emu(fields):
    # __init__ only stores its arguments, so None placeholders are fine here.
    e = Emulator(None, None, None, "host", "host")
    e.cols = 80
    e.fields = fields
    return e


def test_hidden_at_no_fields():
    assert _emu([])._hidden_at(0, 0) is False


def test_hidden_at_governing_field():
    fields = [
        {"row": 5, "col": 28, "nondisplay": False},  # userid field (visible)
        {"row": 6, "col": 28, "nondisplay": True},   # password field (hidden)
    ]
    e = _emu(fields)
    # cells after the userid attribute, before the password attribute -> visible
    assert e._hidden_at(5, 29) is False
    assert e._hidden_at(6, 27) is False
    # cells governed by the password field attribute -> hidden
    assert e._hidden_at(6, 29) is True
    assert e._hidden_at(6, 40) is True


def test_hidden_at_wraps_to_last_field():
    # A cursor before the first field is governed by the last field (buffer wraps).
    fields = [
        {"row": 10, "col": 0, "nondisplay": False},
        {"row": 20, "col": 0, "nondisplay": True},
    ]
    e = _emu(fields)
    assert e._hidden_at(0, 0) is True
