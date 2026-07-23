# Splitting out the session library

Decided 2026-07-22. cockpit today is two things wearing one coat: `deck/` (the
use-case-agnostic device library) and `cockpit/` — which is *both* a substantial
"observe and control my Claude Code sessions" engine **and** the Stream-Deck UI
glue that renders it. This promotes the engine to a **second first-class in-repo
library**, peer to `deck/`, so the end state is:

    fleet/       observe + control locally-running agent-CLI sessions.
                 Knows nothing about the Stream Deck.
    deck/        drive the device. Knows nothing about Claude.
    cockpit/     light UI glue: wire the two together.

Not a published package (same "build for us" stance as `deck/` — no
API-stability or docs obligations). The payoff is future agent adapters
(Codex/Copilot) and reuse elsewhere, not a launch. Companion to
[landscape.md](landscape.md), which surveyed only the *device* side, and to the
session-ecosystem scan that motivated this (memory: `cc-session-ecosystem`).

## Why this is worth doing, in one line

The ecosystem scan (2026-07-22) found **no importable, normalized
agent-session-observability library exists** — a dozen popular apps (agentsview,
codeg, c9watch, agenthud, ccm) each re-implement privately what `sessions.py`
already has cleanly factored. We are extracting into a boundary what everyone
else bakes into an app.

## The boundary is already clean — measured, not hoped

Intra-`cockpit/` import graph (2026-07-22, at main `c1b3e2c`):

    sessions      -> (nothing)                    LIB core (pure leaf)
    registry      -> sessions                      LIB core
    attention     -> sessions                      LIB core
    osint         -> (nothing)                     LIB core (macOS OS layer)
    axread        -> (nothing)                     LIB core (macOS AX layer)
    statusline    -> (nothing)                     LIB (stdlib-only shim)
    claude_code   -> axread osint registry sessions  LIB — reference adapter
    listener      -> doctor registry sessions      LIB — but see the one edge
    ---
    dashboard     -> attention sessions            GLUE (deck tiles)
    actions       -> dashboard osint               GLUE (press routing)
    daemon        -> actions claude_code dashboard listener registry  GLUE (orchestrator)
    palette       -> (nothing)                     GLUE (deck visuals)
    claude_config -> listener                      GLUE (wiring/config)
    doctor        -> (nothing)                      GLUE (app self-checks)
    __init__/__main__ -> daemon                     GLUE (entry)

The candidate library set imports **only within itself, with exactly one
exception**: `listener → doctor`. It is a *lazy* import inside the `/doctor` GET
handler (`from .doctor import daemon_self_checks`, listener.py:116). That single
edge is the only thing standing between us and a clean cut.

**Sever it by inversion:** the library `ChannelListener` takes an optional
`self_check` callable (absent → `/doctor` still answers, with `checks: []`).
`daemon.py` passes `daemon_self_checks` in when it builds the listener. Library
gains a seam; app keeps its endpoint; the dependency now points app→lib, never
lib→app. A raising probe degrades to `checks: []` rather than a 500 — the
endpoint is a debugging aid and must not become a way to take the daemon down.

## File-by-file fate

