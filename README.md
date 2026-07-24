---
name: cockpit
type: domain
status: active
owner: grant
related: [house, fugitive-labs]
last-reviewed: 2026-07-22
---

# cockpit

A physical control surface for AI coding-agent sessions. An Elgato Stream Deck
Neo shows every running Claude Code session — which one is working, which one
needs you — and puts you in it with one press. When a session is holding a
permission prompt, the deck shows that prompt's **actual options** and can
answer it.

Built as a self-hosted Python stack talking straight to the USB device. **The
Elgato app is not used**, and none of its concepts apply — no profiles, no
plugin SDK, no Marketplace.

> Not the Red Hat [Cockpit](https://cockpit-project.org/). Same word, different
> universe; we will never be the bigger one and that's fine.

## Why

With nine agent sessions running, the scarce resource isn't screen space or
keystrokes — it's knowing **which one needs you right now**. This is an
attention assistant, not a window switcher: it earns its place if you glance
over and learn something, even when you press nothing.

## Quickstart

```bash
brew install hidapi
python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt

./launchd/cockpit link      # put `cockpit` on $PATH
cockpit wire                # point Claude Code at the daemon
cockpit install             # run it now, and at every login
cockpit doctor              # verify device, permissions, wiring
```

`cockpit doctor` is the acceptance test — it checks the device, both TCC
permission families, and the Claude Code wiring, **from inside the daemon as
well as from your shell**, because macOS grants those differently. Anything it
flags carries its own fix.

Quit Elgato's app if it's installed: two writers fight over the display.

## Layout

```
src/deck/       the device library — diffing, caching, input, reconnect,
                lifecycle. Knows nothing about Claude. Shareable on its own.
src/fleet/      the session library — discover the agent sessions running on
                this machine, fuse their state, go to one. Knows nothing about
                Stream Decks. Shareable on its own.
src/cockpit/    the glue — the dashboard, the palette, press routing, the daemon.
tests/          624 assertions, no hardware required
launchd/        the LaunchAgent and the `cockpit` management CLI
docs/           architecture, operations, prompts, roadmap
```

Two libraries and a thin consumer, and the shape is load-bearing rather than
cosmetic. `deck/` never learns what a session is; `fleet/` never learns what a
pixel is; the dependency only ever runs `cockpit → {deck, fleet}` and never
between the two or backwards. That is what makes another agent CLI an adapter
inside `fleet/` rather than a rewrite, and another *surface* for `fleet/` — a
TUI, a menubar — a different consumer rather than a fork.

## Docs

- **[docs/architecture.md](docs/architecture.md)** — start here. The channels
  (statusline, hooks, OS polling), the adapter seam, and why the statusline is
  structural rather than decorative.
- **[docs/operations.md](docs/operations.md)** — running it for real:
  new-machine setup, `cockpit doctor`, macOS permissions, the LaunchAgent,
  recurring checks.
- **[docs/prompts.md](docs/prompts.md)** — **read before touching the answer
  keys.** What the deck may answer, what it must not, and the traps behind both.
- **[docs/roadmap.md](docs/roadmap.md)** — current status and what's next.
- [docs/design.md](docs/design.md) — the attention-assistant thesis and the
  constraints that shaped the layout.
- [docs/reference/](docs/reference/) — hardware notes, the self-hosting survey,
  the competitive read, and the Firefox tab notes.
- [src/deck/README.md](src/deck/README.md) — the library API and performance.

## Scope

**In:** the Neo over raw HID, the `deck/` library, the always-on daemon, and the
Claude Code channels that feed it.

**Out:** Elgato's app and ecosystem. Streaming/broadcast use. Smart-home control
lives in [house](../house/) — a key may *trigger* an automation, but the logic
stays there.
