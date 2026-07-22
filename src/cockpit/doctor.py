"""`cockpit doctor` — what works, what doesn't, and exactly how to fix it.

macOS permissions are the least debuggable part of this project, because every
failure mode looks the same from the outside: a board that is simply empty, or
a key that quietly does nothing. Worse, **the answer depends on which process
asks.** macOS grants automation and Accessibility per *responsible process*, so
running a probe from your terminal tells you what Terminal is allowed to do —
not what the LaunchAgent is allowed to do. Those are different, and the
difference has bitten this project more than once.

So this module runs the same probes in two places and reports both:

  - **locally**, in whatever process you invoked it from (usually Terminal), and
  - **inside the daemon**, over its `/doctor` endpoint, which is the only way to
    learn what the always-on process can actually do.

Every failing check carries the concrete fix, because "grant Accessibility" is
not an instruction anyone can follow at 11pm six months from now.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import urllib.request
from dataclasses import dataclass
from typing import Optional

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SETTINGS = os.path.expanduser("~/.claude/settings.json")
HEARTBEAT = os.path.expanduser("~/Library/Logs/cockpit.heartbeat")
DEFAULT_PORT = 8787

OK, WARN, BAD, INFO = "ok", "warn", "bad", "info"


@dataclass
class Check:
    name: str
    status: str
    detail: str = ""
    fix: str = ""


def _osascript(script: str, timeout: float = 8.0):
    try:
        r = subprocess.run(["osascript", "-e", script],
                           capture_output=True, text=True, timeout=timeout)
    except (subprocess.SubprocessError, OSError) as e:
        return None, str(e)
    return (r.stdout.strip(), "") if r.returncode == 0 else (None, r.stderr.strip())


def responsible_binary() -> str:
    """The executable macOS actually attributes permissions to.

    A venv's `bin/python` is a symlink; TCC follows it to the real interpreter,
    which is what you must add in System Settings. Adding the symlink appears to
    work and then silently doesn't.
    """
    return os.path.realpath(sys.executable)


# --- the probes, each usable from any process --------------------------------

def check_automation_terminal() -> Check:
    out, err = _osascript('tell application "Terminal" to return count of windows')
    if out is None:
        return Check("Automation → Terminal", BAD, err[:90],
                     "Approve the prompt, or System Settings → Privacy & Security "
                     "→ Automation → enable Terminal for this process.")
    return Check("Automation → Terminal", OK, f"{out} window(s) visible")


def check_automation_sysevents() -> Check:
    out, err = _osascript('tell application "System Events" to '
                          'return name of first application process whose frontmost is true')
    if out is None:
        return Check("Automation → System Events", WARN, err[:90],
                     "Focus-based recency degrades without it; the board still works. "
                     "System Settings → Privacy & Security → Automation → System Events.")
    return Check("Automation → System Events", OK, f"frontmost: {out}")


def check_accessibility() -> Check:
    """Reading another app's window title is the cheapest true Accessibility probe.

    `UI elements enabled` is NOT a reliable substitute — it can report true from
    a process that still cannot read a title. This is required for Stage 3
    keystroke synthesis (accept/reject); nothing before that needs it.
    """
    out, err = _osascript('tell application "System Events" to return title of '
                          'front window of (first application process whose frontmost is true)')
    if out is None:
        return Check("Accessibility (keystrokes)", WARN,
                     (err or "denied")[:90],
                     f"Needed only for accept/reject. System Settings → Privacy & "
                     f"Security → Accessibility → + → add:\n      {responsible_binary()}")
    return Check("Accessibility (keystrokes)", OK, "window titles readable")


def check_device() -> Check:
    try:
        from StreamDeck.DeviceManager import DeviceManager
        decks = DeviceManager().enumerate()
    except Exception as e:
        return Check("Stream Deck device", BAD, str(e)[:90],
                     "brew install hidapi, then pip install -r requirements.txt")
    if not decks:
        return Check("Stream Deck device", BAD, "none found",
                     "Check USB. Quit the Elgato app if it's running — two "
                     "writers fight over the display.")
    return Check("Stream Deck device", OK, f"{len(decks)} found")


def check_settings() -> list[Check]:
    try:
        with open(SETTINGS) as f:
            cfg = json.load(f)
    except (OSError, ValueError) as e:
        return [Check("Claude Code settings", BAD, str(e)[:90],
                      f"Expected {SETTINGS}")]
    out = []

    sl = cfg.get("statusLine") or {}
    if "cockpit.statusline" not in (sl.get("command") or ""):
        out.append(Check("statusline → cockpit", BAD, "not wired",
                         "Without it, hook events cannot be joined to a window "
                         "at all — it is the only channel that reports a tty."))
    elif not sl.get("refreshInterval"):
        out.append(Check("statusline → cockpit", WARN, "no refreshInterval",
                         "An idle session never re-runs its statusline, so its "
                         "tty never registers. Set refreshInterval: 30."))
    else:
        out.append(Check("statusline → cockpit", OK,
                         f"refreshInterval {sl['refreshInterval']}s"))

    hooks = cfg.get("hooks") or {}
    wanted = {"Notification", "Stop", "UserPromptSubmit", "PermissionRequest"}
    missing = sorted(wanted - set(hooks))
    if missing:
        out.append(Check("hooks → cockpit", WARN, f"missing: {', '.join(missing)}",
                         "Without PermissionRequest a tool approval cannot be "
                         "told apart from a question."))
    else:
        out.append(Check("hooks → cockpit", OK, f"{len(hooks)} event(s) wired"))
    return out


def check_daemon(port: int = DEFAULT_PORT) -> list[Check]:
    """Ask the daemon what *it* can do — the answer that actually matters."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/doctor", timeout=3) as r:
            data = json.loads(r.read())
    except Exception as e:
        return [Check("daemon channels", BAD, f"unreachable on :{port} ({type(e).__name__})",
                      "cockpit status / cockpit start. Until it answers, hooks "
                      "and the statusline have nowhere to report.")]
    out = [Check("daemon channels", OK, f"listening on :{port}, "
                 f"{data.get('sessions', 0)} session(s) registered")]
    for c in data.get("checks", []):
        out.append(Check(f"daemon: {c['name']}", c["status"], c.get("detail", ""),
                         c.get("fix", "")))
    return out


