# Competitive landscape — are we cloning?

Researched 2026-07-20, skeptical framing (find the thing that already exists and
argue we're a clone). Stars/dates pulled live.

## The shape of the market

Two layers with a graveyard between them:

- **Drivers** (bottom): `python-elgato-streamdeck` (1.1k★), Julusian's Node lib,
  the Rust crate. Enumerate, set brightness, push key image, read callback.
  **No widgets, layout, state/diffing, or event bus** — confirmed from source.
- **Config apps** (top): Elgato's app, OpenDeck, **Bitfocus Companion**,
  StreamController. A human arranges buttons in a GUI that fire actions.
- **Between them** — a maintained, adopted, code-first *framework* you program
  against: **does not exist.** The few attempts are abandoned (see below). Real
  opportunity, but the emptiness may reflect weak demand, because Companion eats
  "program against the deck" from above.

## Verdict per use case

| Use case | Status | Why |
|---|---|---|
| **Homelab / external control** | **SATURATED** | Companion owns it |
| **Bidirectional agent I/O** | **PARTIAL, filling fast** | AgentDeck + agentsd exist as apps |
| **Deck as live display surface** | **GENUINELY THIN** | nobody treats the panel as a screen |

### Homelab — do NOT pitch this. Companion owns it three ways.

[Bitfocus Companion](https://github.com/bitfocus/companion) — 2.2k★, MIT, ~9,700
commits, alive (v4.2). Worse overlap than feared:

- **Already drives homelabs.**
  [companion-module-homeassistant-server](https://github.com/bitfocus/companion-module-homeassistant-server)
  exposes HA entities as actions with live "Entity State" feedback painted back
  onto keys; generic MQTT module; 700+ others.
- **Already lets external code drive the deck, two-way.** The
  [Satellite API](https://github.com/bitfocus/companion-satellite) (TCP 16622 /
  WS 16623) and
  [companion-surface-api](https://github.com/bitfocus/companion-surface-api) let
  your program register as a virtual surface — receive images to paint, report
  key presses back. Plus buttons triggerable over HTTP/OSC/TCP/UDP/WS.
- **The Python HA-specific version is also taken:**
  [python-homeassistant-streamdeck](https://github.com/abcminiuser/python-homeassistant-streamdeck),
  by the driver's own author — YAML pages, live button state.

Our only wedge here is "code-first, unified with the agent surface." Thin. The
homelab story is a *demo the same library happens to enable*, never the pitch.

### Bidirectional agent I/O — not the novelty we thought; window closing

Two projects already do true paint + read-back approve/deny loops:

- [puritysb/AgentDeck](https://github.com/puritysb/AgentDeck) — 157★, MIT, active.
  Daemon on :9120; keys show running agents with animated state; presses
  interrupt / answer YES/NO/ALWAYS; tracks token usage + cost; drives Stream
  Deck+, phones, ESP32, TUI. The closest thing to our UC1, and genuinely two-way.
- [paultyng/agentsd](https://github.com/paultyng/agentsd) — 7★, TS, an Elgato
  *plugin*. `Claude Code hooks → HTTP :9200 → deck`. **PermissionRequest hooks
  hold the HTTP response open up to 120s so you approve/deny from a key press** —
  exactly the two-way loop we described.

But **none of them is a library.** They are apps/plugins hard-wired to
coding-agent session lifecycles. The library form is still open.

### Live display surface — the real gap

Nobody frames the grid as a unified live display / video / dataviz canvas. Only
per-key novelty: GIF-icon packs, a one-button IP-camera plugin, Companion
feedbacks that recolor a key. The closest to a display framework,
[streamdeck-ui-node](https://github.com/mrfigg/streamdeck-ui-node), is 2★ and
abandoned (2023), and still per-key. Our measured ~27fps **full-panel** work has
no framework competitor.

## Agent-peripheral angle, precisely

- **Elgato MCP Deck** (7.4, ~Apr 2026): agent can only trigger pre-placed
  user-authored actions. **Cannot set arbitrary key images, cannot read arbitrary
  presses.** One-directional, trigger-only.
- Third-party MCP servers go further but lean output-heavy:
  [verygoodplugins/streamdeck-mcp](https://github.com/verygoodplugins/streamdeck-mcp)
  (22★, authors profile files); `sohumsuthar/stream-deck-mcp` (sets button
  image/color + controls Hue/Key Lights).
- **Nobody has shipped "generic macropad as a two-way MCP peripheral: agent sets
  any key, reads any press, as a reusable primitive."**

## Bottom line

No single use case is unoccupied. The defensible position is the **intersection**,
and it survives only if two things are first-class and excellent — the two nobody
owns:

1. **Panel-as-display rendering** (the true thin gap)
2. **The general two-way agent-peripheral primitive** (even Elgato's MCP won't do
   it)

The framework layer (components/state/diffing/event bus) is a real graveyard =
real opportunity, but is *not enough alone* — Companion outclasses "nicer wiring."
Homelab is a demo, not a headline. And critically — our differentiator over the
existing agent-deck apps (AgentDeck, agentsd) is **library, not app**, plus the
multi-channel ingest (see [architecture.md] once written): hooks + OTel +
statusline + MCP + OS state unified, where they wire one or two channels to a
fixed app.
