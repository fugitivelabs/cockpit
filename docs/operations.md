# Operations: running the cockpit for real

A thing that only exists while a foreground script runs is not done. This is the
lifecycle layer — what "always-on" actually requires. Design captured 2026-07-20;
most of it is NOT built yet, and the flags below say so.

## The constraint that shapes everything: one owner

Only one process can drive the USB device. macOS doesn't hard-lock it (verified),
but two writers just fight over the display. Therefore:

**The always-on daemon is the single owner and single source of truth. Every
channel is a client that talks to it over a local socket.**

- The MCP server Claude Code spawns per-session **cannot** drive the deck itself —
  it's a thin client forwarding `paint`/`ask` to the daemon.
- Hooks → HTTP POST to the daemon.
- The statusline script → writes tokens/cost to the daemon's socket.
- The daemon owns the deck, fuses everything, routes presses back.

This isn't extra complexity; it removes a wrong assumption (that the MCP
subprocess touches hardware — it can't).

## Always-on mechanism: LaunchAgent, not LaunchDaemon

A LaunchDaemon runs in the system context before login, with no GUI session. Our
session-focus is AppleScript against the user's Terminal windows, which only
exists inside the logged-in Aqua session. So it MUST be a per-user LaunchAgent
(`~/Library/LaunchAgents/local.cockpit.daemon.plist`), which also puts it
where the USB device lives in the session.

Supervision the plist provides:

- `RunAtLoad` — starts at login
- `KeepAlive` — restarts on crash; `bootout` stops it and it stays stopped
- `ThrottleInterval` — no crash-loop hammering (≥10s between spawns)
- `StandardOutPath` / `StandardErrorPath` → `~/Library/Logs/cockpit.log`

Control: `launchctl bootstrap gui/$(id -u) …plist` to load, `bootout` to stop,
`kickstart -k` to restart.

## Setting up on a new machine

Four commands. Nothing is hand-edited, and nothing has a path baked into it —
`cockpit wire` computes the statusline command from *this* checkout and *this*
interpreter, so a moved repo or a different machine is the same procedure.

```bash
brew install hidapi
cd cockpit && python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt

./launchd/cockpit link       # 'cockpit' onto $PATH
cockpit wire                 # point Claude Code at the daemon
cockpit install              # LaunchAgent: start now, and at every login
cockpit doctor               # verify device, permissions, and wiring
```

`cockpit doctor` is the acceptance test. Anything it flags carries its own fix;
the only one that can't be automated is the Accessibility grant below, and it is
needed only for answering prompts from the deck.

### What `cockpit wire` owns

It writes two things into `~/.claude/settings.json` and leaves the rest of the
file alone:

- **`statusLine`** → `fleet.statusline`, with `refreshInterval: 30`. This is
  not cosmetic: it is the **only** channel that reports a session's tty, which
  is the sole join between a hook's `session_id` and a *window*. Without it the
  board still lists sessions but never learns any hook state. The interval
  matters too — an idle session that never re-runs its statusline never
  registers its tty.
- **`hooks`** → seven events pointing at `127.0.0.1:8787`.

Properties worth relying on:

- **Idempotent.** Re-run it any time; it reconciles rather than duplicating.
- **Non-destructive to hooks.** Foreign hooks on the same events are preserved —
  only entries pointing at our port are replaced.
- **Destructive to `statusLine`, and it says so.** Claude Code supports exactly
  one, so wiring ours necessarily replaces any existing one. `cockpit wire`
  prints what it replaced, and backs the file up first. `cockpit wire --remove`
  takes our wiring back out, but cannot restore a foreign statusline — recover
  that from the backup it names.
- `cockpit wire --print` shows exactly what would be written, changing nothing.
- `cockpit wire --capture` additionally installs a **diagnostic** hook that logs
  raw payloads and touches no state. Off by default; it exists for answering
  "what does Claude Code actually send for this event?" — which is how the
  permission-prompt/question conflation was found.

Existing Claude Code sessions pick the wiring up without restarting.

