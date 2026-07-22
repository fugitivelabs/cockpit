# Hardware and software stack

Researched 2026-07-18. Versions current as of that date.

## The device

Elgato Stream Deck Neo, purchased 2026-07-18 for ~$85 (MSRP $99.99).

| Property | Value |
|---|---|
| Keys | 8 LCD keys, 4×2 matrix — a screen per key |
| Key image | 96 × 96 px, JPEG over HID, rotated 180° before upload |
| Touch points | 2 capacitive sensors flanking the info bar; page left/right |
| Info bar | LCD strip, 248 × 58 px per HID docs; clock/notifications |
| Connection | USB 2.0 Type-C, fixed 0.9 m cable. VID `0x0FD9`, PID `0x009A` |
| Local identity | Model `20GBJ9901`, serial `A7BSA5321JFSLQ` |

Confirmed locally: the profile manifest declares **two controllers** — a `Keypad`
(the 4×2 grid, addressed `"0,0"`–`"3,1"`) and a separate one of type `Neo` (the
touch points / info bar). They are addressed independently.

**What the Neo lacks vs siblings:** no rotary dials and no touch strip, so the
`dialRotate`/`dialDown` events and the `setFeedback` layout API do not apply.
Those belong to the Stream Deck +. Anything written for the + that uses dials
will not port. Key icons are 96 × 96 (same as XL, larger than MK.2's 72 × 72).

Unconfirmed: power draw (no official figure). HID docs also list a 480 × 320
high-DPI display for the Neo, which is inconsistent with 8 × 96 px keys — do not
rely on that number.

## Desktop software

Installed: **7.5.0** (build 22885), released 2026-06-30.

- **Profile** — a full device layout, scoped to a device type. Contains pages.
- **Page** — one screenful. On the Neo the two touch points page left/right.
- **Folder** — a key descending into a nested sub-layout with an auto back key.
- **Multi-Action** — one key bound to an ordered sequence, with optional delays.
- **Smart Profile** — a profile bound to an application, activated when that app
  is frontmost.

### Smart Profile caveats

Both documented, both will cost time if forgotten:

1. **Smart Profiles are disabled while the Stream Deck editor window is open.**
   By design, so editing doesn't trigger switching. Close/minimize the app to test.
2. They require macOS Accessibility permission, which **major macOS updates
   silently revoke** — breaking Smart Profiles *and* Hotkey/Text actions. Fix by
   toggling Stream Deck off/on in System Settings → Privacy & Security →
   Accessibility.

### Profiles on disk

`~/Library/Application Support/com.elgato.StreamDeck/ProfilesV3/<UUID>.sdProfile/`
with a root `manifest.json` and a `Profiles/` subdirectory of per-page folders,
each holding its own `manifest.json` of actions keyed by `"X,Y"` grid coordinate.
Plain JSON, so readable and in principle generatable.

**Treat this as an unstable contract.** The schema is community reverse-engineering,
not vendor spec. Published write-ups describe `ProfilesV2`; this install is already
on `ProfilesV3`. The app also holds state in memory, so files edited while it runs
can be overwritten. Elgato's supported path is export/import of `.streamDeckProfile`
archives. Prefer the plugin API for anything dynamic; treat file generation as a
convenience, not a foundation.

## Plugin SDK

- `@elgato/streamdeck` **v2.1.0** (2026-04-16), Node ≥ 20.5.1
- `@elgato/cli` **v1.7.4** (2026-04-14) — `npm i -g @elgato/cli@latest`
- Manifest `SDKVersion` field takes `2` or `3` — a different axis from the npm version

**Runtime:** backend is Node.js (20.20.0 or 24.13.1 as of Stream Deck 7.3); the
Property Inspector frontend is a Chromium 130 DOM context. Transport between the
plugin and the app is a WebSocket, which is why community SDKs exist in Go, Python,
and C#.

**The key fact: a plugin is an ordinary, unsandboxed Node process.** Full
`child_process`, filesystem, network, arbitrary npm dependencies. No capability
model, no permission prompt, no signing or notarization for local sideloading.
A plugin is effectively "run arbitrary code on key press" — this is the escape
hatch for everything MCP cannot do.

### Feedback back to keys

`setImage` (file path, base64, or **encoded SVG string** — the way to render
dynamic counters without pre-baking PNGs; animated GIF unsupported), `setTitle`,
`setState(0|1)`, `showOk()` / `showAlert()`.

Two constraints that shape design:

- **A user-set custom title or image wins over anything the plugin pushes at
  runtime.** Keys intended to be dynamic must be left uncustomized in the GUI.
- **A plugin can only switch to profiles it bundles**, never user-created ones.
  Programmatic profile switching means our plugin owns the profiles.

No rate limits are documented anywhere. USB 2.0 with 96×96 JPEG keys implies real
bandwidth limits, but no figure is published — treat high-frequency `setImage`
as unvalidated.

## Escape hatches short of a plugin

Built-in System actions: **Hotkey, Open, Website, Multimedia, Text**, plus
Multi-Action to chain them. There is **no built-in "run shell command" action**.

`System: Open` will execute a script with a recognized extension (`.sh`, `.py`)
if it is executable, but passes no arguments, captures no stdout, and does not
reliably inherit `$PATH` — use absolute paths. macOS Shortcuts is the common
no-plugin route, since Shortcuts exports items under `~/Applications` that
`System: Open` can launch.

Third-party plugins that fill the shell gap:

- **Mac Automation** (ThoughtAsylum, free) — shell commands, AppleScript from file
- **Stream Deck Shell** (paulfxyz, v2.0.0 2026-04-12) — real zsh/bash execution,
  pipes and operators, status feedback on the key
- **streamdeck-osascript** (gabrielperales) — arbitrary AppleScript + JXA

## Sources

- <https://docs.elgato.com/streamdeck/hid/stream-deck-neo/>
- <https://docs.elgato.com/streamdeck/sdk/introduction/plugin-environment/>
- <https://docs.elgato.com/streamdeck/sdk/references/manifest/>
- <https://docs.elgato.com/streamdeck/sdk/guides/keys/>
- <https://docs.elgato.com/streamdeck/sdk/guides/profiles/>
- <https://www.elgato.com/us/en/p/stream-deck-neo>
- <https://help.elgato.com/hc/en-us/articles/360053419071> (Smart Profiles)
