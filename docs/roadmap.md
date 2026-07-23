# Roadmap

Architecture is settled — bespoke Python daemon, direct HID, no Elgato app. See
[design.md](design.md) and [selfhosted.md](reference/selfhosted.md).

**Consequence of that choice:** there is no longer a "zero-code stage." Without
the Elgato app there is no GUI to click Hotkey actions into, so everything is code
from day one. The compensation is that the first useful build is genuinely small.

## Stage 0 — the library — DONE

[deck/](../src/deck/) exists and works, in two tiers, both use-case-agnostic:

- **Surface tier:** declarative slots with diffing, image caching, debounced
  input, long-press, reconnect, text fitting, panel tiling, pre-encoded animation.
- **Component tier:** `Component` / `Static` / `Button` / `Live` / `meter`,
  `View` / `PagedView` (touch-point paging), and an `App` runner. The foundational
  framework layer — built now because retrofitting callers is the expensive part.
  Deliberately deferred as additive-not-foundational: remote/socket drive,
  transitions, arbitrary per-key drawing.

Verified 24/24 headless + live on hardware. None of it can pigeonhole the
decisions below.

Hardware fully characterised — see [deck/README.md](../src/deck/README.md) and the
verification table in [selfhosted.md](reference/selfhosted.md).

Verified on hardware (2026-07-20): long-press, debounce, unplug/replug recovery,
sleep/wake (handle survives). See [operations.md](operations.md).

Still unverified (lower stakes): sustained multi-hour operation; brightness range;
thermal behaviour; deep/hibernation sleep (would present as an unplug, and the
unplug path is proven).

## Stage 0.5 — lifecycle primitives + always-on — BUILT (awaiting soak)

The mechanical, self-verifiable operational layer. Details and rationale in
[operations.md](operations.md). Four library primitives, then packaging — all
built 2026-07-21, 37 headless assertions in [tests/test_lifecycle.py](../tests/):

1. **Per-component fault isolation** — ✅ a raising `render()` yields an `ERR`
   tile for that key (`View.slots()`); `flush()` isolates a bad slot from a real
   disconnect so a malformed `Slot` can't trigger a phantom reconnect.
2. **SIGTERM graceful shutdown** — ✅ `Surface.run()` catches SIGTERM/SIGINT
   (main-thread, restores prior handlers), blanks the deck, releases, returns.
3. **Single-instance guard** — ✅ `SingleInstance` flock; kernel drops it on
   exit even under SIGKILL, so no stale lock survives a crash.
4. **Structured logging** — ✅ `logging` throughout + `configure_logging()`
   opt-in; library ships only a NullHandler.
5. **LaunchAgent** — ✅ `launchd/*.plist.template` + `install.sh`/`uninstall.sh`,
   per-user (NOT a LaunchDaemon — it lacks the GUI session AppleScript needs),
   `RunAtLoad` / `KeepAlive={SuccessfulExit:false}` / `ThrottleInterval` 10s,
   logs to `~/Library/Logs/`. Target is `cockpit/daemon.py`, the always-on
   daemon skeleton (placeholder heartbeat view; Stage 1 swaps in the dashboard).

None of 1–5 needed hardware-in-the-loop to build; the risky physical unknowns
were already retired. **What remains is not code but uptime:** install the
LaunchAgent and live with it (sleep, unplug, crash, read the log) before this
layer is called done — see [operations.md](operations.md). That soak is the
gate into Stage 1, and it's Grant's to run.

## Stage 1 — read-only session dashboard — BUILT (2026-07-21)

The whole thesis, with none of the hard problems: one key per Claude Code
session, colored by state, press to jump to that window. Live under launchd,
74 headless assertions in [tests/test_sessions.py](../tests/).

Deliberately excluded: no hooks, no accept/reject, no keystroke synthesis. Which
means it sidesteps state staleness, prompt-shape detection, and the wrong-session
hazard entirely — every one of the known-hard problems is downstream of *sending*
input, and this stage only reads.

**The three modules, which are the adapter seam made real:**

