"""The Claude Code adapter — Terminal windows in, Sessions out.

Adapter #1, and the one that shapes the seam. Everything Claude-specific about
discovery lives here: the title format, the state glyphs, and how you navigate
to a session. `cockpit/sessions.py` holds the model; `cockpit/dashboard.py`
renders it; neither imports anything from this file except through `Adapter`.

Channel used: **window titles only**. That is a deliberate Stage-1 floor — it
needs no hooks, no statusline, no plugin install, and no Accessibility grant
(Terminal scripting is Automation-of-Terminal; see ../../docs/operations.md Permissions).
It buys `working` vs `idle` and nothing more. Stage 2's hooks add real `blocked`
state and the statusline adds telemetry; both flow into the same Session, so
this file gains fields rather than the layers above gaining special cases.

Title format Claude Code produces, as observed live:

    cwd — GLYPH task — proc — 133×45          a session with a task
    cwd — claude — 133×45                     a fresh session, no task yet
    cwd — -bash — 133×45                      not an agent session at all
"""

from __future__ import annotations

import logging
import re
import subprocess
from typing import Optional

from dataclasses import replace

from .osint import frontmost
from .registry import fuse_state
from .sessions import Session

log = logging.getLogger("deck.cockpit.claude")

AGENT = "claude"
TERMINAL_BUNDLE = "com.apple.Terminal"

# Braille spinner frames — Claude Code cycles these while a turn is in flight.
SPINNER = set("⠁⠂⠃⠄⠅⠆⠇⠈⠉⠊⠋⠌⠍⠎⠏⠐⠑⠒⠓⠔⠕⠖⠗⠘⠙⠚⠛⠜⠝⠞⠟⠠⠡⠢⠣⠤⠥⠦⠧⠨⠩⠪⠫⠬⠭⠮⠯"
              "⠰⠱⠲⠳⠴⠵⠶⠷⠸⠹⠺⠻⠼⠽⠾⠿⡀⡄⡆⡇⣀⣄⣆⣇⣠⣤⣦⣧⣰⣴⣶⣷⣸⣼⣾⣿")
IDLE_MARK = "✳"

# One round-trip for every window. Cheap enough to poll, but it is still a
# subprocess + an AppleScript bridge, so the caller runs it off the render loop.
#
# The delimiter is `character id 9`, NOT the `tab` keyword that osint.py uses.
# Inside `tell application "Terminal"`, `tab` resolves to Terminal's own *tab*
# class rather than the character constant, and silently coerces to the literal
# string "tab" — every line came back as `53025tabstreamdeck — …`. It compiles
# and returns rc 0, so nothing surfaces it but reading the bytes. A terminal
# emulator having a `tab` noun is exactly the kind of collision that only bites
# in the app you're scripting.
#
# The tty is Stage 2's join key: hook payloads carry no terminal identity, so a
# session_id only reaches a *window* by way of tty (see registry.py). Wrapped in
# a try because a window mid-close has no selected tab, and one bad window must
# not cost us the whole listing.
LIST_SCRIPT = '''
tell application "Terminal"
  set out to ""
  repeat with w in windows
    set t to ""
    try
      set t to tty of selected tab of w
    end try
    set out to out & (id of w) & (character id 9) & t & (character id 9) & (name of w) & linefeed
  end repeat
  return out
end tell
'''


def _has_state_glyph(part: str) -> bool:
    """A field carrying a spinner or the idle mark is a task, never a proc."""
    return IDLE_MARK in part or any(c in SPINNER for c in part)


def _is_proc_field(part: str) -> bool:
    """Does this em-dash-delimited field name the running process?

    Terminal's proc field is the process name, sometimes with a wrapper chain
    ("caffeinate ◂ claude"). Two guards, because "the field that says claude"
    is not on its own enough — a task can say it too ("upgrade claude config"):

      - match `claude` as a whole word, not a substring (so `claudette` isn't
        a session), and
      - reject any field carrying a state glyph, which marks it as the task.

    The caller adds the third guard, taking the *rightmost* match.
    """
    if _has_state_glyph(part):
        return False
    return bool(re.search(r"(?<![\w-])claude(?![\w-])", part))


