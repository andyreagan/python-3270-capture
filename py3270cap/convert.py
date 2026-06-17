"""`py3270-convert` — project a captured session into other shapes.

Inputs: a `.har` file, or a live `.jsonl` transcript (e.g. from a crashed session).
Outputs (any combination):
  --har    PATH   (re)assemble a HAR from a JSONL transcript
  --events PATH   flat JSONL event stream (keystrokes + AID requests + responses)
  --text   PATH   human-readable sequential screen dumps
"""

from __future__ import annotations

import argparse
import json
import sys

from . import __version__


def _load(path: str) -> dict:
    """Return a HAR-shaped dict from either a .har or a .jsonl transcript."""
    if path.endswith(".jsonl"):
        return {"log": _har_from_jsonl(path)}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _har_from_jsonl(path: str) -> dict:
    session: dict = {}
    entries: list[dict] = []
    cur: dict | None = None
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            t = rec.get("type")
            if t == "session":
                session = {k: v for k, v in rec.items() if k != "type"}
            elif t == "request":
                cur = {
                    "index": rec["index"],
                    "startedDateTime": rec["t"],
                    "request": {
                        "aid": rec["aid"],
                        "cursor": rec.get("cursor"),
                        "keystrokes": rec.get("keystrokes", []),
                        "screenBefore": None,
                    },
                }
            elif t == "response" and cur is not None:
                cur["time"] = rec.get("time")
                cur["response"] = {
                    "receivedDateTime": rec["t"],
                    "keyboard": rec.get("keyboard"),
                    "screen": rec.get("screen"),
                    "fields": rec.get("fields", []),
                }
                entries.append(cur)
                cur = None
    if cur is not None:
        cur.setdefault("response", None)
        entries.append(cur)
    return {
        "version": "1.0",
        "creator": {"name": "py3270cap", "version": __version__},
        "session": session,
        "entries": entries,
    }


def _write_events(har: dict, path: str) -> None:
    log = har["log"]
    with open(path, "w", encoding="utf-8") as out:
        out.write(json.dumps({"event": "session", **log.get("session", {})}) + "\n")
        for e in log.get("entries", []):
            req = e.get("request", {})
            for ks in req.get("keystrokes", []):
                out.write(
                    json.dumps(
                        {
                            "event": "keystroke",
                            "index": e.get("index"),
                            "t": ks.get("t"),
                            "key": ks.get("key"),
                            "value": ks.get("value"),
                            "row": ks.get("row"),
                            "col": ks.get("col"),
                        }
                    )
                    + "\n"
                )
            out.write(
                json.dumps(
                    {
                        "event": "aid",
                        "index": e.get("index"),
                        "t": e.get("startedDateTime"),
                        "aid": req.get("aid"),
                        "cursor": req.get("cursor"),
                    }
                )
                + "\n"
            )
            resp = e.get("response")
            if resp:
                out.write(
                    json.dumps(
                        {
                            "event": "screen",
                            "index": e.get("index"),
                            "t": resp.get("receivedDateTime"),
                            "time_ms": e.get("time"),
                            "keyboard": resp.get("keyboard"),
                            "rows": (resp.get("screen") or {}).get("rows"),
                            "cursor": (resp.get("screen") or {}).get("cursor"),
                        }
                    )
                    + "\n"
                )


def _write_text(har: dict, path: str) -> None:
    log = har["log"]
    sess = log.get("session", {})
    with open(path, "w", encoding="utf-8") as out:
        out.write(f"# py3270cap session: {sess.get('host')} ({sess.get('connect')})\n")
        out.write(f"# model {sess.get('model')}  started {sess.get('startedDateTime')}\n\n")
        for e in log.get("entries", []):
            req = e.get("request", {})
            resp = e.get("response")
            out.write("=" * 80 + "\n")
            out.write(
                f"[{e.get('index')}] AID={req.get('aid')}  "
                f"sent={e.get('startedDateTime')}  rtt={e.get('time')}ms\n"
            )
            typed = [k for k in req.get("keystrokes", []) if k.get("key") == "Char"]
            if typed:
                out.write("    typed: " + "".join(k.get("value") or "" for k in typed) + "\n")
            out.write("-" * 80 + "\n")
            screen = (resp or {}).get("screen") or {}
            for row in screen.get("rows", []):
                out.write(row.rstrip() + "\n")
            out.write("\n")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="py3270-convert",
        description="Convert a py3270cap .har (or .jsonl) into other formats.",
    )
    p.add_argument("input", help="a .har file or a live .jsonl transcript")
    p.add_argument("--har", metavar="PATH", help="(re)assemble a HAR (useful after a crash)")
    p.add_argument("--events", metavar="PATH", help="flat JSONL event stream")
    p.add_argument("--text", metavar="PATH", help="plain-text sequential screen dumps")
    args = p.parse_args(argv)

    if not (args.har or args.events or args.text):
        p.error("choose at least one output: --har / --events / --text")

    har = _load(args.input)

    if args.har:
        with open(args.har, "w", encoding="utf-8") as f:
            json.dump(har, f, indent=2, ensure_ascii=False)
        print(f"wrote HAR -> {args.har}")
    if args.events:
        _write_events(har, args.events)
        print(f"wrote events -> {args.events}")
    if args.text:
        _write_text(har, args.text)
        print(f"wrote text -> {args.text}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
