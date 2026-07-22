# Architecture: the session cockpit

Decided 2026-07-20. **Build for us, not for launch** — the market is crowded
enough (see [landscape.md](reference/landscape.md)) that shipping a product adds discipline
cost for little return. Goal: the best possible cockpit for Grant's own Claude
Code + homelab use, kept clean, launch reconsidered later only if it earns it.

That decision is freeing: we optimize for our real workflow, never a hypothetical
user, and skip device-coverage / API-stability / docs obligations.

## The two-layer split (keep this)

- **`deck/`** — the use-case-agnostic library. Built and verified. Knows nothing
  about Claude, terminals, or homelab. This stays clean regardless.
- **the cockpit** — a consumer built ON `deck/`, wired to the channels below.
  This is where all Claude/homelab knowledge lives.

The moat over the incumbent apps (AgentDeck, agentsd) was never a single feature —
they have the features. It's **multi-channel ingest unified on one surface**, and
library-not-app. They wire one or two channels to a fixed app; we fuse all of them.

## The channels (all verified against Claude Code docs, 2026-07-20)

**Get the shape right first, because it is easy to state backwards.** The cockpit
is a **control surface over the whole Claude-Code + desktop lifecycle** — plugins,
hooks, statusline telemetry, OS focus, app/tab switching, notifications. It is
**not an endpoint that an agent paints to.** Two directions, and MCP is a sliver
of one of them:

- **What the deck shows** is fused by the daemon from **ambient** channels that
  need no cooperation from the model: statusline telemetry, hook events, and OS
  polling. This is the backbone.
- **What the deck does** on a press is **OS-level, and decoupled from whatever
  raised the alert**: the core move is *navigate to that window and that tab and
  take focus* — a press always means "take me there," however we learned it
  needed you. (Plus: answer a held permission prompt, jump to an app, synthesize
  a keystroke, fire a notification.) The notifying channel and the navigation
  action are independent by design; no model cooperation required for either.
- **MCP** is the *narrowest* channel — a **single configured, active session**
  deliberately emitting "paint this / ask the human this." Genuinely useful, but
  one tiny fraction: it is scoped to one session that opted in, and it is
  explicitly **not how the deck normally gets its content or takes its actions.**

The ordering below is by weight: statusline first, MCP near the bottom.

### Statusline — the passive telemetry backbone. The big unlock.

Claude Code runs a statusline command and feeds it session JSON on stdin. It
carries **exactly the "Claude statistics" we wanted, live, with no OTel collector
to stand up:**

```json
{
  "model": { "id": "claude-opus-4-8", "display_name": "Opus" },
  "session_id": "…", "cwd": "…",
  "cost": { "total_cost_usd": 0.0123, "total_duration_ms": 45000,
            "total_api_duration_ms": 2300 },
  "context_window": {
    "total_input_tokens": 15500, "total_output_tokens": 1200,
    "context_window_size": 200000, "used_percentage": 8,
    "current_usage": { "input_tokens": 8500, "output_tokens": 1200,
      "cache_creation_input_tokens": 5000, "cache_read_input_tokens": 2000 }
  }
}
```

- **Tokens, cost, context %, duration** — all present, pre-calculated.
- Fires after each assistant message, after `/compact`, on permission-mode change,
  on vim-mode toggle; debounced 300ms; `refreshInterval` keeps it live when idle.
- The command is arbitrary — it can, as a side effect, write this JSON to a socket
  or file the daemon reads. This is how the cockpit learns token/cost/context.
- Caveat: statusline is hidden briefly during autocomplete and permission prompts.

**This is the single most valuable channel** and the one the incumbents mostly
don't tap. It is `used_percentage` that lets a key go amber at 80% context.

### Hooks — event-driven state

- `PermissionRequest` (tool-matched) → session is **blocked** awaiting approval
- `Stop` → turn finished / idle
- `PreToolUse` (matcher e.g. `Bash`, `mcp__.*`) → a tool is about to run
- **HTTP hook type confirmed** — a hook can `POST` straight to
  `http://localhost:PORT/…` on the daemon, no shell script in between:
  ```json
  { "type": "http", "url": "http://localhost:8080/hooks/permission",
    "timeout": 30 }
  ```
- Hook payloads carry `session_id` + `cwd` + tool data, but **not token counts** —
  those come from the statusline. The channels are complementary by design.

### MCP — the deliberate channel, and the smallest one

A bundled MCP server (stdio) exposes tools the model calls to **push to the deck**
(paint a key, ask a question) and, via the channel capability
(`capabilities.experimental['claude/channel']`), push notifications into the
session. This is the "agent deliberately drives the deck / asks the human" path —
distinct from, and far narrower than, the passive channels above.

**Scope, stated plainly so it stops getting overweighted:** MCP only ever reflects
**one configured, active session** — the one that spawned this server and opted
in. It carries only what that model deliberately emits. It cannot see the other
nine terminals, cannot drive focus or app/tab switching, and is not how the deck
paints itself. Treat it as a nice-to-have for the session in front of you, never
as the backbone. Everything lifecycle-wide comes from statusline + hooks + OS.

### OS state — ground truth

AppleScript window enumeration + focus, already built and verified. The poll that
reconciles against hook edges (a session's glyph flipping back means it un-blocked
regardless of the last hook).

### OpenTelemetry — optional, heavier

`claude_code.cost.usage` and `claude_code.token.usage` metrics exist, but require
an OTLP collector. **The statusline supersedes this for our needs** — same data,
no infrastructure. Keep OTel in reserve only if we ever want cross-session
aggregation.

