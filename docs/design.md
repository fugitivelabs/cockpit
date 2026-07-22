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
[axread.py](../src/cockpit/axread.py); the press-time guard re-reads and
requires the identical menu to still be there.

Still true, and still the reason for the conservative parser: a wrong guess is
worse than showing nothing.

### The prompt taxonomy

Moved to **[prompts.md](prompts.md)** — it grew into the most safety-critical
document here, and burying it inside a design rationale was the wrong place for
knowledge you must read before touching the answer keys.

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