| File | Destination | Notes |
|---|---|---|
| `sessions.py` | `fleet/` core | Move verbatim. The Session/Telemetry/Adapter model, ordering, labeling, summarize. Pure. |
| `registry.py` | `fleet/` core | Move verbatim. The channel fusion — the crown jewels (see below). |
| `attention.py` | `fleet/` core | Move; `STATE_DIR` default path becomes a constructor arg the app supplies (it already accepts `state_path`). |
| `statusline.py` | `fleet/` | Move; `COCKPIT_PORT`/URL stay env-configurable. Reusable tty-walking shim. |
| `osint.py` | `fleet/macos/` | Agent-agnostic OS focus/activate/keystroke. (Named `macos/`, not `os/` — the latter shadows the stdlib module in every reader's head.) |
| `axread.py` | `fleet/macos/` | Accessibility screen read + prompt parse. |
| `claude_code.py` | `fleet/adapters/` | The reference adapter (Claude + Terminal.app). One of eventually several. |
| `listener.py` | `fleet/` | Move after severing the `doctor` edge (inject `self_check`). |
| `dashboard.py` | stays `cockpit/` | Deck tiles — consumes the library. |
| `actions.py` | stays `cockpit/` | Press→action routing. |
| `daemon.py` | stays `cockpit/` | Orchestrator; wires lib + deck. |
| `palette.py`, `doctor.py`, `claude_config.py`, `__main__.py` | stay `cockpit/` | App glue/entry. |

Tests keep their imports pointed at the new paths. **Splitting the straddling
files is deliberately deferred**, and that is a judgement rather than an
oversight: `test_sessions.py` and `test_answer.py` interleave library assertions
(parse/order/label, prompt parsing) with app assertions (tiles, the answer bar)
line by line, so carving them apart during a move that must not change behavior
trades real risk of dropping an assertion for a purely cosmetic filing. The
suite is the safety net for this refactor; reorganising the safety net *during*
the refactor is the wrong order. Do it as its own change, when a second consumer
of `fleet/` makes "which of these are the library's tests" a question that
actually needs answering.

## The crown jewels — logic that MUST survive the move byte-for-byte

Each was earned by a live bug; a "cleanup" during the move that loses one is a
regression, not a refactor.

- **registry.py `set_flag`**: a *named-tool* flag differing from the one on
  record **replaces outright** (a new prompt — e.g. AskUserQuestion→`waiting`
  overriding a stale Bash `blocked`), while a *bare tool-less* Notification stays
  subordinate to ranking (so it can't downgrade a real block). Sharpened in
  commit e2612ed.
- **`fuse_state`**: "hooks for edges, polling for truth" — a spinning title beats
  any stale flag, which is how an answered prompt clears with no "answered" event.
- **the tty join** (`by_tty`, `_supersedes`, `STALE_JOIN_S` vs `FLAG_TTL_S`): a
  blocked session emits nothing, so silence only ages out an *unflagged* record;
  recycled ttys break ties toward the safer (unflagged) record.
- **attention.py earned-recency**: a first-seen session is deliberately NOT
  stamped active, or the recency sort collapses onto the window-id tiebreak.

Its 518-assertion suite (0 failing at `c1b3e2c`) is what guards all of this
through the move.

## Phased plan

**Phase A — mechanical extraction, zero behavior change. DONE 2026-07-22.**
Created `fleet/`, moved the files with `git mv`, severed the `listener→doctor`
edge by injection, rewired imports, pointed the tests at the new paths. Green at
the same 518 assertions, plus 4 new ones covering the injected seam = **522**.
Verified beyond the suite: nothing in `fleet/` imports `cockpit` *or* `deck`,
every entry point imports, and the statusline still prints and exits 0 with the
daemon down.

**Two traps it hit that the plan above did not predict** — both silent, both
the kind a "mechanical" move is supposed to be too boring to contain:

1. **Logging would have gone quiet.** Every moved module named its logger
   `deck.cockpit.*`, and `configure_logging()` attaches handlers to the `deck`
   tree — so the names were *inheriting* their handlers. Renaming them to
   `fleet.*` orphans them: no handler, no output, no error. Fixed by giving
   `configure_logging` a `name` parameter (default unchanged) and having the
   daemon configure both trees. A library that logs into the void looks exactly
   like a library with nothing to say.
2. **The statusline rename is a live migration, not a refactor.** The command is
   written into `~/.claude/settings.json` as a module path string, so moving
   `cockpit.statusline` → `fleet.statusline` strands every already-wired machine.
   The *writer* now emits only the new path while every *recognizer* accepts both
   (`is_our_statusline`), so a stale install reads as wired rather than broken
   and `strip()` still removes it. **Operationally: re-run `cockpit wire` after
   this lands**, or the statusline keeps invoking a module that no longer exists
   — and with it goes the tty join, which is the only thing attaching hook
   events to a window at all.

**Phase B — better discovery (the real upgrade).** Add a transcript/process-scan
discovery adapter behind the existing `Adapter` seam (c9watch pattern: read
`~/.claude/projects/**/*.jsonl` + scan processes) — cross-terminal, retires the
window-title-scraping fragility, and can classify working/idle straight from the
transcript, shrinking reliance on the statusline→tty→window join. A/B it against
the Terminal adapter on the real ~9-session desktop before making it #1. **Keep
hooks for true `blocked`** — the transcript can't cleanly separate
blocked-on-prompt from turn-finished.

**Phase C — terminal-agnostic focus.** Generalize `focus()`/navigation beyond
Terminal.app to iTerm2/Ghostty via TTY→window (ccm pattern).

**Left alone deliberately:** the answer-prompt path. cockpit's screen-read
(axread) + verified-frontmost keystroke is a chosen safety model — the daemon
*cannot* express a permission decision (listener always replies bare `200 {}`).
The hook-handshake alternative (menubar-buddy) is a *different* safety model, not
a clean swap; revisit only as an explicit decision.

## The name

**`fleet`** (Grant's call, 2026-07-22, from `crew` / `fleet` / `roster` /
`agentsessions`). `deck/` is the device; `fleet/` is the set of running sessions
you command from it. Short, no collision with the `Agent`/`Adapter` vocabulary
already in the model, and it reads right at the import site:

    from fleet import Session, Registry, order_sessions
    from fleet.adapters.claude_code import ClaudeCodeAdapter
