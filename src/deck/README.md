# deck

A light declarative layer over a Stream Deck. Describe what the keys should
show; the Surface works out what needs to hit USB.

**It knows nothing about Claude Code, Terminal, sessions, or any other use case.**
That is deliberate. All policy lives above it, so the layer survives having its
consumer rewritten or thrown away.

## Why this exists

`python-elgato-streamdeck` is a driver: claim the device, push a JPEG to a key
index, set an RGB colour, read a raw callback. Useful, but everything above that
gets hand-rolled every time. This adds the missing middle:

- **Diffing** — declare state, only changed keys are written
- **Image caching** — encoding costs ~2 ms/key vs ~0.8 ms to push, so rendered
  images are cached by slot value
- **Debounced input** — the touch pads repeat-fire while held (measured ~30
  events from a handful of taps); untreated they are unusable
- **Long-press** — measured on release for mechanical keys
- **Reconnect** — survives unplug/replug instead of dying
- **Text fitting** — shrink-then-truncate so labels fit 96×96
- **Animation** — pre-encoded frame playback
- **Panel tiling** — treat the 4×2 grid as one 384×192 canvas

## Use

```python
from deck import Surface, Slot

with Surface(brightness=70) as s:
    s.on_press(lambda i, long: print("pressed", i, "long" if long else ""))
    s.show({
        0: Slot(label="provenance", sub="1804 Wake deed", accent="#3FA7D6"),
        1: Slot(label="docland", bg="#141414"),
    })
    s.set_info("2 sessions", "1 working")
    s.run(tick=repaint, interval=2.0)
```

`Slot` is a frozen value object — equality drives both the render cache and the
diff, so re-declaring an identical slot costs nothing.

### Animation

```python
frames = [tile(deck, my_image, gap=26) for my_image in sequence]
prepared = s.prepare(frames)     # encode up front — this is the whole trick
fps = s.play(prepared, fps=30, loops=3)
```

Frames are `{key_index: PIL.Image}`, optionally with `"info"` for the info bar.

## Component framework

The layer above bare slots, for composing a live surface:

    Surface     owns the device                              (surface.py)
    Component   renders one key, handles its press           (components.py)
    View        maps components onto keys, routes presses    (app.py)
    App         binds Surface + View, runs the loop          (app.py)

```python
from deck import App, PagedView, Button, Live, meter, Slot

counter = [0]
view = PagedView([
    Live(lambda: Slot(label=time.strftime("%H:%M"))),               # a clock
    Button(lambda: Slot(label=str(counter[0])),                     # a counter
           on_press=lambda: counter.__setitem__(0, counter[0] + 1)),
    meter(lambda: used_fraction(), label_fn=lambda: "ctx"),         # a 0..1 bar
    *[Static(Slot(label=f"#{i}")) for i in range(12)],              # overflow
])                                                                  # -> 2 pages
App(view=view).run()   # touch pads page; presses route to the right component
```

