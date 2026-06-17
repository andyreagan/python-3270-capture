# py3270cap — a `.har` for the mainframe

A capturing TN3270 terminal emulator. Run it like `c3270`, drive your mainframe
apps exactly as you do today, and every keystroke and every screen — the full
back-and-forth — is recorded to a structured, HAR-style transcript. Built for
**migrating mainframe apps**: capture the real sessions, then analyze or replay them.

```
uv run py3270 mainframe
```

## How it works (and the one tradeoff)

py3270cap does **not** re-implement the 3270 protocol. It drives **`s3270`** (the
scripting engine from the same x3270 suite as your `c3270`) as the protocol engine,
and layers an interactive curses UI + a capture recorder on top.

Why wrap s3270 instead of a pure-Python stack? Correctness. s3270 already handles
TN3270**E**, structured fields, extended/SFE attributes, TLS, and every datastream
edge case that a from-scratch parser gets wrong on first contact with a real host.
For migration capture, fidelity of the captured screens matters most.

**What you give up by wrapping:** a dependency on the `s3270` binary, and host-response
timestamps accurate to a few ms (we observe s3270's state rather than the wire packet).
**What you keep:** every keystroke (you type into *our* UI), every decoded screen with
field structure, **and** the raw 3270 datastream — s3270's trace is captured to a
sidecar `.trace` file and referenced in the HAR, so wire-level bytes are preserved too.

Requires `s3270` on PATH — macOS: `brew install x3270`.

**Terminal size:** the emulator needs at least **one more row than the 3270 model** —
e.g. **25 rows × 80 cols** for the default 24×80 model (the extra line is the status/OIA
bar, just like a real terminal). If your window is too small it shows a resize prompt
instead of silently clipping the bottom of the screen. macOS Terminal's default is 24
rows, so make the window one row taller.

## Ways to run it

`uv run` syncs the environment for you — there is no separate `uv sync` step. Pick whichever fits:

| Command | When | Notes |
|---|---|---|
| `uv run py3270 mainframe` | from the project dir | one step; auto-syncs each time (cached, instant) |
| `uvx --from /path/to/3270-capture py3270 mainframe` | from anywhere | no project venv to manage |
| `uv tool install /path/to/3270-capture` → then `py3270 mainframe` | install once | puts `py3270` + `py3270-convert` on your PATH globally — closest to a plain command; re-install or `uv tool upgrade py3270cap` to pick up changes |

All three expose both entry points (`py3270` and `py3270-convert`).

## Usage

```
uv run py3270 mainframe                  # plain TN3270, port 23
uv run py3270 --tls --port 992 host      # TLS
uv run py3270 'L:host:992'               # raw s3270 host string (prefix preserved)
uv run py3270 --model 3279-4-E host      # 43x80; default is 3279-2-E (24x80 color)
uv run py3270 --capture-dir ~/captures host
uv run py3270 --trace host               # also write the raw datastream trace
uv run py3270 --capture-secrets host     # record password-field keystrokes in plaintext
```

### Keys

| Key | Action |
|-----|--------|
| Enter | submit (AID Enter) |
| Tab / Shift-Tab | next / previous field |
| arrows, Home | move cursor |
| Backspace, Delete, Insert | field editing |
| F1–F12 | PF1–PF12 |
| Ctrl-R | Reset (unlock keyboard) |
| Ctrl-] | menu: **q**uit, **c**lear, **a**ttn, PA**1**/**2**/**3**, **p**f# (PF13–24), **h**elp |

Field editing rules (protection, numeric-only, autoskip, insert mode) are enforced
by s3270, so they behave exactly like a real terminal.

## What gets captured

Each session writes into the capture dir (default `./captures/`):

- **`NAME-TIMESTAMP.jsonl`** — durable event transcript, written live (crash-safe).
- **`NAME-TIMESTAMP.har`** — assembled HAR-style session, written on exit.
- **`NAME-TIMESTAMP.trace`** — raw s3270 3270 datastream trace. **Opt-in via `--trace`**
  (it's verbose s3270 internals; the decoded screens in the HAR/JSONL are the primary record).

## Security: credentials in captures

The point of this tool is to record everything you type — so by default it would also
record passwords. Two safeguards:

- **Screens never leak secrets.** 3270 password fields are *non-display*; s3270 returns
  them blank, so screen snapshots in the HAR/JSONL contain no password text.
- **Password keystrokes are masked by default.** Characters you type into a non-display
  field are recorded as `•` in the keystroke log (length preserved, content hidden).
  Pass `--capture-secrets` to record them in plaintext (e.g. for faithful replay).

Masking is best-effort, driven by the host's field attributes. Captures still contain
every *non-secret* keystroke and all screen text, so treat capture files as sensitive
and store them accordingly.

A **transaction** is the mainframe analog of a HAR entry: the AID you press
(Enter / PFn / PAn / Clear, plus the initial Connect) is the *request* — with the
keystrokes you typed into fields beforehand — and the screen the host paints in
response (observed when the keyboard unlocks) is the *response*.

```jsonc
{
  "log": {
    "version": "1.0",
    "creator": { "name": "py3270cap", "version": "0.1.0" },
    "session": { "host": "mainframe", "connect": "mainframe:23",
                 "model": "3279-2-E", "startedDateTime": "…", "tracefile": "….trace" },
    "entries": [
      {
        "index": 1,
        "startedDateTime": "2026-06-15T…Z",
        "time": 142,                       // ms from AID to keyboard-unlock
        "request": {
          "aid": "Enter",
          "cursor": { "row": 21, "col": 19 },
          "keystrokes": [ { "t": "…", "key": "Char", "value": "U", "row": 21, "col": 14 }, … ],
          "screenBefore": { "rows": ["…80 chars…", …], "cursor": {…} }
        },
        "response": {
          "receivedDateTime": "…",
          "keyboard": "unlocked",
          "screen": { "rows": [ … 24 rows … ], "cursor": {…} },
          "fields": [ { "row": 5, "col": 19, "protected": false, "numeric": false, … } ]
        }
      }
    ]
  }
}
```

## Converting

```
# flat JSONL event stream (keystrokes + AIDs + screens)
uv run py3270-convert captures/mainframe-….har --events out.events.jsonl

# human-readable sequential screen dumps
uv run py3270-convert captures/mainframe-….har --text out.txt

# rebuild a HAR from a live transcript after a crash
uv run py3270-convert captures/mainframe-….jsonl --har recovered.har
```

## Project layout

```
py3270cap/
  s3270.py     # s3270 subprocess driver (scripting protocol + status parsing)
  capture.py   # HAR-style recorder (live JSONL + assembled HAR)
  emulator.py  # curses UI: render screen, forward keystrokes, drive transactions
  cli.py       # `py3270` entry point
  convert.py   # `py3270-convert` entry point
```