def parse_title(window_id: int, title: str, tty: str = "") -> Optional[Session]:
    """One Terminal title -> a Session, or None if it isn't an agent session.

    Returning None for a plain shell is a feature: the dashboard is a *session*
    dashboard, and a `-bash` window on it is noise competing for eight keys.
    """
    parts = [p.strip() for p in title.split("—")]
    if len(parts) < 2:
        return None

    # Rightmost match: the proc field sits at the end of the title (before the
    # optional dimensions), and the task field sits at the front, so scanning
    # from the right can't mistake one for the other.
    proc_i = next((i for i in range(len(parts) - 1, 0, -1)
                   if _is_proc_field(parts[i])), None)
    if proc_i is None:
        return None

    cwd = parts[0] or "?"

    # The task occupies the field between cwd and proc. A fresh session has no
    # such field (proc sits directly after cwd), which is a task of "", not a
    # parse failure.
    raw_task = parts[1] if proc_i > 1 else ""

    state = "idle"
    if any(c in SPINNER for c in raw_task):
        state = "working"
    elif IDLE_MARK in raw_task:
        state = "idle"

    # Strip the leading state glyph and any punctuation it drags with it.
    task = re.sub(r"^[\W_]+", "", raw_task).strip()

    return Session(
        id=f"{AGENT}:{window_id}",
        agent=AGENT,
        cwd=cwd,
        task=task,
        state=state,
        handle=str(window_id),
        tty=tty or None,
        title=title,
    )


def front_window_id(raw: str) -> Optional[str]:
    """The frontmost Terminal window from a raw listing, session or not.

    Read before filtering, deliberately. Terminal enumerates front-to-back, so
    the first *line* is the front window — but the first parsed *session* is
    not, whenever the window you're actually looking at is a plain shell. Using
    the latter silently credits focus to whatever session happens to sit behind
    your bash window, repeatedly, which quietly pins it to the top of the board.
    """
    for line in raw.strip().splitlines():
        wid = line.split("\t", 1)[0].strip()
        if wid.isdigit():
            return wid
    return None


def parse_listing(raw: str) -> list[Session]:
    """The whole AppleScript reply -> Sessions. Pure; the testable half.

    Each line is `window_id TAB tty TAB title`. The tty may be empty (a window
    mid-close), which costs that session its hook state but not its tile.
    """
    out = []
    for line in raw.strip().splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        wid_s, tty, title = parts[0], parts[1], "\t".join(parts[2:])
        try:
            wid = int(wid_s.strip())
        except ValueError:
            continue
        s = parse_title(wid, title, tty=tty.strip())
        if s is not None:
            out.append(s)
    return out