## Multiple agent CLIs — the adapter seam (Claude Code first)

Claude Code is the driver and stays the driver. But Codex and Copilot CLIs are
coming, so the cockpit must not hardcode Claude *at the cockpit level* — the fix
is a thin seam, not a framework, and most of the machinery is already shared.

**What is already agent-agnostic (build once, reuse for every CLI):**
- `deck/` — the device. Knows nothing about anything.
- the **OS layer** (`cockpit/osint.py`) — `frontmost()` (what's in focus) and
  `activate()` (jump to an app). Focus and app-switching are identical whoever
  the agent is. This is where "know what's in focus" and "alt-tab-ish" live, and
  it's nearly all OS-provided one-liners.
- rendering, press→action routing, and the whole daemon/lifecycle layer.

**What differs per agent (one adapter each, ideally ~one file):**
- **session discovery** — how you learn its sessions exist (Claude: statusline
  `session_id` + Terminal windows; another CLI may be pure window-title parsing).
- **telemetry** — Claude Code is unusually rich: live tokens/cost/context% over
  the statusline, plus HTTP hooks. Codex/Copilot may expose far less and lean on
  OS window-title state instead.
- **event/state shapes** — what "blocked / working / idle" looks like for that CLI.

**The seam is a normalized `Session`** as the daemon's currency. An adapter's one
job is to produce Sessions; the View renders Sessions; a press routes to an
agent-agnostic OS action. **Built 2026-07-21 in `cockpit/sessions.py`**, shaped
by adapter #1 rather than guessed:

    Session: id, agent ("claude"|"codex"|…), cwd, task,
             state (blocked|waiting|working|idle),
             handle,                                   # adapter-private routing
             telemetry? (tokens, cost, context_pct)    # OPTIONAL — many CLIs
                                                       # won't have it; degrade

    Adapter: name; sessions() -> [Session]; focus(Session) -> bool

Two things the real implementation added to the sketch. **`handle`** — opaque
adapter-private routing data (for Claude Code, the Terminal window id). The
dashboard hands the whole Session back to `adapter.focus()` rather than
interpreting it, so "go there" can mean a window id for one CLI and something
else entirely for the next. And **`state` is ordered by urgency**, so ordering is
a property of the vocabulary rather than a rule each renderer reinvents — today
only `working`/`idle` are detectable from titles, with `blocked`/`waiting` wired
through and waiting on Stage 2's hooks.

The telemetry block is optional on purpose: the model must render a Codex session
that carries only a window title as gracefully as a Claude session with full
stats. Poorer channels mean a poorer tile, not a broken one.

**Config: deliberately minimal, and deliberately not yet.** When there are two
adapters, a small config selects which are enabled plus per-adapter knobs (a
title regex, a socket port). Until adapter #2 actually exists, that config is
defaults in code — no file, no format. We are explicitly avoiding the first-class
apps' config sprawl; the whole point of build-for-us is that the config is "what
Grant runs", discovered from a second real adapter, not designed up front.

**OS actions, incl. "alt-tab":** mostly the OS's job. `activate(bundle)` is the
direct-jump primitive (better than blind Cmd-Tab cycling for a control surface).
The full navigation target is **(window, tab) + focus**, and the *tab* granularity
is where per-app work lives: selecting a Terminal tab is Terminal scripting;
selecting a browser tab is the native-host bridge in [firefox-tabs.md](reference/firefox-tabs.md)
(deferred). So "navigate to that window and that tab and focus" decomposes as
`activate(app)` → adapter selects window → adapter selects tab — the first step
generic, the last two app-specific. **Keystroke synthesis** (accept/reject, or a
literal Cmd-Tab) is one osascript call but is the *dangerous* direction — gated
behind the Stage-3 focus guard (verify `frontmost()` is the target before
sending). `frontmost()`, built now, is half of that guard.

**Sequence:** done as far as it should go. The focus primitive is in, and the
Claude Code adapter (Stage 1) coded the `Session` model and the adapter protocol
against one real implementation. Codex/Copilot are now "write an adapter", not
"refactor" — and the config question stays deferred until adapter #2 exists to
shape it.

## Packaging — one plugin, one install

A Claude Code plugin bundles **all** of it: `hooks/hooks.json`, `.mcp.json`,
statusline config in `settings.json`, plus commands/agents if wanted. Structure:

```
cockpit-plugin/
├── .claude-plugin/plugin.json
├── hooks/hooks.json        # HTTP POSTs to the daemon
├── .mcp.json               # spawns / points at the daemon's MCP endpoint
└── settings.json           # statusline command -> writes JSON to daemon
```

Install: `/plugin install …` or `--plugin-dir ./path` for local. So "set up the
cockpit" is one step, and all three channels point at the same daemon with no
documented conflicts.

## The daemon shape

One Python process:

- owns the deck via `deck/`
- listens on `localhost:PORT` for hook HTTP POSTs
- reads a socket/file the statusline writes (tokens/cost/context)
- speaks MCP over stdio (spawned by Claude Code) for the deliberate channel
- polls Terminal windows for ground truth
- fuses all of it into what the 8 keys + info bar show, and routes presses back
  (focus a window; answer a held permission hook; call an MCP tool result)

## What we are deliberately NOT building

- No GUI config app (OpenDeck/Companion own that)
- No homelab-control headline (Companion owns it; it's a later demo at most)
- No general device coverage, API-stability guarantees, or user docs — this is
  ours. Clean, not productized.
