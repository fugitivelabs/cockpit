"""OS introspection — what the machine has in focus right now.

Agent-agnostic on purpose. This is the shared OS layer that *every* agent-CLI
adapter (Claude Code today; Codex, Copilot soon) sits on top of — it knows
nothing about Claude, sessions, or terminals, it just reports what macOS has
frontmost. It is deliberately NOT in `deck/` (which stays device-only) and NOT
in any one adapter (focus is common to all of them).

Why focus matters enough to be a primitive now:
  - Stage 1 disambiguation — which of several look-alike sessions is the one
    you're actually looking at.
  - Stage 3 focus guard — before the deck sends a keystroke to "the blocked
    session", confirm that session is really frontmost. Acting on the wrong
    window is the one genuinely dangerous failure, and it's a focus check.

Permissions note (macOS TCC): reading the frontmost *app identity* (name,
bundle id, pid) needs Automation permission for System Events. Reading another
app's *window title* additionally needs Accessibility, so `window_title` is
best-effort and may be "" even when the rest is populated. A LaunchAgent may
need these granted once; when denied, frontmost() degrades to None rather than
raising.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger("fleet.osint")

# One System Events round-trip. Window title is attempted but never fatal.
# Note: AppleScript, not Python — fields are joined with the `tab` keyword (not
# "\t", which AppleScript does not interpret), and the return is one line (its
# continuation char is ¬, not backslash). Getting either wrong makes the whole
# script fail to compile and frontmost() go None — which is exactly how the
# first cut broke, caught only by running it live.
_FRONTMOST = '''
tell application "System Events"
  set p to first application process whose frontmost is true
  set t to ""
  try
    set t to title of front window of p
  end try
  return (name of p) & tab & (bundle identifier of p) & tab & (unix id of p as text) & tab & t
end tell
'''


@dataclass(frozen=True)
class Focus:
    """The app/window in focus. A value object — cheap to compare tick to tick."""

    app: str            # frontmost application name, e.g. "Terminal"
    bundle_id: str      # e.g. "com.apple.Terminal"
    pid: int            # process id of the frontmost app
    window_title: str   # title of its front window ("" if none / not permitted)


def parse_focus(raw: str) -> Optional[Focus]:
    """Parse the tab-joined osascript reply. Pure — the testable half."""
    line = raw.strip("\n")
    if not line:
        return None
    parts = line.split("\t")
    if len(parts) < 4:
        return None
    name, bundle, pid_s = parts[0], parts[1], parts[2]
    title = "\t".join(parts[3:])  # titles could in theory contain a tab
    try:
        pid = int(pid_s)
    except ValueError:
        return None
    if not name:
        return None
    return Focus(app=name, bundle_id=bundle, pid=pid, window_title=title)


def frontmost(timeout: float = 3.0) -> Optional[Focus]:
    """The app/window currently in focus, or None if it can't be read.

    Never raises: a missing osascript, a TCC denial, or a timeout all return
    None, so a caller can poll this every tick without guarding each call.
    """
    try:
        r = subprocess.run(["osascript", "-e", _FRONTMOST],
                           capture_output=True, text=True, timeout=timeout)
    except (subprocess.SubprocessError, OSError):
        return None
    if r.returncode != 0:
        return None
    return parse_focus(r.stdout)


def keystroke(keys: str, timeout: float = 5.0) -> bool:
    """Send literal keystrokes to whatever is frontmost. The dangerous primitive.

    There is no IPC into a running Claude Code session, so answering a prompt
    means synthesizing a keypress into the focused terminal — and this function
    has **no idea what is focused**. It types into whatever is in front. Calling
    it without first verifying the target is how you approve a tool call in a
    session you weren't looking at.

    So it is deliberately not exported for casual use: the only caller is the
    permission bar in `actions.py`, which re-verifies focus immediately before
    calling and refuses if anything moved. Requires Accessibility (see
    ../../../docs/operations.md); `cockpit doctor` reports whether the daemon has it.
    """
    # `keystroke` sends the characters as typed input. Quotes are escaped
    # because the string is interpolated into AppleScript source.
    # Escape sends as a key code, not a character: `keystroke "\x1b"` is not a
    # thing System Events understands.
    if keys == "\x1b":
        script = 'tell application "System Events" to key code 53'
    else:
        safe = keys.replace("\\", "\\\\").replace('"', '\\"')
        script = f'tell application "System Events" to keystroke "{safe}"'
    try:
        r = subprocess.run(["osascript", "-e", script],
                           capture_output=True, text=True, timeout=timeout)
    except (subprocess.SubprocessError, OSError) as e:
        log.warning("keystroke %r failed to run: %s", keys, e)
        return False
    if r.returncode != 0:
        # Silence here is how a dead accept key looks like a working one.
        log.warning("keystroke %r rejected (rc=%d): %s",
                    keys, r.returncode, r.stderr.strip()[:160])
        return False
    return True


def activate(bundle_id: str, timeout: float = 5.0) -> bool:
    """Bring an app to the front by bundle id (activate, launching if needed).

    The safe half of "app switching". A control surface wants a *direct* jump —
    "focus the terminal with the blocked session" — not blind Cmd-Tab cycling,
    so activate-by-identity is the primitive, not a synthesized Cmd-Tab. Uses
    LaunchServices via `open -b`, so it needs no Automation permission.

    Window-level focus (which Terminal window) is adapter-specific and lives
    with the adapter that knows that app's scripting model. Keystroke synthesis
    — the accept/reject actions — is the *dangerous* direction and is gated
    behind the Stage-3 focus guard (confirm frontmost() is the target first),
    so it is deliberately not exposed here yet.
    """
    try:
        r = subprocess.run(["open", "-b", bundle_id],
                           capture_output=True, timeout=timeout)
        return r.returncode == 0
    except (subprocess.SubprocessError, OSError):
        return False
