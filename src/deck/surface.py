"""Surface — a declarative view of a Stream Deck.

You describe what the deck should show; Surface works out what actually needs to
hit USB. It owns the device, caches rendered images, debounces input, and
survives unplug/replug.

It deliberately knows nothing about what the slots mean. No sessions, no
terminals, no applications. That separation is the point: the policy layer above
can be rewritten or thrown away without touching this.
"""

from __future__ import annotations

import logging
import signal
import threading
import time
from typing import Callable, Optional

from StreamDeck.DeviceManager import DeviceManager
from StreamDeck.ImageHelpers import PILHelper

from .render import BLANK, ERROR_SLOT, Slot, render, render_info

log = logging.getLogger(__name__)

# The Neo reports its two capacitive pads as ordinary keys after the 8 real ones.
TOUCH_LEFT = 8
TOUCH_RIGHT = 9

# Debounce, both values verified on hardware 2026-07-20 (deck/hwtest.py):
#   - Touch pads do NOT auto-repeat: 1 raw event for an 8s continuous hold.
#     (An earlier guess that they repeat-fired was wrong — it mistook ~30
#     deliberate taps for auto-repeat, and led to a 450ms guard that would have
#     silently eaten fast paging.)
#   - At 80ms, 33 rapid pad taps all got through: raw 33 -> dispatched 33.
#   - Mechanical keys showed no bounce across 24 rapid presses.
# So both guards exist only to swallow contact bounce and are kept small enough
# never to drop an intentional tap.
KEY_DEBOUNCE_S = 0.05
TOUCH_DEBOUNCE_S = 0.08

LONG_PRESS_S = 0.6

# How often to retry opening the device while disconnected. The run loop keeps
# ticking between attempts, so this is a poll cadence, not a freeze.
RECONNECT_POLL_S = 1.5


class DeckUnavailable(RuntimeError):
    pass


