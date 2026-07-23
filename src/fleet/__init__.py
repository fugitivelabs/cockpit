"""fleet — observe and control the agent-CLI sessions running on this machine.

Answers two questions about sessions you started yourself, in your own
terminals: **which ones exist and what is each one doing**, and **take me to
that one**. Knows nothing about Stream Decks, tiles, or pixels — the surface
that renders this belongs to the layer above.

    from fleet import Registry, order_sessions
    from fleet.adapters.claude_code import ClaudeCodeAdapter

    registry = Registry()                      # what hooks + statusline said
    adapter = ClaudeCodeAdapter(registry=registry)
    for s in order_sessions(adapter.sessions()):
        print(s.state, s.cwd, s.task)
    adapter.focus(s)                           # go there

The normalized `Session` is the currency: everything above speaks Sessions,
everything below is an adapter whose one job is to produce them. Adding a
second agent CLI is "write an adapter", not "refactor" — see
docs/architecture.md.

Layout, and the split is load-bearing:

    sessions    the Session/Telemetry model, the Adapter protocol, ordering
                and labeling. Pure — no I/O, no subprocess, no platform.
    registry    what the hook and statusline channels reported, and the fusion
                rule that turns edges + polling into one state.
    attention   recency across snapshots — the memory a snapshot lacks.
    listener    the loopback HTTP endpoint hooks and the statusline report to.
    statusline  the statusline command, which is also the tty join.
    adapters/   one per agent CLI. `claude_code` is #1 and shaped the seam.
    macos/      the OS layer: what's focused, and reading a terminal's screen.

`sessions` and `registry` are importable anywhere; `macos` and the adapters
reach for platform APIs and degrade rather than raise when they are missing.
"""

import logging as _logging

from .attention import AttentionTracker
from .listener import DEFAULT_PORT, HOOK_PATHS, ChannelListener
from .registry import (
    BLOCKED,
    FLAG_TTL_S,
    NEEDS_INPUT,
    STALE_JOIN_S,
    Registry,
    SessionRecord,
    fuse_state,
)
from .sessions import (
    NEEDS_HUMAN,
    STATE_RANK,
    STATES,
    Adapter,
    Session,
    Telemetry,
    label_sessions,
    order_sessions,
    summarize,
    task_phrase,
)

# Library convention, same as deck: never configure logging ourselves; keep the
# stdlib quiet until a consumer opts in. A host wanting these lines calls
# deck.lifecycle.configure_logging(name="fleet") — without that this tree has
# only a NullHandler and stays silent.
_logging.getLogger("fleet").addHandler(_logging.NullHandler())

__all__ = [
    # the model — the currency everything above speaks
    "Session",
    "Telemetry",
    "Adapter",
    "STATES",
    "STATE_RANK",
    "NEEDS_HUMAN",
    # ordering + labeling: decisions, not rendering
    "order_sessions",
    "label_sessions",
    "task_phrase",
    "summarize",
    # channel state and the fusion rule
    "Registry",
    "SessionRecord",
    "fuse_state",
    "BLOCKED",
    "NEEDS_INPUT",
    "FLAG_TTL_S",
    "STALE_JOIN_S",
    # recency
    "AttentionTracker",
    # the ingest endpoint
    "ChannelListener",
    "DEFAULT_PORT",
    "HOOK_PATHS",
]
