"""Adapter #2 — Claude Code discovered by process, not by window.

Same contract as `claude_code`, different discovery channel, and the difference
is the point. Adapter #1 asks Terminal.app what windows it has and reads the
title; this asks the OS what is *running*. Consequences:

- **Any terminal.** iTerm2, Ghostty, VS Code, tmux — a `claude` process has a
  controlling terminal wherever it was launched. Window enumeration only ever
  worked for the one app we wrote AppleScript for.
- **Better labels.** The task comes from Claude Code's own `aiTitle` in the
  transcript rather than a truncated title fragment, and `gitBranch` comes along
  free.
- **No title parsing at all.** No spinner glyph table, no em-dash splitting, no
  guessing which field is the process name — the parsing that is most exposed to
  Claude Code changing its title format.

**What it still needs from the other channels, unchanged.** A process knows its
tty but not its `session_id`, and a transcript knows a turn is in flight but not
whether it is *blocked on you* — an awaiting-approval `tool_use` and an
executing one are byte-identical (see transcript.py). So:

    procscan   -> tty, cwd, pid          which sessions exist, and where
    registry   -> session_id by tty      the statusline join, still structural
    transcript -> task, branch, working  what it is doing and what to call it
    hooks      -> blocked / waiting      the only channel that can say this

`fuse_state` combines the last two exactly as it does for adapter #1: polled
truth beats a stale hook edge.

**Navigation is deliberately still app-specific.** `handle` is the tty — a
stable, terminal-agnostic identity — and `focus()` resolves it to a window at
press time. Only Terminal.app is implemented here; iTerm2 and Ghostty are the
next resolver in the same seam, not a rewrite.
"""

from __future__ import annotations

import logging
import re
import subprocess
from typing import Optional

from .. import procscan, transcript
from ..registry import fuse_state
from ..sessions import Session

log = logging.getLogger("fleet.claude_process")

AGENT = "claude"
TERMINAL_BUNDLE = "com.apple.Terminal"

# tty -> window id, for navigation. Same shape as adapter #1's listing, minus
# the title: we no longer need it, because nothing here parses one.
TTY_WINDOW_SCRIPT = '''
tell application "Terminal"
  set out to ""
  repeat with w in windows
    set t to ""
    try
      set t to tty of selected tab of w
    end try
    set out to out & (id of w) & (character id 9) & t & linefeed
  end repeat
  return out
end tell
'''


def parse_tty_windows(raw: str) -> dict[str, str]:
    """`window_id TAB tty` lines -> {tty: window_id}. Pure.

    A window mid-close has no selected tab and so no tty; it is skipped rather
    than mapped to "", which would collide every such window onto one key.
    """
    out: dict[str, str] = {}
    for line in (raw or "").strip().splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        wid, tty = parts[0].strip(), parts[1].strip()
        if wid.isdigit() and tty:
            out[tty] = wid
    return out


def _short_cwd(path: str) -> str:
    """The last path component — what the tile shows.

    The full path is kept for the transcript lookup, but the *label* stays the
    basename so this adapter is drop-in with the existing labeling rule, which
    already disambiguates collisions on the task subtitle rather than the path.
    """
    return (path or "").rstrip("/").rsplit("/", 1)[-1] or "?"


def build_sessions(procs, joined: dict, metas: dict) -> list[Session]:
    """Compose the three channels into Sessions. Pure — the testable half.

    `joined` is `Registry.by_tty()`; `metas` is {tty: TranscriptMeta or None}.
    Both may be missing any tty, and each absence degrades one field rather than
    dropping the session: a session we can see and navigate to is worth showing
    even when we know nothing else about it yet.
    """
    out: list[Session] = []
    for p in procs:
        rec = joined.get(p.tty)
        meta = metas.get(p.tty)

        # The transcript is the only channel that observes a turn directly, so
        # it supplies the polled truth here. With no transcript we cannot tell
        # working from idle at all, and `idle` is the safe guess: it never
        # invents activity, and a hook flag can still raise the tile.
        polled = meta.state if meta is not None else "idle"
        state = fuse_state(polled, rec.flag if rec is not None else None)

        task = ""
        if meta is not None:
            task = (meta.ai_title or meta.last_prompt or "").strip()

        out.append(Session(
            # Keyed on pid, deliberately. It is unique, stable for the life of
            # the session, and — unlike session_id — available on the very first
            # poll. An id that changes once the statusline reports in would
            # reset that session's recency (attention.py keys on it) at the
            # least convenient moment.
            id=f"{AGENT}:pid:{p.pid}",
            agent=AGENT,
            cwd=_short_cwd(p.cwd),
            task=task,
            state=state,
            handle=p.tty,
            telemetry=rec.telemetry if rec is not None else None,
            tty=p.tty,
            session_id=rec.session_id if rec is not None else None,
            model=(rec.model or None) if rec is not None else None,
            title=None,          # nothing here parses a title; see module docs
        ))
    return out


