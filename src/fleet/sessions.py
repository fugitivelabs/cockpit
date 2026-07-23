"""The normalized Session — the daemon's currency, and the adapter seam.

Everything above this module (the dashboard, the info bar, press routing) speaks
Sessions and nothing else. Everything below it (Claude Code today; Codex or
Copilot later) is an adapter whose one job is to *produce* Sessions. That split
is the whole point: adding a second agent CLI should be "write an adapter", not
"refactor the cockpit".

This module is deliberately pure — no AppleScript, no subprocess, no device. It
holds the value objects, the ordering rule, and the labeling rule, all of which
are decisions worth testing directly rather than through a screenshot.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol

# Ordered most-urgent first; the index is the sort rank.
#
#   blocked — a hook confirmed this session is holding a permission prompt
#   waiting — the agent explicitly wants a human (Stage 2 supplies this)
#   working — a turn is in flight; it does NOT need you
#   idle    — no turn running
#
# Honest gap: today only `working` and `idle` are distinguishable, because
# window titles are the only channel and they carry just the spinner/idle glyph.
# `blocked` and `waiting` are wired through the model and the styling now so
# Stage 2's hooks can populate them without touching anything above this line.
STATES = ("blocked", "waiting", "working", "idle")
STATE_RANK = {s: i for i, s in enumerate(STATES)}


@dataclass(frozen=True)
class Telemetry:
    """Live per-session stats. Optional on purpose — most CLIs won't have them.

    Claude Code's statusline carries all three; a Codex session may carry none.
    A poorer channel must mean a poorer tile, never a broken one, so every
    consumer has to treat this as absent-by-default.
    """

    tokens: Optional[int] = None
    cost_usd: Optional[float] = None
    context_pct: Optional[float] = None


@dataclass(frozen=True)
class Session:
    """One agent session, however we learned about it.

    `handle` is adapter-private routing data (for Claude Code: the Terminal
    window id). Nothing outside the owning adapter may interpret it — the
    dashboard passes the whole Session back to `adapter.focus()` and lets the
    adapter decide what "go there" means for its app.
    """

    id: str                 # globally unique, adapter-scoped, e.g. "claude:53025"
    agent: str              # "claude" | "codex" | …
    cwd: str                # last path component as the title reports it
    task: str               # the session's current task text, "" if unknown
    state: str              # one of STATES
    handle: str             # opaque to everyone but the adapter that made it
    telemetry: Optional[Telemetry] = None
    # Monotonic timestamp of the last time this session was working, focused,
    # or visited from the deck. Filled in by AttentionTracker — an adapter
    # producing a raw snapshot has no way to know it, and leaves it None.
    last_active: Optional[float] = None
    # The terminal device backing this session, and the agent CLI's own id for
    # it. Both are the Stage 2 join: hooks know `session_id` but not the window,
    # the window knows `tty`, and the statusline is the only thing that sees
    # both (see registry.py). Absent until a statusline has reported in.
    tty: Optional[str] = None
    session_id: Optional[str] = None
    # Display name of the model this session is on ("Opus 4.8"). From the
    # statusline; absent until it reports in.
    model: Optional[str] = None
    # The raw window title. Kept because it is the only stable key that matches
    # a Terminal window to its Accessibility counterpart — see axread.py.
    title: Optional[str] = None


class Adapter(Protocol):
    """What every agent-CLI adapter must provide. Two methods, no framework."""

    name: str

    def sessions(self) -> list[Session]:
        """Current sessions. May do I/O; callers keep it off the render loop."""

    def focus(self, session: Session) -> bool:
        """Navigate to that session — window, tab, and focus. May do I/O."""


# Two tiers, not four. Anything that genuinely wants a human sorts above
# everything else; within a tier, recency decides. Ranking all four states
# strictly is what buried the session Grant was actually working in: the moment
# it stopped spinning it fell behind every other session and paged out of sight,
# even though it was the one window he was looking at.
#
# `working` deliberately does NOT outrank a recently-used idle session. A
# session chewing through a long task needs nothing from you; the one you just
# typed into is where you live. Recency captures that and a state rank can't.
NEEDS_HUMAN = ("blocked", "waiting")


def _sort_key(s: Session):
    """Needs-you first, then most-recently-active, then a stable tiebreak.

    The final tiebreak deliberately isn't the adapter's enumeration order:
    Terminal lists windows front-to-back, which reshuffles every time you switch
    windows, and tiles that move on their own are worse than no tiles. Window
    ids are stable for the life of the window, so they are.
    """
    if s.state in NEEDS_HUMAN:
        tier = STATE_RANK.get(s.state, 0)      # blocked above waiting
    else:
        tier = len(NEEDS_HUMAN)
    # Newest window first, NOT oldest. Terminal window ids increase over time,
    # so the obvious ascending sort means "oldest window wins" — which put a
    # session untouched for a week above the one being actively used, every time
    # recency was unavailable. When we know nothing else, newer is the better
    # guess at relevance.
    try:
        native = (0, -int(s.handle))
    except (TypeError, ValueError):
        native = (1, 0)
    return (tier, -(s.last_active or 0.0), native, s.handle, s.id)


def order_sessions(sessions) -> list[Session]:
    """Sessions needing a human float to the top; the rest sort by recency.

    Grant's call (2026-07-21): urgency goes where the eye lands first. Amended
    the same day after live use — a strict state ranking made the *active*
    session vanish to page two the instant it went quiet, which is the opposite
    of an attention assistant. `last_active` comes from
    [attention.py](attention.py); sessions without it (a raw, untracked
    snapshot) simply fall back to the window-id tiebreak.
    """
    return sorted(sessions, key=_sort_key)


# ---------------------------------------------------------------------------
# Labeling — the problem Stage 1 existed to solve
#
# The natural label is the cwd, and it is the right one: it is short, stable,
# and it is how Grant already names his work. It just isn't unique — three of
# nine live sessions were all `Projects`, which makes three identical tiles.
#
# The rule (decided 2026-07-21): keep the cwd wherever it *is* unique, so
# `peregrine` / `docland` / `provenance` stay recognizable at a glance, and fall
# back to the task's leading words only for the sessions that actually collide.
# Disambiguate the ambiguous ones; don't punish the rest.

_VERBS = {
    "add", "build", "check", "continue", "create", "debug", "explore", "finish",
    "fix", "implement", "improve", "investigate", "locate", "make", "organize",
    "plan", "refactor", "research", "review", "run", "scope", "set", "setup",
    "transcribe", "update", "write",
}

_STOPWORDS = {
    "a", "an", "the", "and", "or", "to", "for", "in", "of", "with", "on",
    "into", "across", "from", "at", "by", "up", "out", "via", "my", "its",
}


def task_phrase(task: str, max_chars: int = 16) -> str:
    """The shortest distinctive head of a task, for use as a label.

    Drops a leading imperative verb (nearly every task starts with one, and it
    is the least distinguishing word in the string) and the stopwords after it,
    then takes whole words while they fit. Two-character words survive only if
    they're uppercase, so acronyms like `OS` and `NC` are kept while `v2` and
    `to` are dropped.

        "Scope v2 corpus migration to v3 charter" -> "corpus migration"
        "Personal OS reorganization and dossier"  -> "Personal OS"
        "Locate and recover abandoned estate"     -> "recover"
    """
    words = task.split()
    if words and words[0].lower().strip(":,") in _VERBS:
        words = words[1:]

    keep = []
    for w in words:
        bare = w.strip(",.;:—-")
        if not bare or bare.lower() in _STOPWORDS:
            continue
        if len(bare) < 3 and not bare.isupper():
            continue
        keep.append(bare)

    out = ""
    for w in keep:
        candidate = f"{out} {w}".strip()
        if out and len(candidate) > max_chars:
            break
        out = candidate
        if len(out) >= max_chars:
            break
    return out


def label_sessions(sessions) -> dict[str, tuple[str, str]]:
    """{session id: (label, sub)} — **the cwd is always the label.**

    Amended 2026-07-22 (Grant's call, on seeing it): the earlier rule promoted
    the task head to the label whenever a cwd collided and demoted the cwd to
    the subtitle. That disambiguated, but it made the board structurally
    inconsistent — `peregrine` showed project-over-task while a colliding
    `Projects` session showed task-over-project, so the big line meant a
    different *kind* of thing from one tile to the next and the eye had no
    stable place to land.

    The fix keeps the disambiguation and drops the inversion: the project name
    is always the big line, and the distinguishing task head always goes in the
    subtitle. Three `Projects` tiles still read differently, because the second
    line is what tells them apart — it just no longer costs the first line.

    The subtitle prefers `task_phrase` over the raw task everywhere, not only on
    collisions: it is the distinctive head of the string, so it survives
    truncation at 96 px far better than "Implement the thing that…".
    """
    out: dict[str, tuple[str, str]] = {}
    for s in sessions:
        out[s.id] = (s.cwd, task_phrase(s.task) or s.task)
    return out


def summarize(sessions) -> dict[str, int]:
    """Counts per state, every state present — for the info bar."""
    out = {s: 0 for s in STATES}
    for s in sessions:
        if s.state in out:
            out[s.state] += 1
    return out
