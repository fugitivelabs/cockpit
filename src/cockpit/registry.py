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

**But the statusline is not a heartbeat, and treating it as one was a bug.** It
runs on assistant messages and `refreshInterval` — and it stops entirely while a
permission prompt is on screen. A blocked session therefore emits nothing at
all: no statusline (paused) and no hooks (that is what blocked *means*). Any
rule that expires a join on silence will expire exactly the sessions worth
showing. So silence only ages out a record that holds no flag; a flagged one is
governed by `FLAG_TTL_S`, the timer written for that question. See `by_tty`.

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
# joinable *on its own evidence*. Comfortably more than the 30s refreshInterval,
# so a busy machine never drops a live session.
#
# This is the weaker of the two liveness signals and it used to be the only one,
# which produced the bug this rule now exists alongside: **the statusline stops
# refreshing while a permission prompt is on screen.** Measured 2026-07-22 —
# over two minutes every live session's statusline ran (12-30 invocations each)
# except the one sitting at an approval prompt, which ran zero times. Since a
# blocked session also fires no further hooks (that is what blocked means), its
# record went quiet for exactly as long as it was worth showing, aged out at
# 120s, and the red tile went dark while the prompt was still up.
#
# So a timer alone cannot answer "is this record still this tty's session". The
# caller can: it enumerates live ttys every poll. See `by_tty`.
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


def _supersedes(candidate: "SessionRecord", prior: "SessionRecord") -> bool:
    """Which of two records claiming one tty is the live session? See `by_tty`.

    Recency decides it, because a tty is reused by a *later* session than the
    one that left it. When the two are indistinguishable — identical
    `updated_at`, which a coarse clock or two hooks in one tick can produce —
    there is no recency signal left, so the tie breaks toward the safer wrong
    answer instead of toward whichever record happened to be inserted first.

    Safer is unflagged. Losing a flag costs an alert you would have seen
    anyway the moment the session speaks again; keeping a dead session's flag
    paints a false red on somebody else's window, which is the failure this
    whole join is built to avoid.
    """
    if candidate.updated_at != prior.updated_at:
        return candidate.updated_at > prior.updated_at
    return prior.flag is not None and candidate.flag is None


