# Design: the attention assistant

Settled 2026-07-20, in conversation. The renderer backend is still open pending
open-source research; everything above that line is decided.

## Thesis

**This is not a navigation assistant. It is an attention assistant.**

The deck's job is to answer "which of my ten Claude Code sessions needs me right
now, and let me act on it in one press." Not to be a faster way to cycle windows.

The distinction matters because it inverts the design. A navigation device is
judged on press-count and speed. An attention device is judged on **glance-value**
— it is working if you look over and learn something, even if you press nothing.
That argues for information density over action coverage.

## What was rejected, and why

The original sketch was: top row = prev/next window, prev/next tab; bottom row =
contextual Claude Code keys + an app switcher.

**Prev/next cycling was cut.** Those are already `Cmd+~` and `Ctrl+Tab` — keys the
hands are already on. Reaching to a device a foot away, looking down, pressing, and
returning is slower than the shortcut it replaces. With ten windows, cycling is
O(n) besides. It spent four of eight keys making an existing shortcut worse.

**Prev/next tab was cut on evidence.** Observed state: ten Terminal windows, one
tab each. Tabs aren't part of this workflow.

**Up/down arrows for select-menus were cut.** Same critique (arrows are already
arrows), plus hooks cannot detect that prompt shape — see below.

The deeper reason: a Stream Deck's one advantage over a keyboard is that it has
**eight little screens**, so it can offer labeled direct access to a named target.
Rendering arrows on them spends the only differentiating capability.

## The shape

Observed live via AppleScript — every Terminal window is a Claude Code session,
already labeled with its task, with a state glyph in the title:

```
win 50831  ⠐ Set up Elgato Streamer Neo with MCP integrations
win 50245  ✳ Implement Peregrine model IR with concrete type definitions
win 31552  ✳ Transcribe 1804 Wake County deed across two pages
...ten total
```

So: **top six keys are a live session dashboard.** One key per session, labeled
with its title, colored by state. Press to jump straight to that window. Bottom
row keeps contextual accept/reject plus an app-switch key.

## Key repainting, not profile switching

Three mechanisms exist for changing what the deck shows:

1. **Smart Profiles** — the app swaps the whole profile on frontmost-app change
2. **Plugin-driven profile switch** — `switchToProfile()`, limited to profiles the
   plugin itself bundles
3. **Key repainting** — one profile, `setImage`/`setTitle` per key at runtime

**Decision: repainting.** The argument that settles it: Smart Profiles switch on
frontmost *application*, and all ten sessions are the same application. Smart
Profiles cannot distinguish the deed-transcription session from the HVAC session,
and cannot express "the session you're looking at is blocked." The signal is
structurally coarser than the thing we care about.

Repainting also avoids the editor-window gotcha and the Accessibility-revocation
breakage that Smart Profiles carry.

**Trap:** a custom icon or title set in the GUI permanently overrides plugin
`setImage`/`setTitle`. Keys we repaint must be left uncustomized.

## Designing around the three known limitations

### Stale state — hooks for edges, polling for truth

Claude Code's `Notification` hook with matcher `permission_prompt` fires exactly
when a session blocks, carrying `session_id` and `cwd`. But **there is no
"prompt answered" event** — un-blocking has to be inferred from `PostToolUse` or
`PermissionDenied`, which is a proxy, not a signal.

Resolution: hooks give the precise blocking event; a 1–2s poll of window titles
gives ground truth about now. If a session's glyph flipped back to the working
spinner, it isn't blocked regardless of what the last hook said. Neither source
is sufficient alone. The poll is one AppleScript call for all windows, so it's cheap.

### Prompt shape — SUPERSEDED 2026-07-21: read it, don't guess

The original rule stands as a rule about *hooks*, and it was right: hooks carry
no UI-shape metadata, so a key labeled "Yes" that sends `1` into the wrong menu
does something unpredictable to a session you can't see.

**Live evidence of exactly how bad the guess would have been.** Three prompts,
captured from real sessions:

    Bash outside project   1. Yes                              2. No
    Write outside project  1. Yes  2. Yes, +allow settings…    3. No
    WebFetch               1. Yes  2. Yes, +don't ask again…   3. No

