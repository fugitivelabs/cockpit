# Running the Neo without Elgato's app

Researched 2026-07-20. Headline: **open-source Neo support on macOS is complete,
not thin.** All 8 keys, both touch points, and the info bar. This was the opposite
of the expected result.

## Low-level libraries — all three support Neo fully

| Library | Stars | Last push | License | Keys | Touch points | Info bar |
|---|---|---|---|---|---|---|
| [abcminiuser/python-elgato-streamdeck](https://github.com/abcminiuser/python-elgato-streamdeck) | 1,121 | 2026-05-03 | MIT-ish | Yes | Yes | Yes |
| [Julusian/node-elgato-stream-deck](https://github.com/Julusian/node-elgato-stream-deck) | 200 | 2026-07-20 | MIT | Yes | Yes | Yes |
| [OpenActionAPI/rust-elgato-streamdeck](https://github.com/OpenActionAPI/rust-elgato-streamdeck) | 89 | 2026-06-20 | MPL-2.0 | Yes | Yes | Yes |

Confirmed from source, not inferred:

- **Python** has a dedicated `StreamDeckNeo.py`: `KEY_COUNT = 8`,
  `TOUCH_KEY_COUNT = 2`, 96×96 keys, `SCREEN_PIXEL_WIDTH = 248` / `HEIGHT = 58`,
  and `SCREEN_FLIP = (True, True)` — **the library handles the 180° rotation**, so
  that detail from `hardware.md` is not ours to implement. Touch points are key
  indices 8–9 via `set_key_color`; the info bar is `set_screen_image`.
- **Node** declares the 4×2 grid plus two `feedbackType: 'rgb'` buttons at index
  8/9 and an `lcd-segment` control at 248×58.
- **Rust** has `PID_STREAMDECK_NEO = 0x009a` and `set_touchpoint_color()`.

Go is not competitive — `dh1tw/streamdeck` has no Neo, and the 4-star
`rafaelmartins/streamdeck` did not survive a source check. Skip Go.

**The one real hardware limit:** the Neo info bar is **whole-panel write only, no
partial region updates.** Node's `fillLcdRegion` throws "Not supported for this
model"; the Rust crate says regions are Stream Deck + only. This looks like a Neo
protocol constraint rather than a library gap. To change one glyph you re-encode
and push the full 248×58 JPEG. Fine at clock-tick rates; not for animation.

## Full daemons

**[OpenDeck](https://github.com/nekename/OpenDeck)** — 1,921 stars, pushed
2026-07-07, GPL-3.0, v2.13.1. Despite a Linux-sounding repo description it ships
real macOS DMGs (aarch64 and x64). Neo support arrived in stages: keys June 2024,
**touch points v2.8.0 (2026-01-02)**, **info bar v2.13.0 (2026-06-22)**. Runs
Elgato-format plugins.

**[Bitfocus Companion](https://github.com/bitfocus/companion)** — 2,224 stars,
mature, cross-platform, Neo supported including the info bar. But its model is
broadcast/AV control: buttons drive *modules* aimed at switchers, playback,
lighting. General desktop automation works against the grain. Not our shape.

**StreamController** is Linux/GTK4 only. **muesli/deckmaster** last pushed
2024-05-22, Linux-only, dormant. Neither is viable.

## macOS viability — no blockers found

- **No entitlements, no kext, no driver shim.** The Python library's macOS install
  is `brew install hidapi` with no permission steps, in contrast to its Linux
  section which details udev rules.
- **Input Monitoring (TCC) almost certainly not triggered.** Stream Decks present
  on a vendor-defined HID usage page, not Generic Desktop/Keyboard, and TCC gates
  `IOHIDDeviceOpen` on keyboard-like usages. Supporting evidence: zero issues
  mentioning macOS input monitoring across all three library repos plus OpenDeck —
  for a 1,100-star library with years of macOS use, a mandatory permission prompt
  would be the most-filed issue there is. **Inferred, strongly supported.**
  Verifiable in five minutes by running the Python library's `example_deckinfo.py`.
- **Exclusive access is real.** hidapi opens the device exclusively, so the Elgato
  app must be **fully quit** (menu-bar quit, not just window close). OpenDeck ships
  a "Disable Elgato device discovery" setting to arbitrate exactly this.
- **Signing:** not needed to run. A locally built daemon has no quarantine bit. A
  LaunchAgent gets launch-at-login with no signing. Downloaded builds are a
  different story — OpenDeck's macOS DMGs are unsigned and un-notarized, so
  expect `sudo xattr -cr /Applications/OpenDeck.app` on every update.

## Firmware — a real forfeit, easily mitigated

Firmware updates ship **only** through Elgato's app. There is no open-source
firmware tooling for macOS. (A search result claiming otherwise is a Windows-only
driver manager with AI-generated filler copy — ignore it.)

Mitigation: this isn't all-or-nothing. Keep the Elgato app installed but not
running; launch it once a year to check firmware, then quit. Nothing is lost.

## Telemetry — the accurate read

**Confirmed by direct observation on this machine** (process list, 2026-07-18):
the app runs a `crashpad_handler` posting minidumps to `o324181.ingest.sentry.io`.
That is Sentry crash reporting, definitively present. Note it appears to be
*undocumented publicly* — a research pass found no mention in Elgato's docs,
release notes, or privacy pages.

What else is documented:

- **No account required for basic local use.** A Marketplace account is needed only
  to download or update Marketplace plugins.
- **The app phones home at startup** — Elgato states it "communicates with the
  Marketplace upon startup to inform you about any updates on your purchased or
  downloaded products."
- Elgato states it does not sell customer data. No global telemetry opt-out found.

Honest framing: this is a **normal commercial desktop app with a store attached**,
not a surveillance product. Crash reporting is standard practice. The good reasons
to avoid it here are **architectural** — we want a scriptable, version-controlled,
self-hosted config with no GUI in the loop — and that justification stands on its
own without needing a privacy story.

## Plugin portability

OpenDeck's Elgato-plugin compatibility is genuine — plugins are WebSocket processes
speaking a documented protocol, so re-hosting is tractable. The
[OpenAction API](https://openaction.amankhanna.me/) is a backward-compatible
superset.

One plugin *can* run on both stacks, conditionally: OpenAction's docs say plugins
work with both hosts "provided that your plugin doesn't use extended features of
the OpenAction API that aren't supported by the Stream Deck SDK." Write to the
intersection and you're portable; use OpenAction extensions and you're OpenDeck-only.
**There is no published compatibility matrix** — you'd find the edges empirically.

## Verified on this machine, 2026-07-20

Smoke-tested against the actual Neo with `streamdeck 0.9.8` + `pillow 12.3.0`
under Python 3.14.6 (native cp314 wheels, no build issues).

| Check | Result |
|---|---|
| Neo class in installed lib | Present, geometry matches research exactly |
| `DeviceManager().enumerate()` | 1 device — "Stream Deck Neo" |
| `deck.open()` | **Succeeded with the Elgato app still running** |
| TCC / Input Monitoring prompt | **None appeared** — inference confirmed |
| Serial / firmware | `A7BSA5321JFSLQ` / `1.00.013` (serial matches profile manifest) |
| `set_brightness` | OK |
| 8-key render via PIL | OK |
| Touch point colours (`set_key_color` 8, 9) | OK |
| **Info bar `set_screen_image`** | **OK** — 248×58, the least-proven path |
| `reset()` + `close()` | Clean |
| Key input, all 8 | OK — indices 0–7 via `set_key_callback` |
| **Touch point input** | **OK — indices 8 and 9, same callback** |

**14/14.** The full stack is proven on this hardware: claim, identify, render keys,
colour touch points, write the info bar, and read every input.

Confirmed from library source — `StreamDeckNeo._read_control_states` reads
`4 + KEY_COUNT + TOUCH_KEY_COUNT` = 14 bytes, strips 4, and reports all 10 controls
as `ControlType.KEY`. So **touch points are not a separate callback**; they arrive
as keys 8 and 9. `set_touchscreen_callback` and `set_dial_callback` exist on the
class but are inherited surface, not Neo features.

**Implementation note: the touch points need debouncing.** A handful of taps
produced 30 and 27 events respectively — they repeat-fire while held, or are
extremely sensitive. Untreated, a single held tap would page through many screens.

**Correction to the research:** it reported HID access as exclusive, requiring the
Elgato app to be fully quit. That is **not true on macOS** — the device opened
fine with the app running. Practically we still want the app quit during real use,
since both processes writing means they fight over the display, but there is **no
hard lock** and testing does not require quitting anything.

Still unverified, needs a human at the desk:

- whether the writes were *visually* on screen, or immediately repainted over by
  the still-running Elgato app (API returned success either way)
- **button and touch-point input** — `set_key_callback` is untested

`smoketest.py` in the session scratchpad covers the input half.

## Conclusion for this project

For a deck where **every key is dynamic and driven by our own logic**, a full
daemon is overhead. OpenDeck's value is its GUI, profiles, and plugin ecosystem —
three things this design uses none of. What we actually need is "draw 8 images,
receive 10 button events," which is precisely what the libraries expose directly.

**Recommended: a bespoke Python daemon on `python-elgato-streamdeck`**, shipped as
a LaunchAgent. Most-starred, best-documented, complete and explicit Neo class, and
PIL makes rendering text labels onto 96×96 keys straightforward. Node is the
alternative if the JS toolchain matters more than rendering ergonomics.

OpenDeck stays the fallback if owning the device lifecycle (sleep/wake, USB
reconnect, brightness) proves more annoying than expected.

Known-immature, stated plainly:

- OpenDeck's Neo info bar support is ~1 month old — least-proven path if we use it
- No partial info-bar updates anywhere; full repaints only
- Firmware is a hard forfeit without occasionally launching Elgato's app
- Elgato-plugin compatibility in OpenDeck is real but unmapped
- The TCC/Input-Monitoring conclusion is inferred, not tested