class ClaudeCodeAdapter:
    """Discovers Claude Code sessions from Terminal, and navigates to them."""

    name = AGENT

    def __init__(self, timeout: float = 5.0, registry=None):
        self._timeout = timeout
        self._registry = registry
        self._front_window: Optional[str] = None
        self._warned_no_focus = False
        self._logged_focus_ok = False

    def sessions(self) -> list[Session]:
        """Every Claude Code session Terminal knows about. Never raises.

        A failure here means an empty dashboard for one poll, which the caller
        renders as "no sessions" — the daemon must not die because osascript
        hiccuped or Terminal was mid-quit.
        """
        try:
            r = subprocess.run(["osascript", "-e", LIST_SCRIPT],
                               capture_output=True, text=True, timeout=self._timeout)
        except (subprocess.SubprocessError, OSError) as e:
            log.warning("session enumeration failed: %s", e)
            return []
        if r.returncode != 0:
            log.warning("session enumeration returned %d: %s",
                        r.returncode, r.stderr.strip()[:200])
            return []
        self._front_window = front_window_id(r.stdout)
        return self._fuse(parse_listing(r.stdout))

    def _fuse(self, sessions: list[Session]) -> list[Session]:
        """Attach hook/statusline state to each window, joined on tty.

        Stage 1 sessions pass through untouched when no registry is wired, so
        the dashboard degrades to title-only rather than breaking — which is
        also exactly what happens for a session whose statusline hasn't
        reported in yet.
        """
        if self._registry is None:
            return sessions
        joined = self._registry.by_tty()
        if not joined:
            return sessions
        out = []
        for s in sessions:
            rec = joined.get(s.tty) if s.tty else None
            if rec is None:
                out.append(s)
                continue
            out.append(replace(
                s,
                state=fuse_state(s.state, rec.flag),
                telemetry=rec.telemetry or s.telemetry,
                session_id=rec.session_id,
                model=rec.model or s.model,
            ))
        return out

    def read_prompt(self, session: Session):
        """The menu on screen in that session's window, or None.

        Scoped by window title so a read can never return another session's
        screen — see axread.py. Returns None when Accessibility is unavailable,
        which simply means the deck offers no answer keys.
        """
        from .axread import read_prompt as _read
        front = frontmost()
        if front is None or front.bundle_id != TERMINAL_BUNDLE:
            return None
        # Prove which window this is with a stable id, freshly — not with the
        # cached title (which mutates with the spinner) and not with the last
        # poll's value (which can be two seconds stale).
        if self.front_window_now() != session.handle:
            return None
        return _read(front.pid)

    def front_window_now(self) -> Optional[str]:
        """Terminal's frontmost window id, read now. The press-time ground truth."""
        try:
            r = subprocess.run(
                ["osascript", "-e",
                 'tell application "Terminal" to return id of window 1 as text'],
                capture_output=True, text=True, timeout=self._timeout)
        except (subprocess.SubprocessError, OSError):
            return None
        wid = r.stdout.strip()
        return wid if (r.returncode == 0 and wid.isdigit()) else None

    def focused(self, sessions) -> Optional[str]:
        """The handle of the session you're actually looking at, or None.

        Three conditions, all necessary. Terminal must be the frontmost app (a
        front Terminal window means nothing while you're in Firefox); we take
        the front window recorded from the raw listing, not the first parsed
        session; and that window must itself be a session — if you're looking
        at a plain shell, no session has focus and the honest answer is None.

        Best-effort by design. `frontmost()` needs an Automation grant for
        System Events that the window list doesn't, and a LaunchAgent can't
        prompt for one; on denial this returns None and the board simply falls
        back to transition-based recency (see attention.py).
        """
        if not sessions:
            return None
        front = frontmost()
        if front is None:
            # Log once, not every poll: a permanent None means the TCC grant is
            # missing, and that is the difference between "recency tracks the
            # window you're in" and "recency only tracks what spins". Silent
            # degradation here would be indistinguishable from a sorting bug.
            if not self._warned_no_focus:
                self._warned_no_focus = True
                log.warning("frontmost() unavailable — focus-based recency is off "
                            "(grant Automation → System Events to this binary)")
            return None
        if not self._logged_focus_ok:
            self._logged_focus_ok = True
            log.info("focus detection available (frontmost app: %s)", front.app)
        if front.bundle_id != TERMINAL_BUNDLE:
            return None
        wid = self._front_window
        if wid is None:
            return None
        return wid if any(s.handle == wid for s in sessions) else None

    def focus(self, session: Session) -> bool:
        """Raise that window and bring Terminal forward. The Stage-1 action.

        Window-level only. Every observed session is one tab in its own window
        (see ../../docs/design.md), so tab selection buys nothing yet; when it does, it
        belongs right here — the layers above just say "go to this session".
        """
        try:
            wid = int(session.handle)
        except (TypeError, ValueError):
            return False
        # **Order matters, and getting it wrong sends you to the wrong session.**
        # Reordering a background app's windows does not stick: `set index`
        # while Terminal is behind Firefox is discarded, and the subsequent
        # `activate` then restores whatever window Terminal last had in front.
        # Pressing "streamdeck" from Firefox reliably surfaced a different
        # session. Activating first makes the reorder land on a foreground app,
        # where it holds. Verified from Firefox, both orders, 2026-07-22.
        script = (f'tell application "Terminal"\n'
                  f'  activate\n'
                  f'  set index of window id {wid} to 1\n'
                  f'end tell')
        try:
            r = subprocess.run(["osascript", "-e", script],
                               capture_output=True, text=True, timeout=self._timeout)
        except (subprocess.SubprocessError, OSError) as e:
            log.warning("focus %s failed: %s", session.id, e)
            return False
        if r.returncode != 0:
            log.warning("focus %s returned %d: %s",
                        session.id, r.returncode, r.stderr.strip()[:200])
            return False
        return True