Option **2 is "No" on the first and a permanent permission grant on the third**,
and the option count itself varies. A fixed accept/always/reject bar sends the
same digit to both. This was one build session away from shipping.

**What changed: the escape hatch works, via the other API.** Terminal's own
AppleScript `contents of tab` does type-error, exactly as recorded — but the
**Accessibility API** reads the visible text fine (`AXStringForRange` on the
text area's visible range). That is a different mechanism entirely, and the
earlier note conflated "Terminal scripting can't" with "it can't be done".

So the rule is now: **read the options off the screen and label the keys with
them.** Nothing is inferred, the deck shows what the screen shows, and a screen
with no numbered options (e.g. the free-text "tell Claude what to do
differently" follow-up) produces no answer keys at all. See
[axread.py](../src/fleet/macos/axread.py); the press-time guard re-reads and
requires the identical menu to still be there.

Still true, and still the reason for the conservative parser: a wrong guess is
worse than showing nothing.

### The prompt taxonomy

Moved to **[prompts.md](prompts.md)** — it grew into the most safety-critical
document here, and burying it inside a design rationale was the wrong place for
knowledge you must read before touching the answer keys.

## The colour language — decided 2026-07-22

Settled in conversation with Grant, after the first redesign pass proposed
giving up colour-for-status entirely and was rejected. Colour carries status;
the fix was to stop *everything else* borrowing the status palette.

### What was actually wrong

Not "the colours were similar" — they were the same literals in three files:

| hex pair | meant |
|---|---|
| `("#0E2A16", "#4CD964")` | `STYLE["working"]` **and** `ANSWER_YES` |
| `("#3A0A0A", "#FF6B6B")` | `STYLE["blocked"]`, `ANSWER_NO`, **and** `error_slot()` |
| `#3FA7D6` | the `waiting` accent, `Slot.bar_color`'s default, `meter()`'s default |

So red meant *a session is blocked*, *No*, and *the renderer crashed*, all
visible simultaneously — green meant *working* on the top row and *Yes* on the
bottom row. Colour cannot carry status while three unrelated things wear the
same coat. Everything now lives in [palette.py](../src/cockpit/palette.py), and
`tests/test_visual.py` asserts the invariants, because this is drift that no
type can catch.

### The rule

**A hue means one thing, deck-wide.**

    red      warning    a session has stopped and cannot continue without you
    amber    caution    wants attention, but is not blocking
    green    go         affirmative — the answer bar only, never a session state
    blue     advisory   in motion; nothing for you to do
    grey     inert      idle, declined, disabled, furniture

Two consequences are the point rather than side effects:

**`working` is blue, not green.** Green for "Yes" is the most over-learned
mapping in computing, and green-for-working was a convention this project
invented. When they collide, the invented one yields — so the answer bar keeps
green and the board gives it up.

**"No" is a bright neutral, not red.** Declining a permission prompt is always
the safe move. Alarm-colouring it both misspends the deck's scarcest signal and
nudges toward approving, on the one row that types into a live session — see
[prompts.md](prompts.md) for why that nudge is the wrong direction.

**Warm means act; cool means ignore.** blocked/waiting are warm, working/idle
cool. That is a second, coarser read that survives peripheral vision and
colour-blindness — you can tell whether the board wants anything without
resolving a single hue. The state is *also* spelled out on the tile, because
colour alone is not a label.

This is the aviation annunciator convention (red warning, amber caution, blue
advisory), which is well-trodden and, given what this project is called, hard to
argue with.

### Focus is mass at the bottom, not an outline

Two pixels was invisible; four was still "a thin white line" on real glass. The
bezel and the viewing angle eat any hairline, and a saturated field crowds it
further. So focus is **mass**: a solid white bar thick enough to read as a block.

It sits at the **bottom**. A top cap was tried first and worked visually, but it
pushed the project name down by its own height — so the title sat at one of two
heights depending on which session you were in, and the row read as disjointed.
The label is anchored to the top of the tile, so a bottom band buys the same
white for free. No frame either, for the same reason: an inset perimeter shifts
the text sideways by its own width. **Focus must be addable and removable
without anything else on the tile twitching**, which is now a test —
`test_visual.py` renders a tile focused and unfocused and asserts the entire
text area is pixel-identical.

That is what `foot` exists for in `Slot`: the mirror of `rule`, for markers that
must not disturb the type. The context meter stacks above it rather than under
it, so the two never overdraw.

Related, and a genuine bug: `pulse` used to dim *all* chrome, so a white focus
frame turned grey whenever the tile under it happened to breathe — the one tile
you were looking at got the weakest indicator. Pulse now moves the **field
only**. Edges carry structure, and structure answers a different question than
whatever is animating.

### Focus stopped being a tint

`FOCUS_LIFT = 0.30` lightened the focused tile toward white, which desaturates:
blocked's field landed on `#755353`, a muted mauve, and working's on `#566A5C`, a
grey-green. The tile you were looking at was the one whose status was hardest to
read — a direct tax on the thing colour is for. Focus is now a white perimeter,
so hue stays at full strength and the two questions ("which needs me" vs "which
am I in") stop competing.

### A quiet deck is one surface

`idle` tiles and action-bar keys drifted to different darks — `#2C323B` against
`#0E0F12`, more than three times the luminance — so a board with nothing
happening read as two stacked shades of grey, carrying no meaning. They now
share one `QUIET` field.

The board and the controls are already told apart by **structure** (left-aligned
project-over-task versus a centred value over a small-caps caption). Brightness
saying it a second time, badly, was just inconsistency. And a uniform quiet
surface is what makes a single coloured tile shout.

### Role is carried by structure, not colour

A session tile, an inert info key, and a key that types `2` into a live session
used to be the same object in different colours. Now they are different shapes:

- **session tile** — top rule whose thickness scales with urgency, name, state
- **action key** — no rule, value centred over a small-caps caption: furniture
- **answer key** — a full perimeter, which nothing else on the deck has

That last one matters most. Role should be legible without reading and without
depending on hue: the perimeter says *this key types into a session*, whatever
colour it happens to be.

### Motion — two tiers, and opt-in per state

**Revised 2026-07-22 after living with it: `blocked` does not move.** A breathing
red tile is intolerable to sit beside for a working day, and once the field is a
saturated flood the colour is already doing the attention-getting the pulse was
there for. Loud and still beats loud and moving. `waiting` still breathes — it is
the quieter warm state and the one most easily missed.

That makes motion a per-state flag (`StateStyle.breathes` / `.flashes`) rather
than something `needs_you` implies. The machinery is untouched and re-enabling it
is a one-word change in the palette. `animating()` reports only genuine movement,
so a static state does not speed the loop up either — switching it off is a real
saving, not just a cosmetic one.

Only warm states may move. If everything animates, motion stops meaning anything.

- **Onset** — a decaying flash on the *transition into* a needs-you state.
  "This just happened."
- **Sustain** — a slow breathe for as long as it stays there. "This is still
  true."

Both, because neither covers the other: a flash alone is missed if you were not
looking, and a sustain alone is wallpaper by the end of the first day. A session
already blocked when the daemon starts does **not** flash — a restart is not the
same event as a session blocking.

The mechanism is generic and lives in [anim.py](../src/deck/anim.py). The trick
that makes it affordable is **quantization**: values snap to `STEPS` buckets, so
a breathing key resolves to ~10 distinct `Slot`s — ten cached images, then a
100% cache hit rate forever — and `flush()`'s diff still suppresses the write
within a bucket. Measured: `render()` is 4.7 us, all eight keys re-render in
25 us per tick, 0.03% of a core at the fast tick. Without quantization every
frame would be an unseen slot paying a full ~1.3 ms rasterise plus a USB write.

`App` ticks fast only while the view reports `animating()`, and `CockpitView`
answers for *visible* components only — a blocked session two pages away costs
nothing.

### Where the deck/cockpit line holds

Grant asked for as much as possible at the library level, and most of it is
there: `deck/color.py` (colour arithmetic), `deck/anim.py` (phase, easing,
quantization), and the `Slot` drawing vocabulary — rule, frame, tracked small
caps, hairline meter, pulse.

**What deliberately stayed in `cockpit/`: the meanings.** `deck/` must never
learn that red means blocked, or that blocked breathes. It knows how to move a
colour toward white and how to make a number wobble; what any of it *signifies*
is the consumer's business. That is the split
[architecture.md](architecture.md) calls load-bearing, and a palette in the
library would be the first thing to break it.

### The info bar reports, or it quotes

Revised 2026-07-22. It used to shout `2 NEEDS YOU` in red across the full width
whenever anything wanted attention. That is gone: the coloured chips already
carry the tally, the board itself is unmissable, and a full-width alarm for
something two other channels are already saying is noise.

So the bar has two modes:

- **calm** — `4 sessions`, plus a chip per non-zero state in that state's hue.
- **being asked** — the screen's own words for what is about to happen, lifted
  verbatim: `Fetch https://example.com`, `Create ~/.claude/probe.txt`. That tells
  you what you are approving in a way no count or colour can, and it is the one
  moment the bar has something better to say than how many sessions exist.

While quoting, the tally drops to the needs-you states only. At 248 px the chips
compete with the headline for the only line that can hold a sentence, and the
calm states are the ones you would not act on anyway.

`Prompt.subject` (axread.py) carries it. Two properties matter and both are
tested: capturing it **cannot change whether a menu is recognised** — it is read
after the fact and can only ever be `""` — and it is **excluded from the
press-time guard**, which still compares `options` and nothing else. Letting a
cosmetic redraw of the context line veto a still-valid answer would be a
regression in exactly the direction [prompts.md](prompts.md) warns about.

### Also fixed on the way past

`_FONT_PATHS` listed `/System/Library/Fonts/SFNSDisplay.ttf` first — a file that
has not shipped on macOS for several releases — so every lookup fell through to
`Helvetica.ttc` **index 0, Regular**. The deck had no bold on it at all. Faces
inside a `.ttc` need an explicit index; asking without one silently gets you
Regular. Fonts are now named roles with fallback chains, and the workhorse is a
condensed face, because a 96 px key is width-bound and condensed buys several
points of size on the same string.

## Open questions

- **Ordering: priority vs fixed slots.** Blocked sessions floating to the top puts
  urgency where the eye goes first. Fixed slots build muscle memory ("key 3 is
  always provenance"), which is most of what makes hardware beat a keyboard.
  Leaning fixed slots with color carrying urgency — but this is a preference about
  how Grant works, not a technical call.
- **Overflow: ten sessions, six slots.** Touch points page left/right, which is
  what they're for, but paging fights muscle memory too. Alternative: only pinned
  sessions get slots.
- **Info bar** — can a plugin write to it? Unverified. Would suit an aggregate
  ("2 waiting").

## Renderer backend — decided: direct HID

The interesting logic (enumerate sessions, reconcile hooks against polling, decide
what eight keys show) is **identical** whether we render through the Elgato app or
straight to USB HID. Only the render/input layer differs.

Research settled it — see [selfhosted.md](reference/selfhosted.md). Open-source Neo support
turned out to be **complete**, not thin: all 8 keys, both touch points, and the
info bar, verified from source in three separate libraries. The earlier prediction
that touch points and info bar would be the gap was wrong.

**Decision: a bespoke Python daemon on `python-elgato-streamdeck`, as a
LaunchAgent.** No Elgato app, no OpenDeck.

Reasoning: every key in this design is dynamic and driven by our own logic. A full
host's value is its GUI, profiles, and plugin ecosystem — none of which this uses.
What we need is "draw 8 images, receive 10 button events," which the library
exposes directly. Going through a host would mean fighting a configuration layer we
don't want.

This also deletes several constraints that shaped earlier drafts: the custom-icon
override trap, the undocumented `ProfilesV3` schema, Smart Profile gotchas, and
the bundled-profiles-only restriction on `switchToProfile`. None of those concepts
exist when we own the device.

Costs accepted: we own the device lifecycle (sleep/wake, USB reconnect,
brightness), and firmware updates require occasionally launching Elgato's app —
keep it installed, run it about annually, quit it. OpenDeck remains the fallback
if lifecycle ownership proves more annoying than expected.

Still worth keeping the render/input boundary clean, but now for testability
rather than escape.
