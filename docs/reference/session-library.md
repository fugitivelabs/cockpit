# Splitting out the session library

Decided 2026-07-22. cockpit today is two things wearing one coat: `deck/` (the
use-case-agnostic device library) and `cockpit/` — which is *both* a substantial
"observe and control my Claude Code sessions" engine **and** the Stream-Deck UI
glue that renders it. This promotes the engine to a **second first-class in-repo
library**, peer to `deck/`, so the end state is:

    <libname>/   observe + control locally-running agent-CLI sessions.
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
`self_check` callable (default `None` → the `/doctor` route 404s or returns
`{"checks": []}`). cockpit's `doctor.py` passes `daemon_self_checks` in when it
wires the daemon. Library gains a seam; app keeps its endpoint; the dependency
now points app→lib, never lib→app.

## File-by-file fate

| File | Destination | Notes |
|---|---|---|
| `sessions.py` | `<libname>/` core | Move verbatim. The Session/Telemetry/Adapter model, ordering, labeling, summarize. Pure. |
| `registry.py` | `<libname>/` core | Move verbatim. The channel fusion — the crown jewels (see below). |
| `attention.py` | `<libname>/` core | Move; `STATE_DIR` default path becomes a constructor arg the app supplies (it already accepts `state_path`). |
| `statusline.py` | `<libname>/` | Move; `COCKPIT_PORT`/URL stay env-configurable. Reusable tty-walking shim. |
| `osint.py` | `<libname>/os/` (macOS) | Agent-agnostic OS focus/activate/keystroke. |
| `axread.py` | `<libname>/os/` (macOS) | Accessibility screen read + prompt parse. |
| `claude_code.py` | `<libname>/adapters/` | The reference adapter (Claude + Terminal.app). One of eventually several. |
| `listener.py` | `<libname>/` | Move after severing the `doctor` edge (inject `self_check`). |
| `dashboard.py` | stays `cockpit/` | Deck tiles — consumes the library. |
| `actions.py` | stays `cockpit/` | Press→action routing. |
| `daemon.py` | stays `cockpit/` | Orchestrator; wires lib + deck. |
| `palette.py`, `doctor.py`, `claude_config.py`, `__main__.py` | stay `cockpit/` | App glue/entry. |

Tests move with their code and split where a file straddles the line:
- `test_channels.py` (107) → library (registry/fusion/listener/statusline).
- `test_osint.py` (13) → library.
- `test_sessions.py` (176) → **split**: parse/order/label → library; SessionTile/poller/dashboard → app.
- `test_answer.py` (65) → **split**: axread parsing → library; actions answer-bar → app.
- `test_framework.py` (24), `test_lifecycle.py` (37), `test_visual.py` (96) → unaffected (deck/app).

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

**Phase A — mechanical extraction, zero behavior change.** Create `<libname>/`,
move the files, sever the `listener→doctor` edge by injection, fix imports, move
+ split the tests. Done when the suite is green at the same 518 assertions and
`cockpit` runs unchanged. Pure structure; no new capability. Commit as its own
reviewable unit.

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

## Open: the name

`<libname>` is a placeholder. `deck/` is the device; this is the sessions/agents
you command from it. Candidates: `crew`, `fleet`, `roster`, or the plain
`agentsessions`/`sessions`. Pick before Phase A — it's every import path.
