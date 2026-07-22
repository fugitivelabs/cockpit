"""Lifecycle-primitive tests — no device needed.

Covers the four Stage 0.5 primitives that turn the library from a foreground
script into a supervisable daemon:

  1. per-component fault isolation  (a raising render() -> error tile, not death)
  2. SIGTERM/SIGINT graceful shutdown  (handler wiring, main-thread guard)
  3. single-instance guard  (flock refuses a second holder)
  4. structured logging  (configure_logging is levelled + idempotent)

The device-facing halves — that a rasterisation failure is isolated while a
transport failure triggers reconnect — are exercised here with a fake deck, so
even the flush() split is covered without hardware.
"""
import logging
import os
import signal
import sys
import tempfile
import threading

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import deck.surface as surface_mod
from deck import (
    AlreadyRunning,
    BLANK,
    Button,
    Live,
    SingleInstance,
    Slot,
    Surface,
    View,
    configure_logging,
    error_slot,
)
from deck.render import ERROR_SLOT

# Silence the expected error-logs from the fault-isolation cases; the logging
# test re-raises the level explicitly when it needs output.
logging.getLogger("deck").setLevel(logging.CRITICAL)

ok = 0
fail = 0


def check(name, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  PASS  {name}" + (f" — {detail}" if detail else ""))
    else:
        fail += 1
        print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))


# ---------------------------------------------------------------------------
print("\n[fault isolation — component render()]")


class Boom(Live):
    def __init__(self):
        super().__init__(lambda: Slot(label="never"))

    def render(self):
        raise ValueError("kaboom")


v = View([Button(Slot(label="ok")), Boom(), Button(Slot(label="fine"))])
slots = v.slots()  # must NOT raise
check("one bad component does not raise", True)
check("good neighbours still render", slots[0].label == "ok" and slots[2].label == "fine")
check("bad component becomes an error tile", slots[1].key == "__deck_error__", slots[1].label)
check("error tile carries the exception type", slots[1].sub == "ValueError", slots[1].sub)
check("error_slot truncates long detail",
      len(error_slot("A" * 40).sub) == 18)


class PressBoom(Button):
    def __init__(self):
        super().__init__(Slot(label="x"))

    def on_press(self, long):
        raise RuntimeError("press boom")


vp = View([PressBoom()])
check("raising press handler is swallowed", vp.press(0, False) is False)


# ---------------------------------------------------------------------------
print("\n[fault isolation — flush() rasterise vs transport]")


class FakeDeck:
    """Just enough surface for flush() to run without real USB or PIL."""

    def __init__(self, keys=8):
        self._keys = keys
        self.images = {}
        self.fail_set_image = False
        self.reset_called = 0
        self.closed = 0

    def key_count(self):
        return self._keys

    def set_key_image(self, idx, native):
        if self.fail_set_image:
            raise RuntimeError("No HID device")
        self.images[idx] = native

    def set_screen_image(self, native):
        pass

    def set_key_color(self, i, r, g, b):
        pass

    def reset(self):
        self.reset_called += 1

    def close(self):
        self.closed += 1


# Stub the two costs flush() incurs — rendering and native encoding — so the
# test drives the branch logic, not PIL. render() raises for a poison slot to
# simulate a slot that cannot be rasterised.
_orig_render = surface_mod.render
_orig_pil = surface_mod.PILHelper


def fake_render(deck, slot):
    if getattr(slot, "key", None) == "__poison__":
        raise ValueError("unrenderable")
    return ("img", slot.key or slot.label or "blank")


class FakePIL:
    @staticmethod
    def to_native_key_format(deck, img):
        return img

    @staticmethod
    def to_native_screen_format(deck, img):
        return img


surface_mod.render = fake_render
surface_mod.PILHelper = FakePIL
try:
    poison = Slot(label="X", key="__poison__")
    s = Surface()
    s._deck = FakeDeck()
    written = s.show({0: Slot(label="good", key="g"), 1: poison})
    check("bad slot does not abort the flush", True)
    check("good key written normally", s._deck.images.get(0) == ("img", "g"),
          str(s._deck.images.get(0)))
    check("unrenderable key falls back to error tile",
          s._deck.images.get(1) == ("img", ERROR_SLOT.key),
          str(s._deck.images.get(1)))

    # Transport failure (set_key_image raising) is the ONE thing that must be
    # treated as a disconnect, not isolated. With auto_reconnect off it re-raises.
    s2 = Surface(auto_reconnect=False)
    s2._deck = FakeDeck()
    s2._deck.fail_set_image = True
    raised = False
    try:
        s2.show({0: Slot(label="a")})
    except RuntimeError:
        raised = True
    check("transport failure is not swallowed (would reconnect)", raised)
finally:
    surface_mod.render = _orig_render
    surface_mod.PILHelper = _orig_pil


# ---------------------------------------------------------------------------
print("\n[non-blocking reconnect]")

