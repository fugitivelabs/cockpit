"""Attention tracking — the memory that a snapshot doesn't have.

Adapters report what is true right now: this session is spinning, that one is
sitting at the prompt. Two things the board needs are not in that snapshot, and
this module supplies exactly one of them. The distinction matters enough to
write down, because getting it wrong is how you build a liar:

**What titles CAN support: recency.** Which session were you last actually in.
That is a memory of transitions plus focus, and it is what keeps the session
you're working in from sinking to page two the moment it stops spinning.

**What titles CANNOT support: "Claude is asking you something."** A session at
the `✳` prompt might be holding a permission prompt, waiting on an answer to a
question, or simply done with nothing pending. All three render identically,
because `✳` means "not currently spinning" and nothing more. Inferring a
response-request from a working→idle transition conflates "finished a turn"
with "blocked on you", and a control surface that cries for attention when
nothing is pending is worse than one that stays quiet.

So `blocked` and `waiting` are **never set here.** They come from Claude Code
hooks, which know the difference because the model tells them — see
../../docs/architecture.md. This module deliberately stops at recency.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import replace
from typing import Optional

from .sessions import Session

log = logging.getLogger("deck.cockpit.attention")

STATE_DIR = os.path.expanduser("~/Library/Application Support/cockpit")
STATE_FILE = os.path.join(STATE_DIR, "recency.json")
SAVE_EVERY_S = 30.0
# Long enough to survive a weekend; bounded so the file can't grow forever.
KEEP_FOR_S = 30 * 86400.0


class AttentionTracker:
    """Remembers when each session was last live, across successive snapshots.

    Not thread-safe on its own; it lives inside the poller, which drives it from
    exactly one thread.
    """

    def __init__(self, clock=time.time, state_path: Optional[str] = STATE_FILE):
        # Wall clock, deliberately, not `time.monotonic`. Recency has to mean
        # "when did I last touch this" across daemon restarts and reboots, and a
        # monotonic clock resets on both — which is precisely how a session
        # untouched for a week ended up outranking the one in active use.
        self._clock = clock
        self._state_path = state_path
        self._last_active: dict[str, float] = {}
        self._seen: set[str] = set()
        self._dirty = False
        self._last_save = 0.0
        self._load()

    # --- persistence -------------------------------------------------------

    def _load(self) -> None:
        """Best-effort. A missing or corrupt file means we start cold, not fail."""
        if not self._state_path:
            return
        try:
            with open(self._state_path) as f:
                data = json.load(f)
        except (OSError, ValueError):
            return
        if not isinstance(data, dict):
            return
        cutoff = self._clock() - KEEP_FOR_S
        for k, v in data.items():
            if isinstance(v, (int, float)) and v >= cutoff:
                self._last_active[str(k)] = float(v)
        log.info("recency restored for %d session(s)", len(self._last_active))

    def save(self, force: bool = False) -> None:
        """Persist recency, debounced. Called on tick and on shutdown.

        Written via a temp file + rename so a crash mid-write can't leave a
        truncated file that would silently reset every session's recency.
        """
        if not self._state_path or (not self._dirty and not force):
            return
        now = self._clock()
        if not force and (now - self._last_save) < SAVE_EVERY_S:
            return
        self._last_save = now
        self._dirty = False
        cutoff = now - KEEP_FOR_S
        keep = {k: v for k, v in self._last_active.items() if v >= cutoff}
        try:
            os.makedirs(os.path.dirname(self._state_path), exist_ok=True)
            tmp = self._state_path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(keep, f)
            os.replace(tmp, self._state_path)
        except OSError as e:
            log.warning("could not persist recency: %s", e)

    def mark_seen(self, session_id: str) -> None:
        """You went there deliberately — pressing a tile counts as being active.

        Also the fallback that keeps recency working when focus detection is
        unavailable (System Events automation denied): the deck still knows you
        navigated to that session, because it is what sent you there.
        """
        self._last_active[session_id] = self._clock()
        self._dirty = True

    def update(self, sessions, focused_handle: Optional[str] = None) -> list[Session]:
        """Stamp each session with `last_active`. Idempotent for a repeat snapshot.

        A session counts as active when it is working, when it is the window you
        are looking at, or when you reached it from the deck.

        **A session first seen is NOT stamped**, and that is load-bearing. The
        obvious seeding — "new session, call it active now" — gives every
        session on the first poll an identical timestamp, which collapses the
        recency sort back onto the window-id tiebreak and lands the one actually
        working session *last*. Recency has to be earned by an observed signal,
        so an untouched session stays at 0.0 and sorts below anything that has
        ever spun or held focus.
        """
        now = self._clock()
        out: list[Session] = []
        live: set[str] = set()

        for s in sessions:
            live.add(s.id)
            self._seen.add(s.id)
            if s.state == "working":
                self._last_active[s.id] = now
                self._dirty = True
            if focused_handle is not None and s.handle == focused_handle:
                self._last_active[s.id] = now
                self._dirty = True
            out.append(replace(s, last_active=self._last_active.get(s.id, 0.0)))

        # Deliberately NOT dropping sessions that are absent from this snapshot.
        # Recency is only useful if it outlives the moment: a window closed and
        # reopened, or simply not yet enumerated, should not lose its history.
        # Unbounded growth is prevented by the age cutoff at save time instead.
        self.save()
        return out

    def last_active(self, session_id: str) -> Optional[float]:
        return self._last_active.get(session_id)