class Surface:
    """Declarative deck surface.

        with Surface() as s:
            s.on_press(lambda i, long: print(i, long))
            s.show({0: Slot(label="one"), 1: Slot(label="two")})
            s.run()
    """

    def __init__(self, brightness: int = 60, auto_reconnect: bool = True):
        self._deck = None
        self._brightness = brightness
        self._auto_reconnect = auto_reconnect

        self._desired: dict[int, Slot] = {}
        self._pushed: dict[int, Slot] = {}
        self._colors: dict[int, tuple] = {}
        self._info: Optional[tuple] = None
        self._info_pushed: Optional[tuple] = None

        self._cache: dict[Slot, object] = {}
        self._press_cb: Optional[Callable[[int, bool], None]] = None
        self._last_event: dict[int, float] = {}
        self._down_at: dict[int, float] = {}

        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._shutdown_signal: Optional[str] = None
        # Native image shown when a slot cannot be rasterised; encoded lazily
        # against the open device so fault isolation never itself raises.
        self._error_native = None

        # Non-blocking reconnect bookkeeping. A lost device marks the surface
        # disconnected and returns; the run loop retries open() on a cadence
        # while continuing to tick, so the daemon never freezes device-out.
        self._next_reconnect = 0.0
        self.disconnects = 0
        self.reconnects = 0
        self._on_reconnect: Optional[Callable[[], None]] = None
        self._on_disconnect: Optional[Callable[[], None]] = None

    # -- lifecycle --------------------------------------------------------

    def open(self) -> "Surface":
        decks = DeviceManager().enumerate()
        if not decks:
            raise DeckUnavailable(
                "No Stream Deck found. Check the cable; if the Elgato app is "
                "running it will fight this process for the display."
            )
        self._deck = decks[0]
        self._deck.open()
        self._deck.set_brightness(self._brightness)
        self._deck.set_key_callback(self._on_raw)
        # Force a full repaint against the new device. The error tile is
        # encoded against a specific device object, so drop it too.
        with self._lock:
            self._pushed.clear()
            self._info_pushed = None
            self._error_native = None
        self.flush()
        return self

    def close(self) -> None:
        self._stop.set()
        if self._deck:
            try:
                self._deck.reset()
                self._deck.close()
            except Exception:
                pass  # unplugged mid-shutdown is not worth raising over
            self._deck = None

    def __enter__(self):
        return self.open()

    def __exit__(self, *exc):
        self.close()
        return False

    @property
    def key_count(self) -> int:
        return self._deck.key_count() if self._deck else 8

    def reset(self) -> None:
        """Blank every key and clear cached state, without releasing the device."""
        if self._deck:
            self._deck.reset()
        with self._lock:
            self._pushed.clear()
            self._info_pushed = None

    @property
    def brightness(self) -> int:
        return self._brightness

    def set_brightness(self, level: int) -> int:
        """Set panel brightness 0..100. Returns the level actually applied.

        Recorded even when the device is absent, because `open()` re-applies
        `_brightness` on every (re)connect — so a level set during an unplug
        survives the replug instead of silently reverting to the constructor's.
        Safe to call when disconnected; it just can't push until there's a device.
        """
        level = max(0, min(100, int(level)))
        with self._lock:
            self._brightness = level
        if self._deck:
            try:
                self._deck.set_brightness(level)
            except Exception as e:
                # A dead device here is the reconnect path's business, not the
                # caller's — brightness is never worth raising into a press.
                log.debug("set_brightness(%d) failed: %s", level, e)
        return level

    # -- declaring state --------------------------------------------------

    def show(self, slots: dict[int, Slot], clear_rest: bool = True) -> None:
        """Declare what the keys should show, then flush the difference."""
        with self._lock:
            if clear_rest:
                self._desired = {i: slots.get(i, BLANK) for i in range(self.key_count)}
            else:
                self._desired.update(slots)
        self.flush()

    def set_slot(self, index: int, slot: Slot) -> None:
        with self._lock:
            self._desired[index] = slot
        self.flush()

    def set_touch(self, index: int, rgb: tuple[int, int, int]) -> None:
        """Colour a touch point. Index is TOUCH_LEFT or TOUCH_RIGHT."""
        with self._lock:
            if self._colors.get(index) == rgb:
                return
            self._colors[index] = rgb
        if self._deck:
            self._deck.set_key_color(index, *rgb)

    def set_info(self, text: str, sub: str = "", bg: str = "#000000",
                 fg: str = "#FFFFFF", marks=(), pages=None) -> None:
        """Declare the info bar. See render.render_info for `marks`/`pages`.

        Both are normalised to tuples here rather than trusted from the caller:
        the whole bar is diffed by value before it is pushed, so a list would
        make an unchanged bar compare unequal every tick and repaint forever.
        """
        marks = tuple((c, str(t)) for c, t in marks) if marks else ()
        pages = tuple(pages) if pages else None
        with self._lock:
            self._info = (text, sub, bg, fg, marks, pages)
        self.flush()

    # -- flushing ---------------------------------------------------------

    def flush(self) -> int:
        """Push only what changed. Returns the number of keys written."""
        # If the device is gone, try to reclaim it (throttled) so self-driven
        # loops recover too, not just run(). open() sets _deck before it repaints,
        # so this does not recurse.
        if self._deck is None and self._auto_reconnect:
            self._maybe_reconnect()
        if not self._deck:
            return 0
        with self._lock:
            desired = dict(self._desired)
            info, info_pushed = self._info, self._info_pushed

        written = 0
        for idx, slot in desired.items():
            if idx >= self.key_count:
                continue
            if self._pushed.get(idx) == slot:
                continue
            native = self._cache.get(slot)
            if native is None:
                try:
                    native = PILHelper.to_native_key_format(
                        self._deck, render(self._deck, slot))
                    self._cache[slot] = native
                except Exception:
                    # A slot that won't rasterise (bad colour, malformed value)
                    # must not abort the flush or be mistaken for a disconnect.
                    # Fall back to a generic error tile for this one key.
                    log.exception("could not rasterise slot for key %d", idx)
                    native = self._error_image()
                    if native is None:
                        continue
            # set_key_image is the transport boundary: a failure here is the
            # device going away, which is the one thing that triggers reconnect.
            try:
                self._deck.set_key_image(idx, native)
            except Exception as e:
                if not self._auto_reconnect:
                    raise
                self._handle_disconnect(e)
                return written
            self._pushed[idx] = slot
            written += 1

        if info is not None and info != info_pushed:
            try:
                native_info = PILHelper.to_native_screen_format(
                    self._deck, render_info(self._deck, *info))
            except Exception:
                log.exception("could not rasterise info bar")
                native_info = None
            if native_info is not None:
                try:
                    self._deck.set_screen_image(native_info)
                    self._info_pushed = info
                except Exception as e:
                    if not self._auto_reconnect:
                        raise
                    self._handle_disconnect(e)
        return written

    def _error_image(self):
        """Lazily-encoded generic error tile, cached per device object.

        Rendering ERROR_SLOT is about as safe as rasterisation gets; if even
        that fails the caller skips the key rather than pushing None.
        """
        if self._error_native is None and self._deck:
            try:
                self._error_native = PILHelper.to_native_key_format(
                    self._deck, render(self._deck, ERROR_SLOT))
            except Exception:
                log.exception("could not encode the fallback error tile")
                return None
        return self._error_native

    # -- animation --------------------------------------------------------

    def prepare(self, frames: list) -> list:
        """Pre-encode animation frames.

        Encoding costs ~2 ms/key against ~0.8 ms to push, so anything animated
        must be encoded up front or the encode dominates the frame budget.
        Each frame is a dict of {key_index: PIL.Image}, optionally with the key
        "info" holding an info-bar image.
        """
        if not self._deck:
            raise DeckUnavailable("prepare() needs an open device")
        out = []
        for frame in frames:
            enc = {}
            for idx, img in frame.items():
                if idx == "info":
                    enc["info"] = PILHelper.to_native_screen_format(self._deck, img)
                else:
                    enc[idx] = PILHelper.to_native_key_format(self._deck, img)
            out.append(enc)
        return out

    def play(self, prepared: list, fps: float = 24, loops: int = 1) -> float:
        """Play pre-encoded frames. Returns the achieved frames-per-second.

        Leaves the surface dirty on purpose — the next show()/flush() repaints
        everything, since the device no longer matches the declared state.
        """
        if not self._deck or not prepared:
            return 0.0
        budget = 1.0 / fps
        shown = 0
        t_start = time.perf_counter()
        try:
            for _ in range(loops):
                for frame in prepared:
                    t0 = time.perf_counter()
                    for idx, native in frame.items():
                        if idx == "info":
                            self._deck.set_screen_image(native)
                        else:
                            self._deck.set_key_image(idx, native)
                    shown += 1
                    slack = budget - (time.perf_counter() - t0)
                    if slack > 0:
                        time.sleep(slack)
                    if self._stop.is_set():
                        raise KeyboardInterrupt
        except KeyboardInterrupt:
            pass
        except Exception as e:
            if not self._auto_reconnect:
                raise
            self._handle_disconnect(e)
        finally:
            with self._lock:
                self._pushed.clear()
                self._info_pushed = None
        elapsed = time.perf_counter() - t_start
        return shown / elapsed if elapsed > 0 else 0.0

    # -- input ------------------------------------------------------------

    def on_press(self, cb: Callable[[int, bool], None]) -> None:
        """Register a press handler: cb(index, was_long_press)."""
        self._press_cb = cb

    def _on_raw(self, _deck, index: int, state: bool) -> None:
        now = time.monotonic()
        guard = TOUCH_DEBOUNCE_S if index >= TOUCH_LEFT else KEY_DEBOUNCE_S

        if state:
            self._down_at[index] = now
            # Touch pads repeat while held, so fire on press and guard hard.
            if index >= TOUCH_LEFT:
                if now - self._last_event.get(index, 0.0) < guard:
                    return
                self._last_event[index] = now
                self._dispatch(index, False)
            return

        # Release: mechanical keys fire here so long-press is measurable.
        if index >= TOUCH_LEFT:
            return
        if now - self._last_event.get(index, 0.0) < guard:
            return
        self._last_event[index] = now
        held = now - self._down_at.get(index, now)
        self._dispatch(index, held >= LONG_PRESS_S)

    def _dispatch(self, index: int, long_press: bool) -> None:
        if not self._press_cb:
            return
        try:
            self._press_cb(index, long_press)
        except Exception:
            log.exception("press handler raised for key %d", index)

    # -- resilience -------------------------------------------------------

    def on_reconnect(self, cb: Optional[Callable[[], None]]) -> None:
        """Register a callback fired after the device is reclaimed."""
        self._on_reconnect = cb

    def on_disconnect(self, cb: Optional[Callable[[], None]]) -> None:
        """Register a callback fired when the device is first lost."""
        self._on_disconnect = cb

    def _handle_disconnect(self, exc: Exception) -> None:
        """Mark the surface disconnected and return — do NOT block.

        The old model blocked here polling for the device, which froze the whole
        run loop (heartbeat, channels, everything) for as long as the deck was
        unplugged. Now we just release the handle and let the run loop retry via
        _maybe_reconnect() on its own cadence, so the daemon stays alive and
        responsive while the device is out.
        """
        if self._deck is None:
            return  # already handled this disconnect
        self.disconnects += 1
        log.warning("lost device (%s) — will retry while running",
                    type(exc).__name__)
        try:
            self._deck.close()
        except Exception:
            pass
        self._deck = None
        self._next_reconnect = time.monotonic() + RECONNECT_POLL_S
        if self._on_disconnect:
            try:
                self._on_disconnect()
            except Exception:
                log.exception("on_disconnect callback raised")

    def _maybe_reconnect(self) -> bool:
        """Try to reclaim the device if disconnected. Throttled; never blocks.

        Returns True if connected (already, or newly). Called from the run loop
        each iteration while the deck is gone.
        """
        if self._deck is not None:
            return True
        if self._stop.is_set():
            return False
        if time.monotonic() < self._next_reconnect:
            return False
        try:
            self.open()  # re-registers the key callback and repaints
        except Exception:
            self._next_reconnect = time.monotonic() + RECONNECT_POLL_S
            return False
        self.reconnects += 1
        log.info("reconnected")
        if self._on_reconnect:
            try:
                self._on_reconnect()
            except Exception:
                log.exception("on_reconnect callback raised")
        return True

    # -- run loop ---------------------------------------------------------

    def run(self, tick: Optional[Callable[[], None]] = None,
            interval=1.0, handle_signals: bool = True) -> None:
        """Block, calling `tick` every `interval` seconds until stopped.

        `interval` may be a callable returning the delay, re-consulted each
        pass. That is how an animated surface pays for its frame rate only while
        something is actually moving: a still board keeps ticking once a second,
        and a breathing one speeds up until it settles.

        With `handle_signals` (default) and run() on the main thread, SIGTERM
        and SIGINT are caught and turned into a *graceful* shutdown: the loop
        exits, the deck is blanked, and the device is released before run()
        returns. This is what makes the daemon safe under launchd — the default
        SIGTERM disposition would otherwise kill the process outright, leaving
        the last images frozen on the keys. Previous handlers are restored on
        the way out, so the surface leaves the process's signal state as it
        found it.
        """
        prev = self._install_signal_handlers() if handle_signals else None
        try:
            while not self._stop.is_set():
                # Reclaim the device if it's gone — throttled, non-blocking, so
                # the tick below still runs (heartbeat, channels) while deck-out.
                if self._deck is None and self._auto_reconnect:
                    self._maybe_reconnect()
                if tick:
                    try:
                        tick()
                    except Exception:
                        log.exception("tick raised")
                delay = interval() if callable(interval) else interval
                self._stop.wait(delay)
        except KeyboardInterrupt:
            self._shutdown_signal = self._shutdown_signal or "KeyboardInterrupt"
        finally:
            if prev is not None:
                self._restore_signal_handlers(prev)
            if self._shutdown_signal is not None:
                log.info("shutting down on %s — blanking deck, releasing device",
                         self._shutdown_signal)
                self.close()

    def _install_signal_handlers(self) -> Optional[dict]:
        """Route SIGTERM/SIGINT into a graceful stop. Main-thread only.

        Returns the previous handlers to restore, or None if we couldn't
        install (off the main thread — signal.signal would raise there).
        """
        if threading.current_thread() is not threading.main_thread():
            log.debug("run() off the main thread; leaving signal handling alone")
            return None

        def handler(signum, _frame):
            self._shutdown_signal = signal.Signals(signum).name
            self._stop.set()

        prev: dict = {}
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                prev[sig] = signal.signal(sig, handler)
            except (ValueError, OSError):
                pass
        return prev

    def _restore_signal_handlers(self, prev: dict) -> None:
        for sig, handler in prev.items():
            try:
                signal.signal(sig, handler)
            except (ValueError, OSError):
                pass

    def stop(self) -> None:
        self._stop.set()