@dataclass
class SessionRecord:
    """What the channels know about one session id."""

    session_id: str
    tty: Optional[str] = None
    cwd: str = ""
    flag: Optional[str] = None          # "blocked" | "waiting" | None
    flag_at: float = 0.0
    # Which tool's approval raised the flag, lowercased. The correlation key for
    # clearing — see `set_flag`. Empty when the raising event named no tool.
    flag_tool: str = ""
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

    def set_flag(self, session_id: str, flag: Optional[str], cwd: str = "",
                 tool: str = "", scope: str = "session") -> None:
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

        **A tool-scoped clear only clears the tool that raised the flag.** Claude
        Code runs tools CONCURRENTLY under one session_id, so a sibling
        finishing is not evidence that the blocked one was answered. Observed
        live 2026-07-22 — a session held a WebFetch approval while WebSearch,
        ToolSearch and StructuredOutput kept completing, and each sibling wiped
        the flag a moment after PermissionRequest raised it:

            /hook/blocked   tool=WebFetch    -> blocked
            /hook/tool-done tool=WebSearch   -> cleared  (wrong: other tool)
            (Notification re-nags)           -> waiting
            /hook/tool-done tool=ToolSearch  -> cleared  (wrong again)

        which is precisely the idle/red/amber flicker that produces. `scope`
        separates the two kinds of clearing edge:

            "tool"     PostToolUse — clears only a flag this same tool raised
            "session"  Stop, idle_prompt, UserPromptSubmit — the turn or the
                       prompt is over, whichever tool was involved
        """
        if not session_id:
            return
        with self._lock:
            rec = self._by_id.setdefault(session_id, SessionRecord(session_id))
            before = rec.flag
            if cwd:
                rec.cwd = cwd
            rec.updated_at = self._clock()
            new_tool = tool.lower()
            if flag is None:
                if (scope == "tool" and rec.flag is not None and rec.flag_tool
                        and new_tool != rec.flag_tool):
                    log.debug("session %s keeps %s: %s finished, not %s",
                              session_id, rec.flag, tool, rec.flag_tool)
                    return
                rec.flag, rec.flag_at, rec.flag_tool = None, 0.0, ""
            else:
                # A flag naming a DIFFERENT tool than the one on record is a new
                # prompt, not an update to the old one, so it replaces outright
                # rather than rank-competing. This is what lets an AskUserQuestion
                # (remapped to `waiting`) override a `blocked` left standing by the
                # Bash prompt one event earlier — the exact case where a question
                # showed red. A NAMED tool is required: the bare Notification that
                # accompanies every approval carries no tool, stays subordinate to
                # ranking, and so still cannot downgrade a real block (the reason
                # the ranking exists at all).
                new_prompt = (new_tool and rec.flag is not None
                              and new_tool != rec.flag_tool)
                if rec.flag is None or new_prompt or _rank(flag) >= _rank(rec.flag):
                    rec.flag = flag
                    rec.flag_at = self._clock()
                    rec.flag_tool = new_tool
                else:
                    log.debug("session %s keeps %s over %s (same/ambiguous prompt)",
                              session_id, rec.flag, flag)
                    return
            changed = rec.flag != before
        # Only a real transition earns a line, so a quiet board stays quiet in
        # the log and the events that matter stay findable.
        if changed:
            log.info("session %s flag -> %s", session_id, flag or "clear")

    def forget(self, session_id: str) -> None:
        with self._lock:
            self._by_id.pop(session_id, None)

    # --- reads (from the poll thread) --------------------------------------

    def by_tty(self) -> dict[str, SessionRecord]:
        """Records that have a tty, keyed by it — the join table.

        **Silence expires a record only if it holds no flag.** Going quiet is
        weak evidence of death and no evidence at all for the one state worth
        showing: a blocked session emits nothing by construction (see
        STALE_JOIN_S). A flagged record is bounded by `FLAG_TTL_S` instead,
        which is the timer actually written for "how long may a prompt sit".

        **ttys are recycled**, and letting a record outlive its session is what
        makes that reachable here. A dead session's flag must never land on
        whatever new window macOS next hands /dev/ttysNNN to. Note that a live
        tty proves nothing about *whose* it is — quit `claude` and start it
        again in the same window and the old record still names a real
        terminal — so the guard is the record itself: when two claim one tty,
        the more recently updated wins (`_supersedes`). That used to fall out
        of dict insertion order, which was right by accident and only while
        records could not outlive their sessions.

        **The residual hazard, stated honestly**, is a dead flagged record
        whose tty is reused before the new session's first statusline report.
        For that window the ghost is the only claimant, so `_supersedes` has
        nothing to compare and the stale flag paints the new window. Nothing
        masks it: a session that has not started a turn has no task field, and
        `parse_title` reads that as *idle*, not spinning, so `fuse_state` has
        no polled truth to overrule the flag with. (Checked 2026-07-22 —
        an earlier version of this comment claimed the spinner covered it,
        which is true only once the new session takes its first turn.)

        It is bounded by `FLAG_TTL_S`, which this change widens from the old
        120s to 1800s — a real regression in exchange for the fix, and the
        reason to reach for eviction on the raw window list if it ever bites.

        Expired flags are dropped here rather than mutated in place, so a
        reader never depends on a writer having run recently.
        """
        now = self._clock()
        out: dict[str, SessionRecord] = {}
        with self._lock:
            for rec in self._by_id.values():
                if not rec.tty:
                    continue
                copy = SessionRecord(**vars(rec))
                flag_age = now - copy.flag_at
                if copy.flag and flag_age > self._ttl:
                    log.info("session %s flag %s expired after %.0fs",
                             copy.session_id, copy.flag, flag_age)
                    copy.flag = None
                # Fresh by its own report, or held open by a live flag — the
                # expired-flag branch above having already reduced a lapsed one
                # to None, so this reads the flag as it will be served.
                if (now - copy.updated_at) > STALE_JOIN_S and copy.flag is None:
                    continue
                prior = out.get(copy.tty)
                if prior is not None and not _supersedes(copy, prior):
                    continue
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