class ClaudeProcessAdapter:
    """Discovers Claude Code sessions from the process table."""

    name = AGENT

    def __init__(self, timeout: float = 5.0, registry=None,
                 projects_root: str = transcript.PROJECTS_ROOT):
        self._timeout = timeout
        self._registry = registry
        self._root = projects_root
        self._windows: dict[str, str] = {}       # tty -> window id, last poll
        # See the same field on ClaudeCodeAdapter: `focused()` has to read which
        # *app* is in front in order to answer which *session* is, and that
        # broader fact is worth keeping rather than discarding.
        self._last_focus = None

    @property
    def last_focus(self):
        """The `Focus` seen at the last `focused()` call, or None. Poll-fresh."""
        return self._last_focus

    def sessions(self) -> list[Session]:
        """Every Claude Code session the OS can see. Never raises."""
        procs = procscan.scan(timeout=self._timeout)
        if not procs:
            return []

        joined = self._registry.by_tty() if self._registry is not None else {}

        metas = {}
        for p in procs:
            rec = joined.get(p.tty)
            sid = rec.session_id if rec is not None else None
            path = transcript.find(sid, cwd_hint=p.cwd, root=self._root) if sid else None
            metas[p.tty] = transcript.read(path) if path else None

        return build_sessions(procs, joined, metas)

    # --- navigation ---------------------------------------------------------

    def _tty_windows(self) -> dict[str, str]:
        """Current {tty: window id} from Terminal. Cached from the last read."""
        try:
            r = subprocess.run(["osascript", "-e", TTY_WINDOW_SCRIPT],
                               capture_output=True, text=True,
                               timeout=self._timeout)
        except (subprocess.SubprocessError, OSError) as e:
            log.warning("tty->window lookup failed: %s", e)
            return self._windows
        if r.returncode != 0:
            log.warning("tty->window lookup returned %d: %s",
                        r.returncode, (r.stderr or "").strip()[:200])
            return self._windows
        self._windows = parse_tty_windows(r.stdout)
        return self._windows

    def focus(self, session: Session) -> bool:
        """Navigate to that session's window and take focus.

        Resolves tty -> window at press time rather than trusting a poll-old
        mapping: a tab moved to another window between poll and press would
        otherwise send you somewhere else, and going to the wrong session is the
        failure this whole layer exists to avoid.

        Terminal.app only for now. A session in another terminal is discovered
        and displayed correctly and simply cannot be jumped to yet — which is
        strictly better than not seeing it at all.
        """
        wid = self._tty_windows().get(session.handle)
        if wid is None:
            log.info("no Terminal window for %s (%s) — not navigable yet",
                     session.id, session.handle)
            return False
        # Activate BEFORE reordering: `set index` on a background app is
        # discarded and the subsequent activate restores whatever window was
        # last in front. Same trap, same fix, as adapter #1.
        script = (f'tell application "Terminal"\n'
                  f'  activate\n'
                  f'  set index of window id {wid} to 1\n'
                  f'end tell')
        try:
            r = subprocess.run(["osascript", "-e", script], capture_output=True,
                               text=True, timeout=self._timeout)
        except (subprocess.SubprocessError, OSError) as e:
            log.warning("focus %s failed: %s", session.id, e)
            return False
        if r.returncode != 0:
            log.warning("focus %s returned %d: %s", session.id, r.returncode,
                        (r.stderr or "").strip()[:200])
            return False
        return True

    def _front_window_id(self) -> Optional[str]:
        """Terminal's frontmost window id, read NOW. The press-time truth."""
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
        """The handle (tty) of the session you are looking at, or None."""
        from ..macos.osint import frontmost
        front = frontmost()
        self._last_focus = front
        if front is None or front.bundle_id != TERMINAL_BUNDLE:
            return None
        wid = self._front_window_id()
        if wid is None:
            return None
        for tty, w in self._tty_windows().items():
            if w == wid:
                return tty if any(s.handle == tty for s in sessions) else None
        return None

    # --- reading the screen -------------------------------------------------
    #
    # Parity with adapter #1, and the reason it matters: without these the board
    # offers no answer keys, which makes this adapter strictly worse at the one
    # thing Stage 3 exists to do, however much better it is at discovery.
    #
    # The guard is the same one, expressed in this adapter's identity. Adapter
    # #1 proves "is this session's window in front" by comparing a window id to
    # its handle. Here the handle is a tty, so the proof is one hop longer —
    # tty -> window, then window == front — and **both halves are read fresh**.
    # A cached mapping would reintroduce exactly the bug that made every answer
    # key refuse: state that is a moment stale is indistinguishable from state
    # that is wrong, and acting on the wrong window is the one genuinely
    # dangerous failure in this project.

    def _is_front(self, session: Session) -> Optional[int]:
        """The frontmost app's pid if `session` owns the front window, else None.

        None means "could not prove it", which every caller must treat as no.
        """
        from ..macos.osint import frontmost
        front = frontmost()
        if front is None or front.bundle_id != TERMINAL_BUNDLE:
            return None
        wid = self._front_window_id()
        if wid is None:
            return None
        # Fresh tty->window, not the poll's copy: a tab dragged to another
        # window between poll and press would otherwise resolve to the old one.
        if self._tty_windows().get(session.handle) != wid:
            return None
        return front.pid

    def prompt_ui_present(self, session: Session) -> Optional[bool]:
        """Is a prompt UI on that session's screen? None if unreadable."""
        pid = self._is_front(session)
        if pid is None:
            return None
        from ..macos.axread import prompt_ui_present as _present, visible_text
        text = visible_text(pid)
        if text is None:
            return None
        return _present(text)

    def read_prompt(self, session: Session):
        """The menu on screen in that session's window, or None."""
        pid = self._is_front(session)
        if pid is None:
            return None
        from ..macos.axread import read_prompt as _read
        return _read(pid)
