"""`py3270` entry point: parse args, set up capture files, run the emulator.

Usage mirrors c3270:
    uv run py3270 mainframe
    uv run py3270 --tls --port 992 host.example.com
    uv run py3270 'L:host:992'      # raw s3270 host string (prefix preserved)
"""

from __future__ import annotations

import argparse
import curses
import locale
import os
import sys
from datetime import datetime

from . import __version__
from .capture import Recorder
from .emulator import Emulator
from .s3270 import S3270, S3270Error

# s3270 host-string prefixes (TLS, etc.) — if present we pass the host through verbatim.
_PREFIXES = ("L:", "A:", "B:", "C:", "N:", "P:", "S:", "Y:")


def _host_label(host: str) -> str:
    """A filesystem-friendly name for the host: strip s3270 prefixes and the port."""
    h = host
    while len(h) >= 2 and h[1] == ":" and (h[:2] in _PREFIXES):
        h = h[2:]
    return h.split(":")[0] or "session"


def _build_connect_str(host: str, port: int | None, tls: bool) -> str:
    if any(host.startswith(p) for p in _PREFIXES):
        return host  # already a fully-specified s3270 host string
    s = host
    if port:
        s = f"{host}:{port}"
    if tls:
        s = "L:" + s
    return s


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="py3270",
        description="Capturing TN3270 emulator — a .har for the mainframe.",
    )
    p.add_argument("host", help="host (e.g. mainframe, host:port, or an s3270 'L:host:992' string)")
    p.add_argument(
        "--port", type=int, default=None, help="TCP port (default 23, or 992 with --tls)"
    )
    p.add_argument("--tls", action="store_true", help="connect with TLS (s3270 L: prefix)")
    p.add_argument(
        "--model", default="3279-2-E", help="terminal model (default 3279-2-E = 24x80 color)"
    )
    p.add_argument(
        "--capture-dir",
        default="captures",
        help="where to write the transcript (default ./captures)",
    )
    p.add_argument("--name", default=None, help="session name used in filenames (default: host)")
    p.add_argument(
        "--trace",
        action="store_true",
        help="also write s3270's raw datastream trace (verbose s3270 internals; off by default)",
    )
    p.add_argument(
        "--capture-secrets",
        action="store_true",
        help="record keystrokes typed into non-display (password) fields in plaintext "
        "(default: such keystrokes are masked)",
    )
    p.add_argument("--version", action="version", version=f"py3270cap {__version__}")
    args = p.parse_args(argv)

    # Required before curses.initscr so ncurses emits wide chars (box-drawing,
    # etc.) correctly — matters on Linux; harmless on macOS.
    try:
        locale.setlocale(locale.LC_ALL, "")
    except locale.Error:
        pass

    port = args.port or (992 if args.tls else None)
    connect_str = _build_connect_str(args.host, port, args.tls)
    label = args.name or _host_label(args.host)

    os.makedirs(args.capture_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    base = os.path.join(args.capture_dir, f"{label}-{stamp}")
    jsonl_path = base + ".jsonl"
    har_path = base + ".har"
    trace_path = base + ".trace" if args.trace else None

    try:
        s = S3270(model=args.model, trace_file=trace_path)
    except S3270Error as e:
        print(f"py3270: {e}", file=sys.stderr)
        return 2

    recorder = Recorder(
        jsonl_path,
        har_path,
        session={
            "host": args.host,
            "connect": connect_str,
            "model": args.model,
            "tracefile": os.path.basename(trace_path) if trace_path else None,
            "maskHidden": not args.capture_secrets,
        },
    )

    try:
        curses.wrapper(
            lambda scr: Emulator(
                scr, s, recorder, label, connect_str, mask_hidden=not args.capture_secrets
            ).run()
        )
    except S3270Error as e:
        print(f"py3270: s3270 error: {e}", file=sys.stderr)
    finally:
        recorder.finalize()
        s.close()

    print("\nSession captured:")
    print(f"  HAR        {har_path}  ({len(recorder.entries)} transactions)")
    print(f"  JSONL log  {jsonl_path}")
    if trace_path and os.path.exists(trace_path):
        print(f"  raw trace  {trace_path}")
    print("\nConvert it:")
    print(f"  uv run py3270-convert {har_path} --events {base}.events.jsonl")
    print(f"  uv run py3270-convert {har_path} --text   {base}.txt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
