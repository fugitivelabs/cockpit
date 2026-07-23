"""Wiring Claude Code to the cockpit — reproducibly, on any machine.

The channels only exist because `~/.claude/settings.json` points at this daemon:
a statusline command that reports each session's tty, and a set of hooks that
POST state changes. That wiring was originally done by hand, which meant a new
machine — or a restored home directory, or a moved repo — silently produced a
dashboard with no hook state and no telemetry, and nothing to explain why.

So the wiring lives here as data, and `cockpit wire` applies it:

  - **Paths are computed, never hardcoded.** The statusline command is built
    from this checkout's own location and interpreter, so moving the repo and
    re-running is the whole migration story.
  - **Idempotent.** Applying twice changes nothing; `wire` after an edit
    reconciles rather than duplicating.
  - **Reversible.** `cockpit unwire` removes exactly what we added and leaves
    everything else — the file is the user's, and we are a guest in it.
  - **Backed up** before the first modification.

**One source of truth.** The endpoint paths come from `listener.HOOK_PATHS`, so
a hook can never be configured to call a URL the daemon doesn't serve. That
mattered the moment there were five of them.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
from typing import Optional

from .listener import DEFAULT_PORT

SETTINGS = os.path.expanduser("~/.claude/settings.json")
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Statusline cadence. Without this an idle session never re-runs its statusline,
# so its tty never registers and hook events for it have nowhere to land — the
# join simply doesn't happen. Learned live; do not drop it.
REFRESH_INTERVAL_S = 30

# (event, matcher, endpoint). Matcher None means the event takes none.
#
# The two clearing edges on PostToolUse/PermissionDenied are what make a
# `blocked` flag mean "a prompt is on screen right now" rather than "one was,
# up to 30 minutes ago" — they fire the instant a prompt is answered, where
# `Stop` only fires at the end of the whole turn.
#
# PostToolUse is deliberately scoped rather than unmatched: unmatched, it fires
# for every Read and Grep in every session, which is a lot of traffic to clear a
# flag that only prompting tools can set.
PROMPTING_TOOLS = "Bash|Write|Edit|NotebookEdit|WebFetch|WebSearch"

WIRING = (
    # Notification: fires for BOTH a question and a tool approval, so it only
    # ever raises the weaker "needs input". PermissionRequest upgrades it.
    ("Notification", "permission_prompt", "/hook/needs-input"),
    ("Notification", "agent_needs_input", "/hook/needs-input"),
    ("Notification", "elicitation_dialog", "/hook/needs-input"),
    ("Notification", "idle_prompt", "/hook/idle"),
    # Fires ONLY for tool approvals -> the stronger claim, wins by precedence.
    ("PermissionRequest", None, "/hook/blocked"),
    # Clearing edges.
    ("PostToolUse", PROMPTING_TOOLS, "/hook/tool-done"),
    ("PostToolUse", "mcp__.*", "/hook/tool-done"),
    ("PermissionDenied", None, "/hook/tool-done"),
    ("Stop", None, "/hook/stop"),
    ("UserPromptSubmit", None, "/hook/active"),
)

# Diagnostic only: logs the payload, touches no state. Off by default because
# it is for answering "what does Claude Code actually send?", not for running.
CAPTURE_WIRING = (
    ("PreToolUse", "AskUserQuestion", "/hook/capture"),
)

HOOK_TIMEOUT_S = 5


def _url(path: str, port: int) -> str:
    return f"http://127.0.0.1:{port}{path}"


def statusline_command(python: Optional[str] = None, repo: str = REPO) -> str:
    """The statusline command line for *this* checkout and interpreter."""
    return f"PYTHONPATH={repo} {python or sys.executable} -m cockpit.statusline"


def desired(port: int = DEFAULT_PORT, capture: bool = False,
            python: Optional[str] = None, repo: str = REPO) -> dict:
    """The settings fragment we want to own. Everything else is left alone."""
    hooks: dict = {}
    for event, matcher, path in WIRING + (CAPTURE_WIRING if capture else ()):
        entry = {"hooks": [{"type": "http", "url": _url(path, port),
                            "timeout": HOOK_TIMEOUT_S}]}
        if matcher is not None:
            entry["matcher"] = matcher
        hooks.setdefault(event, []).append(entry)
    return {
        "statusLine": {
            "type": "command",
            "command": statusline_command(python, repo),
            "refreshInterval": REFRESH_INTERVAL_S,
        },
        "hooks": hooks,
    }


def _is_ours(entry: dict, port: int) -> bool:
    """Does this hook entry point at our daemon? Only ours may be removed."""
    for h in entry.get("hooks", []):
        url = h.get("url") or ""
        if url.startswith(f"http://127.0.0.1:{port}/hook/"):
            return True
    return False


def load(path: str = SETTINGS) -> dict:
    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def merge(current: dict, port: int = DEFAULT_PORT, capture: bool = False,
          python: Optional[str] = None, repo: str = REPO) -> dict:
    """Current settings + our wiring, replacing any previous wiring of ours.

    Pure, so the interesting half is testable without touching a real file.
    Foreign hooks on the same events survive: someone else's `Stop` hook is
    none of our business, and clobbering it would be a nasty surprise.
    """
    out = json.loads(json.dumps(current))       # deep copy, cheap at this size
    want = desired(port, capture, python, repo)

    # Claude Code supports exactly one statusLine, so wiring ours necessarily
    # replaces any existing one — there is no merge to do. That is destructive
    # in a way the hooks are not (hooks are a list; we add to it and leave
    # foreign entries alone), so `apply` warns and the file backup is the
    # recovery path. `unwire` cannot put a foreign statusline back, because by
    # then we no longer know what it was.
    out["statusLine"] = want["statusLine"]

    hooks = out.get("hooks")
    if not isinstance(hooks, dict):
        hooks = {}
    for event in list(hooks):
        kept = [e for e in hooks.get(event, [])
                if isinstance(e, dict) and not _is_ours(e, port)]
        if kept:
            hooks[event] = kept
        else:
            hooks.pop(event, None)
    for event, entries in want["hooks"].items():
        hooks.setdefault(event, []).extend(entries)
    out["hooks"] = hooks
    return out


def strip(current: dict, port: int = DEFAULT_PORT) -> dict:
    """Current settings minus our wiring. The exact inverse of merge()."""
    out = json.loads(json.dumps(current))
    sl = out.get("statusLine") or {}
    if "cockpit.statusline" in (sl.get("command") or ""):
        out.pop("statusLine", None)
    hooks = out.get("hooks")
    if isinstance(hooks, dict):
        for event in list(hooks):
            kept = [e for e in hooks.get(event, [])
                    if isinstance(e, dict) and not _is_ours(e, port)]
            if kept:
                hooks[event] = kept
            else:
                hooks.pop(event, None)
        if hooks:
            out["hooks"] = hooks
        else:
            out.pop("hooks", None)
    return out


def backup(path: str = SETTINGS) -> Optional[str]:
    if not os.path.exists(path):
        return None
    dest = f"{path}.bak-{time.strftime('%Y%m%d-%H%M%S')}"
    shutil.copy2(path, dest)
    return dest


def write(data: dict, path: str = SETTINGS) -> None:
    """Atomic: a crash mid-write must not leave Claude Code with broken JSON."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def replaced_statusline(current: dict) -> Optional[str]:
    """A foreign statusLine we are about to overwrite, if any."""
    cmd = (current.get("statusLine") or {}).get("command") or ""
    return cmd if (cmd and "cockpit.statusline" not in cmd) else None