def daemon_self_checks() -> list[dict]:
    """Run inside the daemon, served over /doctor. Same probes, different process."""
    return [vars(c) for c in (check_automation_terminal(),
                              check_automation_sysevents(),
                              check_accessibility())]


# --- report ------------------------------------------------------------------

GLYPH = {OK: "✓", WARN: "!", BAD: "✗", INFO: "·"}


def run(port: int = DEFAULT_PORT) -> list[Check]:
    checks = [Check("python (TCC identity)", INFO, responsible_binary())]
    checks.append(check_device())
    checks += check_settings()
    checks.append(Check("— from this terminal —", INFO))
    checks += [check_automation_terminal(), check_automation_sysevents(),
               check_accessibility()]
    checks.append(Check("— from the daemon —", INFO))
    checks += check_daemon(port)
    return checks


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    port = int(argv[0]) if argv and argv[0].isdigit() else DEFAULT_PORT

    checks = run(port)
    width = max(len(c.name) for c in checks) + 2
    print()
    for c in checks:
        if c.status == INFO and not c.detail:
            print(f"\n  {c.name}")
            continue
        print(f"  {GLYPH.get(c.status, '?')} {c.name.ljust(width)} {c.detail}")
        if c.fix and c.status in (WARN, BAD):
            for line in c.fix.splitlines():
                print(f"      {line}")
    bad = sum(1 for c in checks if c.status == BAD)
    warn = sum(1 for c in checks if c.status == WARN)
    print(f"\n  {bad} problem(s), {warn} warning(s)\n")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
