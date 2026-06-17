"""Tests for the s3270 driver's pure-logic parts (no s3270 process needed)."""

from __future__ import annotations

from py3270cap.s3270 import S3270, Result, Status, _basic_attr


def test_status_parse_valid():
    st = Status.parse("U F U C(mainframe) I 2 24 80 5 12 0x0 0.001")
    assert st.keyboard == "U"
    assert st.connection == "C(mainframe)"
    assert st.rows == 24 and st.cols == 80
    assert st.cursor_row == 5 and st.cursor_col == 12
    assert st.connected is True
    assert st.locked is False


def test_status_locked_and_disconnected():
    st = Status.parse("L F U N N 2 24 80 0 0 0x0 -")
    assert st.locked is True
    assert st.connected is False


def test_status_parse_short_line_defaults():
    st = Status.parse("garbage")
    assert st.rows == 24 and st.cols == 80
    assert st.connected is False


def test_basic_attr():
    assert _basic_attr("SF(c0=0c)") == 0x0C
    assert _basic_attr("SFE(c0=e8,41=f4)") == 0xE8
    assert _basic_attr("SF(41=f4)") == 0  # no c0 component
    assert _basic_attr("SF()") == 0


class _Stub:
    """Stands in for an S3270 so we can exercise read_fields() offline."""

    def __init__(self, line: str):
        self._line = line

    def exec(self, action, timeout=30.0):
        return Result(ok=True, data=[self._line], status=Status(raw=""))


def test_read_fields_display_bits():
    # Regression test for the inverted display bits:
    #   0x0C == non-display (hidden, e.g. password)   0x08 == intensified (visible)
    # col0 non-display, col3 intensified, col6 normal, col9 protected
    line = "SF(c0=0c) 00 00 SF(c0=08) 00 00 SF(c0=00) 00 00 SF(c0=20) 00"
    fields = S3270.read_fields(_Stub(line))
    assert len(fields) == 4

    nd, hi, normal, prot = fields
    assert nd["nondisplay"] is True and nd["intensified"] is False
    assert hi["intensified"] is True and hi["nondisplay"] is False
    assert normal["nondisplay"] is False and normal["intensified"] is False
    assert prot["protected"] is True

    # column positions advance one per SF attribute + one per data cell
    assert nd["col"] == 0
    assert hi["col"] == 3
    assert normal["col"] == 6
    assert prot["col"] == 9


def test_read_fields_numeric_and_modified():
    fields = S3270.read_fields(_Stub("SF(c0=11) 00"))  # 0x10 numeric, 0x01 modified
    assert fields[0]["numeric"] is True
    assert fields[0]["modified"] is True