- `fleet/sessions.py` — the normalized `Session`, the `Adapter` protocol, and
  the ordering + labeling rules. Pure; no I/O, no device.
- `fleet/adapters/claude_code.py` — adapter #1. Terminal titles in, Sessions out, plus
  `focus()`. Everything Claude-specific about discovery is in this one file.
- `cockpit/dashboard.py` — `SessionTile` / `SessionPoller` / `Dashboard`. The
  daemon's `build_view()` placeholder is gone; `--heartbeat` keeps the Stage 0.5
  view as a device-only fallback that needs no Terminal automation.

**The label problem is solved.** Three of nine live sessions were all `Projects`.
The rule (Grant's call): **keep the cwd where it is unique, fall back to the
task's leading words only for the sessions that actually collide** — so
`peregrine` / `docland` / `provenance` stay recognizable and only the colliding
tiles pay, with the cwd demoted to the subtitle. Live result:

    key0 [working] streamdeck        key3 [idle] corpus migration / Projects
    key1 [idle]    docland           key5 [idle] recover          / Projects

**Ordering: blocked floats to the top** (Grant's call, against the doc's earlier
lean toward fixed slots) — urgency goes where the eye lands first. The cost, a
tile moving under your finger, is bounded by a stable tiebreak: within a state,
order is by *window id*, never by Terminal's enumeration order, which reshuffles
front-to-back every time you switch windows.

**Two things learned building it, both about not blocking:**

- Polling and press-handling both run on their own threads. `render()` is
  contracted cheap and pure, and osascript can block for seconds — inline it and
  the deck freezes, which is precisely the failure the non-blocking reconnect
  work fixed.
- **The `tab` trap.** Inside `tell application "Terminal"`, the AppleScript
  `tab` keyword resolves to Terminal's own *tab class* and silently coerces to
  the literal string `"tab"` — every line came back `53025tabstreamdeck — …`,
  with rc 0 and empty stderr. Use `(character id 9)`. `osint.py` uses `tab`
  safely only because it tells System Events, which has no tab noun.

**What is not yet earned:** a real day of use. The dashboard is up and correct,
but whether it beats a glance at the Dock is a question uptime answers, not a
test. Same gate as Stage 0.5.

Still open, deliberately deferred until use says they bite: user-assigned
nicknames (pinning), and telemetry on the tile (a context-% bar) once the
statusline channel exists.

## Stage 2 — state awareness — BUILT (2026-07-21), live

Still read-only, still no keystrokes. The deck became a notifier — most of the
attention-assistant value with no risk of acting on the wrong session.

**Why it could not be faked.** Stage 1 tried to infer "needs you" from a
working→idle transition. That conflates *finished a turn* with *blocked on you*,
and `✳` cannot tell them apart — it means "not spinning" and nothing more. The
inference was deleted. Real attention needs the model to say so, which is hooks.

**The join, which was the actual hard part.** Hook payloads carry `session_id`
and `cwd` but **no terminal identity** (verified: hooks run with no controlling
terminal). And `cwd` can't disambiguate — three sessions are all `Projects`. The
statusline is the only channel that runs as a *process inside the session*, so
it alone can report a tty:

    hook       -> session_id            (who needs you)
    statusline -> session_id + tty      (the join)
    Terminal   -> tty + window id       (which tile)

So the statusline is **structural, not a telemetry nicety** — without it a hook
event cannot reach a tile at all. Two traps found live:

- **Claude Code spawns children with no controlling terminal.** `ps -o tty=` on
  the statusline process returns `??`; only the `claude` process carries the
  tty. `own_tty()` walks the ancestor chain (one `ps`, in-memory walk).
- **An idle session never re-runs its statusline**, so its tty never registers
  and hooks for it land nowhere. Fixed with `refreshInterval: 30`.

**Components:** `fleet/registry.py` (channel state + `fuse_state`),
`fleet/listener.py` (loopback HTTP endpoint), `fleet/statusline.py` (the
statusline command), tty join in `claude_code.py`. 59 assertions in
[tests/test_channels.py](../tests/).

**The safety property, deliberate:** a hook's HTTP response *can* carry a
permission decision. This daemon always replies bare `200 {}` — it is built so
it cannot express approval at all. A dashboard that could accidentally approve
a tool call is far worse than one showing a wrong color. If the daemon is down,
Claude Code treats it as a non-blocking error and carries on.

Verified live: `permission_prompt` → red, `Stop` → clear, telemetry flowing
(context % and cost per session). **Not yet observed:** `agent_needs_input`
(the question-tool case → blue) — it needs a question asked with hooks
installed. Subagent semantics deliberately unaddressed; payloads carry
`agent_id`/`agent_type` when an event comes from one.

Wired via `~/.claude/settings.json` (backed up alongside). Packaging it as a
Claude Code plugin is deferred until the shape stops moving.

## Stage 3 — acting — BUILT (2026-07-22), answering real prompts

The deck answers permission prompts. Verified live: two presses, two approvals,
no stray keystrokes.

**It is not the accept/always/reject bar this doc planned, and that plan was
dangerous.** Three real prompts, captured rather than assumed:

    Bash outside project   1. Yes                              2. No
    Write outside project  1. Yes  2. Yes, +allow settings…    3. No
    WebFetch               1. Yes  2. Yes, +don't ask again…   3. No

Option 2 is **"No"** on one and **a permanent permission grant** on another, and
the count varies. A fixed bar sends the same digit to both.

**So the keys are read off the screen instead** — see the superseded-shape note
in [design.md](design.md). `fleet/macos/axread.py` reads the window's visible text
over the Accessibility API and parses the actual options; the keys are labelled
with the screen's own words. No menu on screen → no answer keys, which is what
makes the free-text "tell Claude what to do differently" follow-up safe.

**The guard is the screen, not a flag.** On press: re-read the window, require
the identical option list, re-check the front window id — then send. Three
refusals are unit-tested (menu gone, menu changed, focus moved).

**Two bugs the first live test found, both invisible without it:**

1. **Scoping by window *title* silently never matched.** Claude Code titles
   carry the live spinner glyph, which changes several times a second, so the
   cached title almost never equalled the current one — every key refused with
   "no menu on screen" while the menu was plainly there. Scoping is now by
   window **id**, read fresh from Terminal.
2. **Escape was sent as `keystroke "\x1b"`**, which System Events ignores; it
   needs `key code 53`.

And a process lesson: `keystroke()` discarded its exit status, so a key that
sent nothing looked identical to one that worked. It logs failures now.

Not built, deliberately: a fixed "always" key. Option 2's meaning is not stable
across prompts, so it cannot be expressed correctly without reading the screen —
and if we're reading the screen, the real label is right there.

## Stage 4 — the rest

- Firefox: the [mozeidon](https://github.com/egovelox/mozeidon) evaluation from
  [firefox-tabs.md](reference/firefox-tabs.md). Note this assumed a Stream Deck *plugin*
  consuming it; with a bespoke daemon we'd talk to the native host directly, which
  is if anything simpler.
- Info bar as an aggregate display ("2 waiting"). Full-panel repaints only, which
  suits a low-frequency summary fine.
- Touch points for paging, if the ten-sessions-into-six-slots problem still bites.
- An MCP server exposed *by our daemon*, which would be strictly better than
  Elgato's MCP Deck — we'd choose the tools rather than pre-placing 32 buttons.

## Open decisions

- ~~**Ordering: priority vs fixed slots.**~~ **Decided 2026-07-21: blocked floats
  to the top**, with a stable window-id tiebreak underneath. See Stage 1.
- ~~**Overflow: ten sessions, six slots.**~~ **Decided: touch-point paging**, and
  it costs nothing until it's needed — the touch points only light when there is
  a second page. Pinning stays available if paging turns out to annoy.
- ~~**Labels are ambiguous.**~~ **Decided: cwd where unique, task head where it
  collides.** See Stage 1.
- How much of the Claude Code workflow belongs on hardware at all. Now answerable
  for real — Stage 1 is on the desk. A real possible answer is still "less than
  we thought," and Stage 2 shouldn't start until this one has been lived with.
