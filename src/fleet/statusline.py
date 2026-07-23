"""The statusline command — renders your status line, and feeds the cockpit.

Claude Code runs this once per assistant message (plus on compact, permission
mode change, and any `refreshInterval`), handing it the session JSON on stdin.
It does two jobs:

  1. **Prints a status line** to stdout. That is its visible job and the only
     output Claude Code consumes.
  2. **POSTs the same information to the cockpit daemon**, including this
     process's tty — which is the one thing hooks cannot supply and the only
     way a `session_id` ever reaches a window (see registry.py).

**It must never slow down or break a session.** It runs in your editing loop,
so: no imports beyond the stdlib, a hard sub-second timeout on the POST, and
every failure swallowed. If the daemon is down, or the deck is unplugged, or
the port moved, this still prints a status line and exits 0. The cockpit is a
nice-to-have for the session; the session is not a nice-to-have for the cockpit.

Wired automatically by `cockpit wire`; the shape it writes is:

    "statusLine": {
      "type": "command",
      "command": "PYTHONPATH=/path/to/cockpit/src /path/to/cockpit/.venv/bin/python -m fleet.statusline"
    }
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

PORT = int(os.environ.get("COCKPIT_PORT", "8787"))
URL = f"http://127.0.0.1:{PORT}/statusline"
POST_TIMEOUT_S = 0.4          # generous for loopback, invisible in an edit loop


MAX_ANCESTRY = 8


def own_tty() -> str:
    """The terminal this session lives in, e.g. `/dev/ttys010`.

    Harder than it looks, and verified the hard way. stdin is the session JSON
    and stdout is captured, so neither is a terminal. Worse, **Claude Code
    spawns its children with no controlling terminal at all** — `ps -o tty=` on
    this process reports `??`, and so does the shell it may have come through.
    Only the `claude` process itself carries the tty.

    So we walk *up* the process tree until a real terminal appears. One `ps`
    call for the whole table, then an in-memory walk: a per-ancestor `ps` would
    multiply subprocess cost in a path that runs on every assistant message.

    Returns "" if nothing in the chain has a terminal — which costs this session
    its hook state, and nothing else.
    """
    try:
        out = subprocess.run(["ps", "-Ao", "pid=,ppid=,tty="],
                             capture_output=True, text=True, timeout=1.5)
    except (subprocess.SubprocessError, OSError):
        return ""

    table: dict[int, tuple[int, str]] = {}
    for line in out.stdout.splitlines():
        parts = line.split(None, 2)
        if len(parts) < 2:
            continue
        try:
            pid, ppid = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        table[pid] = (ppid, parts[2].strip() if len(parts) > 2 else "")

    pid = os.getpid()
    for _ in range(MAX_ANCESTRY):
        entry = table.get(pid)
        if entry is None:
            break
        ppid, tty = entry
        if tty and tty not in ("??", "-"):
            return tty if tty.startswith("/dev/") else f"/dev/{tty}"
        if ppid <= 1:
            break
        pid = ppid
    return ""


def report(payload: dict) -> None:
    """Fire-and-forget to the daemon. Never raises, never blocks for long."""
    body = {
        "session_id": payload.get("session_id"),
        "cwd": payload.get("cwd"),
        "tty": own_tty(),
        "model": payload.get("model"),
        "cost": payload.get("cost"),
        "context_window": payload.get("context_window"),
    }
    req = urllib.request.Request(
        URL, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        urllib.request.urlopen(req, timeout=POST_TIMEOUT_S).close()
    except (urllib.error.URLError, OSError, ValueError):
        pass          # daemon down is the normal case, not an error worth noise


def render(payload: dict) -> str:
    """The visible status line. Deliberately plain — the deck is the display."""
    model = (payload.get("model") or {}).get("display_name") or ""
    ctx = payload.get("context_window") or {}
    cost = payload.get("cost") or {}

    bits = []
    if model:
        bits.append(model)
    pct = ctx.get("used_percentage")
    if isinstance(pct, (int, float)):
        bits.append(f"{int(pct)}% ctx")
    usd = cost.get("total_cost_usd")
    if isinstance(usd, (int, float)) and usd > 0:
        bits.append(f"${usd:.2f}")
    return "  ·  ".join(bits)


def main() -> int:
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
        if not isinstance(payload, dict):
            payload = {}
    except (ValueError, OSError):
        payload = {}

    try:
        report(payload)
    except Exception:
        pass          # belt and braces: nothing here may reach the user's screen

    try:
        print(render(payload))
    except Exception:
        print("")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