## `cockpit doctor` — run this first, always

Every permission failure in this project looks identical from the outside: an
empty board, or a key that quietly does nothing. `cockpit doctor` turns that
into a list with fixes attached.

```bash
cockpit doctor
```

```
  · python (TCC identity)              /opt/homebrew/…/bin/python3.14
  ✓ Stream Deck device                 1 found
  ✓ statusline → cockpit               refreshInterval 30s
  ✓ hooks → cockpit                    5 event(s) wired

  — from this terminal —
  ✓ Automation → Terminal              9 window(s) visible
  ✓ Automation → System Events         frontmost: Terminal
  ✓ Accessibility (keystrokes)         window titles readable

  — from the daemon —
  ✓ daemon channels                    listening on :8787, 8 session(s) registered
  ✓ daemon: Automation → Terminal      9 window(s) visible
  ✓ daemon: Automation → System Events frontmost: Terminal
  ✓ daemon: Accessibility (keystrokes) window titles readable
```

**Why it reports the same three checks twice.** macOS grants permissions per
*responsible process*. A probe run from your terminal reports what **Terminal**
is allowed to do; the LaunchAgent has no Terminal parent and carries its own
grants. They can differ, and when they do, everything works by hand and fails
in the daemon — the single most confusing failure mode here. The `— from the
daemon —` block asks the daemon itself over `/doctor`, so it is the answer that
actually matters.

The daemon also exposes `/health` (is it up) and `/sessions` (the
session_id → tty → window join table, the thing Stage 2 hinges on).

## Permissions (macOS TCC)

Three distinct capabilities, three grants — verified live 2026-07-21:

| Capability | TCC type | Who needs it | Status |
|---|---|---|---|
| Frontmost **app identity** (`osint.frontmost()` name/bundle/pid) | Automation → System Events | the process running osascript | granted (foreground) |
| Generic **front-window title** (`frontmost().window_title`) | **Accessibility** | same | NOT granted → `-1719 "not allowed assistive access"`, degrades to `""` |
| **Terminal session titles** (Stage 1's dashboard source) | Automation → Terminal | same | granted |

**Stage 1 needed none of the missing ones — confirmed by shipping it.** Session
titles come from Terminal scripting (`tell application "Terminal"…`), which is
Automation-of-Terminal, not Accessibility. Accessibility only unlocks generic
(non-Terminal) window titles and, later, System Events keystroke synthesis
(Stage 3 accept/reject).

**The daemon's own grant was the open risk, and it resolved clean.** A
LaunchAgent is a different responsible process from your foreground terminal and
*cannot surface a TCC prompt*, so the worry was a silently empty dashboard under
launchd even though it worked in the foreground. It didn't happen: the first
launchd run after Stage 1 logged `dashboard up — 7 session(s) on first poll`.
If it ever does happen on another machine, the symptom is exactly that — a
permanently empty board with a healthy heartbeat — and the fix is pre-adding the
venv's python binary under Automation → Terminal. `cockpit --heartbeat` is the
discriminator: it needs no automation at all, so if the heartbeat view paints
and the dashboard doesn't, it's TCC and not the device.

### Granting Accessibility (the one you'll have to redo)

Needed only for **Stage 3 keystroke synthesis** (accept/reject) and for reading
non-Terminal window titles. Nothing in Stage 1 or 2 requires it. Status on this
machine as of 2026-07-21: **granted, for both Terminal and the daemon** —
verified via `cockpit doctor`, not assumed.

If `cockpit doctor` ever reports it missing, the fix is:

1. **Find the binary macOS actually keys on.** Not the venv symlink — TCC
   follows it to the real interpreter, and adding the symlink appears to work
   and then silently doesn't:
   ```bash
   cockpit doctor | head -2        # prints it as "python (TCC identity)"
   readlink -f .venv/bin/python
   ```
2. System Settings → **Privacy & Security** → **Accessibility** → **+**
3. `Cmd-Shift-G` in the file picker (it won't browse to `/opt` otherwise), paste
   that path, **Open**, and make sure the toggle is **on**.
4. `cockpit restart` — a running process does not pick up a new grant.
5. `cockpit doctor` to confirm the `— from the daemon —` line flipped to ✓.

**Expect to redo this.** The grant is bound to that exact binary path, so it
breaks on: a Python upgrade (`3.14.6` → anything else changes the path), or
deleting and recreating `.venv` with a different interpreter. Both look like
"accept/reject silently stopped working". `cockpit doctor` names it in one line.

**A LaunchAgent cannot prompt for TCC**, which is why this must be added by hand
rather than waiting for a dialog. And note the grant is on the *interpreter*, so
it extends to any script run by that same Python — a real (if modest) reason not
to grant more than the stage actually needs.

## Lifecycle events and honest status

| Event | Mechanism | Status |
|---|---|---|
| Crash | launchd `KeepAlive` restarts | **BUILT** — fault isolation keeps one bad key from crashing the loop (below); LaunchAgent restarts a true crash |
| Graceful stop | SIGTERM → blank → release → exit 0 | **BUILT 2026-07-21** — `Surface.run()` catches SIGTERM/SIGINT and shuts down cleanly (unit-tested) |
| Unplug / replug | `Surface` **non-blocking** reconnect | **VERIFIED 2026-07-20/21** — render + input recover; the run loop keeps ticking (heartbeat alive) while the device is out (see below) |
| Sleep / wake | handle survives — no reconnect needed | **VERIFIED 2026-07-20** — see below |
| Two instances | `SingleInstance` flock guard | **BUILT 2026-07-21** — a second cockpit is refused (unit-tested). Note: the *Elgato app* is a different writer the flock can't see — still quit it. |
| Observability | `logging` module | **BUILT 2026-07-21** — levelled, timestamped, headless-debuggable via `configure_logging()` |

### Unplug/replug — VERIFIED

Soak-tested live (`deck/soak.py`), two runs, physical cable pull:

- Disconnect detected within ~1s (the ticking clock forces a write each second,
  so a dead device surfaces as a `TransportError: No HID device` promptly).
- Reconnect loop polls `enumerate()` and reopens; reclaim after replug is a few
  seconds. Recorded "down" times of 15–18s were mostly the deliberate wait before
  replugging, not reclaim latency.
- **Input survives** — the risk I most worried about (the library's reader thread
  dying permanently on reopen) did NOT occur. Presses, including a touch point,
  registered ~1s after reconnect across both runs. `open()` re-registers the key
  callback on the fresh device object, and that path works.

The one real caveat: there is a **dead window** while unplugged plus a few seconds
of reclaim, during which presses are genuinely lost (no device to read). Watch for
it in UX — don't promise an action the device can't receive. Worth shrinking the
poll interval (currently 1.5s, `RECONNECT_POLL_S`) to speed reclaim.

**Reconnect is now non-blocking (2026-07-21).** The first live LaunchAgent run
surfaced the reason: a ~15-min unplug left the daemon alive but with a **frozen
run loop** — the old reconnect *blocked* inside `flush()` polling for the device,
so the heartbeat went stale and `cockpit status` couldn't tell "dead" from "alive,
device-out". Recovery still worked (reconnected, same pid, on replug), but a
blocking reconnect is wrong for an always-on daemon that will also serve hooks /
statusline / a socket — it would freeze all of that too. Fixed: `_handle_disconnect`
now just marks the surface down and returns; the run loop (and any writer, via
`flush()`) retries `open()` on the `RECONNECT_POLL_S` cadence while continuing to
tick. So the heartbeat stays fresh and channels stay served throughout an unplug.
Transition hooks `on_disconnect()` / `on_reconnect()` let a consumer narrate or
react (used by `deck/soak.py`). Recovery path itself is unchanged — `open()` still
re-registers the key callback and repaints, which is what made input survive.

### Sleep/wake — VERIFIED, and the opposite of what was expected

Soak-tested live: harness running, lid closed ~3.5 min, reopened.

**The device handle survives sleep entirely — 0 disconnects, 0 reconnects.** The
process is suspended and resumes exactly where it left off; the next write after
wake just succeeds. The clock returned instantly on wake (no reconnect gap,
because nothing was lost), and presses registered immediately after. Sleep and
unplug are fundamentally different: unplug kills the handle and needs reconnect;
normal lid-close sleep does not touch it.

Mechanism note: `time.monotonic()` pauses during system sleep (only ~16s of "up"
time elapsed across ~3.5 min of real sleep), so any monotonic-based timers resume
seamlessly rather than firing a backlog. This also means the harness's 300s
self-stop counts awake time only — fine.

So **no `pyobjc` wake-notification dependency is needed.** The earlier worry (that
the reader thread would die on sleep and need failure-driven reconnect) does not
apply to normal sleep at all.

Remaining caveat (untested): deeper/longer sleep — hibernation, or macOS powering
down the USB bus after extended sleep — could still invalidate the handle. If that
ever happens, the unplug reconnect path (verified) is the safety net that catches
it. So even the untested case degrades to an already-proven recovery.

## Library gaps to close (use-case-agnostic, belong in `deck/`)

1. **Per-component fault isolation** — **BUILT.** A component whose `render()`
   raises now yields a visible `ERR` tile for that one key (`View.slots()` in
   `app.py`), and a raising press handler is swallowed. `flush()` additionally
   splits rasterisation (isolate a bad slot → error tile) from transport (a dead
   device → reconnect), so a malformed `Slot` can no longer masquerade as a
   disconnect and trigger a phantom reconnect loop.
2. **Signal handling + graceful shutdown** — **BUILT.** `Surface.run()` installs
   SIGTERM/SIGINT handlers (main-thread only; restores prior handlers on exit),
   and on a signalled stop blanks the deck, releases the device, and returns.
3. **Single-instance guard** — **BUILT.** `deck.lifecycle.SingleInstance` holds an
   exclusive `flock`; the kernel drops it on exit (even SIGKILL), so no stale
   lock survives a crash. `AlreadyRunning` on a second start.
4. **Structured logging** — **BUILT.** `logging.getLogger(__name__)` throughout;
   `deck.lifecycle.configure_logging(level, logfile, stream)` is the consumer's
   opt-in (idempotent, timestamped). The library itself only ships a NullHandler.
5. **Harden + soak-test reconnect / sleep-wake.** — still the honest gap; see below.

All of 1–4 are covered by `tests/test_lifecycle.py` (37 headless assertions,
including a fake-deck exercise of the flush rasterise-vs-transport split and of
the non-blocking reconnect).

## The honesty about #5

Reconnect and sleep/wake **cannot be validated by a unit test.** The only real
proof is installing the LaunchAgent and living with it for days — sleeping the
Mac, yanking the cable, reading the logs. That is soak time, not a green check.

So the truthful sequence is: build the primitives (1–4), ship the LaunchAgent,
then *run it for real* before the operational layer is called done. "Done" here
is earned by uptime, not by tests passing. **Primitives 1–4 and the LaunchAgent
are now built (2026-07-21); the soak has not yet happened — that is Grant's to
run, and until it does this layer is not "done."**

## Daemon/packaging layer (on top of the library primitives)

Built 2026-07-21:

- **The cockpit daemon** — `cockpit/daemon.py`, the always-on process that owns
  the deck. It composes all four primitives: single-instance guard →
  `configure_logging` → wait-for-device → open → `App.run()` (signals + graceful
  release). Since Stage 1 it runs the real session dashboard; the heartbeat view
  survives behind `--heartbeat` as a device-only fallback that touches no other
  app. Run it: `PYTHONPATH=. ./.venv/bin/python -m cockpit`.

  Two daemon-shaped constraints the dashboard has to respect, both the same
  lesson as the non-blocking reconnect: **session polling and press handling run
  on their own threads**, because each is an osascript round-trip that can block
  for seconds and the run loop must keep ticking. And the dashboard rebuilds its
  tiles only when a content signature changes, so an idle board costs one tuple
  compare per tick.
- **The LaunchAgent** — `launchd/local.cockpit.daemon.plist.template`
  plus `install.sh` (renders the template with this machine's venv/repo paths
  and bootstraps) and `uninstall.sh` (bootout + remove). `KeepAlive` is
  `{SuccessfulExit: false}`: a graceful SIGTERM exit(0) stays stopped after
  `bootout`, a crash or absent-device exit(1) is retried, `ThrottleInterval` 10s.
- **Heartbeat** — the daemon logs `heartbeat — up Ns` every 15s and touches
  `~/Library/Logs/cockpit.heartbeat`, so liveness is checkable
  without the device.

Still to come (later stages): the local socket the channels (MCP client, hooks,
statusline) connect to, and the Claude Code plugin that installs hooks + MCP +
statusline pointing at it (see [architecture.md](architecture.md)).

### Managing the daemon — `launchd/cockpit`

One verb-based tool wraps every launchctl incantation, so start/stop/restart/
update never means hand-typing `bootstrap`/`bootout`/`kickstart`. `cockpit link`
symlinks it into `~/.local/bin` (on PATH) so you can type `cockpit` from
anywhere — the script still lives in and runs from the repo, so edits to it take
effect immediately (the symlink is resolved back to the checkout at run time):

```bash
cd cockpit
./launchd/cockpit link        # -> ~/.local/bin/cockpit; now `cockpit …` works anywhere
cockpit install               # render plist for this machine, load, start (idempotent)
cockpit status                # loaded? pid? last exit? heartbeat age?
cockpit logs                  # tail the structured log (live)
cockpit stop                  # graceful SIGTERM; stays down (blanks the deck cleanly)
cockpit start                 # bring it back
cockpit uninstall             # bootout + remove (logs kept)
cockpit unlink                # remove the PATH symlink
```

**The update model** — two tiers, because launchd runs `python -m cockpit` from
this repo with `PYTHONPATH=$REPO`, and Python re-imports on each process start:

- **Code change** (`deck/*.py`, `cockpit/*.py`) → `cockpit restart`. Graceful
  SIGTERM on the old process (deck blanks, device releases), launchd spawns a
  fresh one that re-imports the edited code. Brief (~1–2s) blank, no re-install.
- **Anything else** (the plist's args/paths/KeepAlive, or the repo moved) →
  `cockpit update`. It re-renders the plist, and *only if it actually
  changed* boots out + bootstraps the new definition; otherwise it's just a
  code restart. Safe to run after any edit — it figures out which tier applies.
- **Dependency change** (`requirements.txt`) → `pip install -r requirements.txt`
  into `.venv` first, then `cockpit restart` (update reminds you of this).

`cockpit foreground` is the dev loop: it stops the managed instance so the
device is free, then runs the daemon in your terminal with `--debug`; Ctrl-C and
`cockpit start` hands it back to launchd. The single-instance guard makes all
of this safe — a restart race can never put two writers on the device; the loser
exits and launchd retries.

`install.sh` / `uninstall.sh` still exist as thin wrappers over `cockpit` for
muscle memory. The soak itself: install, then live with it — sleep the Mac, yank
the cable, force a crash — and read `~/Library/Logs/cockpit.log`.

## Recurring checks

The only two items worth keeping from the pre-daemon maintenance notes; the
rest were about Elgato's app, its profile schema, and its plugin SDK, none of
which this uses.

- **After any macOS major update, run `cockpit doctor`.** macOS has a history of
  silently revoking Accessibility across major versions, and Accessibility is
  now load-bearing: without it the deck can still show you which session needs
  you, but it cannot read a prompt or answer one. The failure is silent by
  nature — answer keys simply stop appearing.
- **Periodically ask which keys actually get pressed.** There are eight. Dead
  weight is expensive on a surface this small, and the honest answer may be that
  some of this belongs on the keyboard after all. `cockpit logs` records every
  press, so this is answerable from data rather than impression.
