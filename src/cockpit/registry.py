"""The channel registry — what hooks and the statusline told us, and the fusion.

Stage 2. This is where "Claude is actually asking you something" finally becomes
knowable. Window titles cannot express it (see attention.py); hooks can, because
the model tells them.

**The join problem, and why the statusline is structural.** A hook payload
carries `session_id` and `cwd` but *no terminal identity* — hooks run without a
controlling terminal, so there is nothing in them that names a window. And `cwd`
cannot stand in: three of Grant's sessions are all `Projects`. The statusline is
the missing link, because unlike a hook it runs as a **process inside the
session**, so it can read its own tty. Terminal can map a tty to a window id.
So the chain is:

    hook      -> session_id                      (who)
    statusline-> session_id + tty                (the join)
    Terminal  -> tty + window id                 (where)

which means the statusline is not an optional telemetry nicety — without it,
hook events cannot be attached to a tile at all.

**Fusion rule: hooks for edges, polling for truth** (design.md). A hook is a
precise statement about a moment; a title poll is a fact about now. When they
disagree, now wins:

    title says spinning        -> working     (whatever a stale hook claimed)
    else a live hook flag      -> blocked / waiting
    else                       -> idle

That ordering is what makes the board self-correcting. There is no "prompt
answered" event in Claude Code — un-blocking must be inferred — and this rule
infers it from the strongest available evidence rather than from a timer.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

from .sessions import Telemetry

log = logging.getLogger("deck.cockpit.registry")

# A permission prompt can legitimately sit unanswered for a long time, so this
# is a backstop against a *missed* clearing event, not a timeout on attention.
# Anything shorter turns a real red key dark while you're still deciding.
FLAG_TTL_S = 1800.0

# How long a record may go without a statusline report before it stops being
# joinable. Comfortably more than the 30s refreshInterval, so a busy machine
# never drops a live session, but short enough that a recycled tty is clean.
STALE_JOIN_S = 120.0

# Notification matchers -> the state they imply. Sourced from Claude Code's
# documented `Notification` matcher vocabulary; the URL path carries the intent
# rather than the payload, so a payload field rename can't silently break this.
NEEDS_INPUT = "waiting"
BLOCKED = "blocked"

# Urgency precedence. A tool approval is the stronger claim: it names a specific
# action awaiting consent, where a bare notification only says "Claude wants you".
_FLAG_RANK = {None: 0, NEEDS_INPUT: 1, BLOCKED: 2}


def _rank(flag: Optional[str]) -> int:
    return _FLAG_RANK.get(flag, 0)


@dataclass
class SessionRecord:
    """What the channels know about one session id."""

    session_id: str
    tty: Optional[str] = None
    cwd: str = ""
    flag: Optional[str] = None          # "blocked" | "waiting" | None
    flag_at: float = 0.0
    telemetry: Optional[Telemetry] = None
    updated_at: float = 0.0
    model: str = ""


class Registry:
    """Thread-safe store of channel state, keyed by Claude's `session_id`.

    Written by the HTTP listener thread, read by the poll thread. Everything
    is copied out under the lock; callers never see internal objects.
    """

    def __init__(self, clock=time.monotonic, ttl: float = FLAG_TTL_S):
        self._clock = clock
        self._ttl = ttl
        self._lock = threading.Lock()
        self._by_id: dict[str, SessionRecord] = {}

    # --- writes (from the listener thread) ---------------------------------

    def note_statusline(self, session_id: str, tty: Optional[str] = None,
                        cwd: str = "", telemetry: Optional[Telemetry] = None,
                        model: str = "") -> None:
        """The statusline reported in. This is what supplies the tty join."""
        if not session_id:
            return
        with self._lock:
            rec = self._by_id.setdefault(session_id, SessionRecord(session_id))
            if tty:
                if rec.tty and rec.tty != tty:
                    log.info("session %s moved tty %s -> %s", session_id, rec.tty, tty)
                rec.tty = tty
            if cwd:
                rec.cwd = cwd
            if telemetry is not None:
                rec.telemetry = telemetry
            if model:
                rec.model = model
            rec.updated_at = self._clock()

    def set_flag(self, session_id: str, flag: Optional[str],
                 cwd: str = "") -> None:
        """A hook fired. `flag` of None clears (the session is no longer held).

        **Flags never downgrade except by an explicit clear**, and that rule is
        load-bearing rather than defensive. Claude Code fires *both* events for a
        tool approval — `PermissionRequest` (which carries `tool_name`, so it is
        unambiguously a tool) and `Notification: permission_prompt` (which does
        not, and which a plain question fires too). Their order is not
        guaranteed. Without precedence, a tool approval would land on red and
        then be repainted blue a moment later by the generic notification,
        depending purely on arrival order.

        So: a question raises `waiting`; a tool approval raises `blocked` and
        stays there; `Stop` / `idle_prompt` / a new prompt clears it.
        """
        if not session_id:
            return
        with self._lock:
            rec = self._by_id.setdefault(session_id, SessionRecord(session_id))
            if cwd:
                rec.cwd = cwd
            rec.updated_at = self._clock()
            if flag is None:
                rec.flag, rec.flag_at = None, 0.0
            elif _rank(flag) >= _rank(rec.flag):
                rec.flag = flag
                rec.flag_at = self._clock()
            else:
                log.debug("session %s keeps %s over %s", session_id, rec.flag, flag)
                return
        log.info("session %s flag -> %s", session_id, flag or "clear")

    def forget(self, session_id: str) -> None:
        with self._lock:
            self._by_id.pop(session_id, None)

    # --- reads (from the poll thread) --------------------------------------

    def by_tty(self) -> dict[str, SessionRecord]:
        """Records that have a tty, keyed by it — the join table.

        Expired flags are dropped here rather than mutated in place, so a
        reader never depends on a writer having run recently.
        """
        now = self._clock()
        out: dict[str, SessionRecord] = {}
        with self._lock:
            for rec in self._by_id.values():
                if not rec.tty:
                    continue
                # **ttys are recycled.** A closed session's record would
                # otherwise sit here forever and silently attach its stale flag
                # to whatever new window macOS next hands /dev/ttysNNN to —
                # exactly the wrong-session failure this project exists to
                # avoid. A live session re-reports every `refreshInterval`
                # (30s), so anything this quiet is gone.
                if (now - rec.updated_at) > STALE_JOIN_S:
                    continue
                copy = SessionRecord(**vars(rec))
                if copy.flag and (now - copy.flag_at) > self._ttl:
                    log.info("session %s flag %s expired after %.0fs",
                             copy.session_id, copy.flag, now - copy.flag_at)
                    copy.flag = None
                out[copy.tty] = copy
        return out

    def snapshot(self) -> dict[str, SessionRecord]:
        with self._lock:
            return {k: SessionRecord(**vars(v)) for k, v in self._by_id.items()}

    def __len__(self) -> int:
        with self._lock:
            return len(self._by_id)


def fuse_state(title_state: str, flag: Optional[str]) -> str:
    """Combine the polled truth with the hook edge. Pure; the rule that matters.

    A spinning session is working no matter what a hook once said — that is how
    an answered permission prompt clears itself without an "answered" event
    existing. Only when the title says quiet does a hook flag get to speak.
    """
    if title_state == "working":
        return "working"
    if flag in (BLOCKED, NEEDS_INPUT):
        return flag
    return title_state