def apply(port: int = DEFAULT_PORT, capture: bool = False,
          path: str = SETTINGS, python: Optional[str] = None,
          repo: str = REPO) -> tuple:
    """Wire it up. Returns (changed, backup_path_or_None, replaced_or_None)."""
    current = load(path)
    clobbered = replaced_statusline(current)
    merged = merge(current, port, capture, python, repo)
    if merged == current:
        return False, None, None
    bak = backup(path)
    write(merged, path)
    return True, bak, clobbered


def remove(port: int = DEFAULT_PORT, path: str = SETTINGS) -> tuple:
    current = load(path)
    stripped = strip(current, port)
    if stripped == current:
        return False, None
    bak = backup(path)
    write(stripped, path)
    return True, bak


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="cockpit wire",
                                 description="wire Claude Code to the cockpit")
    ap.add_argument("--remove", action="store_true", help="undo the wiring")
    ap.add_argument("--capture", action="store_true",
                    help="also install the diagnostic payload-capture hook")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--print", dest="show", action="store_true",
                    help="print what would be written; change nothing")
    args = ap.parse_args(argv)

    if args.show:
        print(json.dumps(desired(args.port, args.capture), indent=2))
        return 0
    if args.remove:
        changed, bak = remove(args.port)
        print("removed cockpit wiring" if changed else "no cockpit wiring present")
    else:
        changed, bak, clobbered = apply(args.port, args.capture)
        print("wiring applied" if changed else "wiring already current")
        if clobbered:
            print(f"  REPLACED your existing statusLine: {clobbered}")
            print("  (Claude Code allows only one; the backup below has it)")
        print(f"  statusline : {statusline_command()}")
        print(f"  hooks      : {len(desired(args.port, args.capture)['hooks'])} events"
              f" -> 127.0.0.1:{args.port}")
    if bak:
        print(f"  backup     : {bak}")
    if changed:
        print("  note: existing sessions pick this up automatically")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