surface_mod.render = fake_render
surface_mod.PILHelper = FakePIL
try:
    import time as _time
    s = Surface(auto_reconnect=True)
    fd = FakeDeck()
    fd.fail_set_image = True
    s._deck = fd
    dcb, rcb = [], []
    s.on_disconnect(lambda: dcb.append(1))
    s.on_reconnect(lambda: rcb.append(1))

    t0 = _time.monotonic()
    s.show({0: Slot(label="x")})          # transport error -> disconnect
    dt = _time.monotonic() - t0
    check("a lost device does not block the caller", dt < 0.5, f"{dt:.3f}s")
    check("surface is marked disconnected", s._deck is None)
    check("disconnect counted + callback fired", s.disconnects == 1 and dcb == [1])

    healthy = FakeDeck()
    s.open = lambda: (setattr(s, "_deck", healthy) or s)   # stand in for real open()
    s.flush()                              # throttled: too soon to retry
    check("reconnect is throttled (no instant retry)", s._deck is None)

    s._next_reconnect = 0.0                # throttle window elapsed
    got = s._maybe_reconnect()
    check("reclaims the device after the throttle", got is True and s._deck is healthy)
    check("reconnect counted + callback fired", s.reconnects == 1 and rcb == [1])
finally:
    surface_mod.render = _orig_render
    surface_mod.PILHelper = _orig_pil


# ---------------------------------------------------------------------------
print("\n[graceful shutdown — signal wiring]")

s = Surface()
prev = s._install_signal_handlers()
check("handlers install on the main thread", prev is not None
      and signal.SIGTERM in prev and signal.SIGINT in prev)
installed = signal.getsignal(signal.SIGTERM)
check("SIGTERM now routes to our handler", callable(installed)
      and installed is not signal.SIG_DFL)
# Fire it directly rather than actually killing the test process.
installed(signal.SIGTERM, None)
check("handler sets the stop flag", s._stop.is_set())
check("handler records which signal", s._shutdown_signal == "SIGTERM", s._shutdown_signal)
s._restore_signal_handlers(prev)
check("previous handlers are restored",
      signal.getsignal(signal.SIGTERM) == prev[signal.SIGTERM])

# Off the main thread, signal.signal would raise — we must skip, not crash.
result = {}


def worker():
    result["prev"] = Surface()._install_signal_handlers()


t = threading.Thread(target=worker)
t.start()
t.join()
check("off-main-thread install is a safe no-op", result["prev"] is None)

# End to end: run() on a signalled stop drives graceful close() exactly once.
s = Surface()
fd = FakeDeck()
s._deck = fd


def tick_then_signal():
    # emulate SIGTERM arriving during the loop
    s._shutdown_signal = "SIGTERM"
    s._stop.set()


s.run(tick=tick_then_signal, interval=0.01)
check("run() closes the device on signalled shutdown", fd.closed == 1
      and s._deck is None, f"closed={fd.closed}")


# ---------------------------------------------------------------------------
print("\n[single-instance guard]")

d = tempfile.mkdtemp()
a = SingleInstance("guard", directory=d)
check("first acquire succeeds", a.acquire() is True)
check("acquire is idempotent for the holder", a.acquire() is True)
check("holder pid is recorded", a.holder_pid() == os.getpid(), str(a.holder_pid()))

b = SingleInstance("guard", directory=d)
check("second instance is refused", b.acquire() is False)
check("refused instance still reads the holder pid", b.holder_pid() == os.getpid())

a.release()
c = SingleInstance("guard", directory=d)
check("lock is reusable after release", c.acquire() is True)
c.release()

with SingleInstance("ctx", directory=d):
    raised = False
    try:
        with SingleInstance("ctx", directory=d):
            pass
    except AlreadyRunning:
        raised = True
    check("context manager raises AlreadyRunning when held", raised)
check("lock frees on context exit", SingleInstance("ctx", directory=d).acquire() is True)


# ---------------------------------------------------------------------------
print("\n[structured logging]")

lg = configure_logging(level=logging.DEBUG, stream=True)
check("configures the deck logger tree", lg.name == "deck")
check("applies the requested level", lg.level == logging.DEBUG)
check("does not propagate to root (no doubled lines)", lg.propagate is False)
owned1 = [h for h in lg.handlers if getattr(h, "_deck_owned", False)]

configure_logging(level=logging.INFO, stream=True)
owned2 = [h for h in lg.handlers if getattr(h, "_deck_owned", False)]
check("reconfigure does not stack handlers", len(owned1) == len(owned2),
      f"{len(owned1)} -> {len(owned2)}")
check("reconfigure updates the level", lg.level == logging.INFO)

logfile = os.path.join(tempfile.mkdtemp(), "cockpit.log")
configure_logging(level=logging.INFO, logfile=logfile, stream=False)
logging.getLogger("deck.example").info("structured line")
wrote = os.path.exists(logfile) and "structured line" in open(logfile).read()
check("writes timestamped lines to the logfile", wrote)


print(f"\n=== {ok} passed, {fail} failed ===")
sys.exit(1 if fail else 0)
