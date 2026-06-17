"""Tests for CLI helpers (host label + connect-string building)."""

from __future__ import annotations

from py3270cap.cli import _build_connect_str, _host_label


def test_host_label():
    assert _host_label("mainframe") == "mainframe"
    assert _host_label("host:23") == "host"
    assert _host_label("L:host:992") == "host"
    assert _host_label("B:x:23") == "x"
    assert _host_label("London") == "London"  # leading 'L' is not an 'L:' prefix


def test_build_connect_str():
    assert _build_connect_str("mainframe", None, False) == "mainframe"
    assert _build_connect_str("host", 23, False) == "host:23"
    assert _build_connect_str("mainframe", 992, True) == "L:mainframe:992"
    # an explicit s3270 host string is passed through untouched
    assert _build_connect_str("L:host:992", None, False) == "L:host:992"