**The contract:** `Component.render() -> Slot` is cheap and pure — it formats
current state and does no I/O. The loop re-renders every visible component each
tick and after each press; caching + diffing make that efficient, so there is no
manual dirty-tracking. State arrives from *outside* (a channel updates a
component's fields); render only formats it.

- `Static` — a fixed slot. `Button` — a slot (or `() -> Slot`) plus press/long
  callbacks. `Live` — a render function, optionally pressable. `meter()` — a
  labelled 0..1 bar that turns amber past a threshold (generic; knows nothing of
  tokens).
- `View` places components on the 8 content keys. `PagedView` holds any number
  and pages through them via the touch points; presses map through the current
  page to the correct underlying component.
- Touch points auto-glow when a view can page.

Verified: 24/24 logic tests (components, meter thresholds, view routing, paging,
paged-press mapping) with no device, plus live on hardware through the full stack.
None of this layer knows any use case.

## Lifecycle (built 2026-07-21)

The operational layer that makes this safe to run headless:

- **Per-component fault isolation** — a component whose `render()` raises yields
  an `ERR` tile for that one key (`View.slots()`), never a blank deck or a dead
  loop; a raising press handler is swallowed. `flush()` splits rasterisation
  (isolate a bad slot) from transport (reconnect on a dead device).
- **SIGTERM/SIGINT graceful shutdown** — `Surface.run()` catches them (main
  thread only, prior handlers restored), blanks the deck, releases, returns.
- **Single-instance guard** — `deck.lifecycle.SingleInstance` (flock; released by
  the kernel on exit, even SIGKILL).
- **Structured logging** — `logging.getLogger(__name__)` throughout;
  `deck.lifecycle.configure_logging()` is the consumer opt-in. The library ships
  only a `NullHandler`, so it stays silent until a consumer configures output.

- **Non-blocking reconnect** — a lost device marks the surface down and returns;
  the run loop (and any writer, via `flush()`) retries `open()` on a cadence while
  continuing to tick, so an always-on consumer never freezes device-out.
  `on_disconnect()` / `on_reconnect()` hooks let a consumer narrate or react.

Covered by `../tests/test_lifecycle.py` (37 headless assertions). The one thing
still earned by uptime, not tests, is reconnect + sleep-wake under the
LaunchAgent — first live day was 2026-07-21; see [../operations.md](../operations.md).

## Measured on a Stream Deck Neo

| | |
|---|---|
| PIL encode | ~2.0 ms/key |
| USB push, single key | ~0.8 ms |
| USB push, 8 keys | ~6–23 ms (first push slower) |
| Info bar push (full panel) | ~0.9 ms |
| Sustained full repaints | ~51/sec |
| Achieved animation | ~27 fps @ 30 target, ~39 @ 45 |

Animation lands consistently ~8–12% under target — `time.sleep` granularity plus
push cost. Treat ~40 fps as the practical full-panel ceiling.

**Encoding is ~2.5× the USB cost**, which is why `prepare()` exists and why the
render cache matters more than push-diffing does.

## Input verified on hardware (2026-07-20, `hwtest.py`)

Raw HID events were counted alongside dispatched ones, so these are measured, not
assumed:

| Behaviour | Result |
|---|---|
| Touch pad held 8s | **1 raw event** — pads do NOT auto-repeat |
| 33 rapid pad taps | 33 dispatched — nothing dropped at 80ms guard |
| Human-counted taps | 33 counted = 33 raw = 33 dispatched — 1 tap : 1 event |
| Mechanical key bounce | none across 24 rapid presses |
| Long press (2s hold) | 1 long, 0 short |
| Quick taps | 3 short, 0 long — never misread as long |
| Diff, repeat flush | 0 redundant writes |
| Diff, 1 slot changed | exactly 1 write |

The one correction this surfaced: an earlier assumption that the pads auto-repeat
was wrong (it mistook deliberate taps for repeat-fire), and the guard was cut from
450ms to 80ms as a result.

`hwtest.py` runs one test at a time — `python3 -m deck.hwtest touch-hold` — or all
of them with no args. Each briefs the instruction on the keys (one word per key,
since the info bar is too small for a sentence) with a 3-2-1 countdown.

## Neo specifics

- 8 keys (4×2), 96×96 JPEG. The library handles the 180° flip.
- Touch pads report as **key indices 8 and 9** through the ordinary key callback
  — `TOUCH_LEFT` / `TOUCH_RIGHT`. They are colour-only (`set_touch`), no image.
- Info bar is 248×58, **full-panel writes only** — no partial regions on Neo.
- Touch pads emit press events only; long-press is meaningless for them, so
  they fire on press while mechanical keys fire on release.

## Files

- `render.py` — `Slot`, text fitting, `tile()`, info-bar rendering, `error_slot()`
- `surface.py` — device lifecycle, diffing, debounce, reconnect, animation,
  signal-driven graceful shutdown
- `lifecycle.py` — `configure_logging()` and the `SingleInstance` guard
- `app.py` / `components.py` — the View/App framework (fault isolation lives here)
- `demo.py` — read-only Terminal session dashboard (the actual use case)
- `showcase.py` — hardware capability demo, five animated scenes

## Running

The library needs `streamdeck` and `pillow`, plus `brew install hidapi`.

```bash
PYTHONPATH=.. python3 -m deck.showcase
PYTHONPATH=.. python3 -m deck.demo
```

Quit the Elgato app first — not because access is exclusive (it isn't, on
macOS), but because both processes will otherwise fight over the display.
