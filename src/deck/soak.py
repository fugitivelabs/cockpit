"""Soak / resilience harness — exercises Surface's reconnect path against real
disruption (unplug, sleep). Logs every transition with timestamps so the
recovery can be inspected after the fact.

    PYTHONPATH=.. python3 -m deck.soak [logfile]

Watch the deck: a ticking clock means it's alive. Then unplug the cable, wait,
replug — and read the log to see whether it detected the loss and recovered.
"""

import os
import sys
import tempfile
import time
from datetime import datetime

from deck import Slot, Surface
from deck.surface import TOUCH_LEFT

LOG = sys.argv[1] if len(sys.argv) > 1 else \
    os.path.join(tempfile.gettempdir(), "cockpit-soak.log")


def log(msg: str) -> None:
    line = f"{datetime.now().strftime('%H:%M:%S')}  {msg}"
    print(line, flush=True)
    with open(LOG, "a") as f:
        f.write(line + "\n")


class SoakSurface(Surface):
    """Surface that narrates its own disconnect/reconnect for the log.

    Reconnect is now non-blocking (the base marks the surface down and the loop
    retries open() on its own), so narration hangs off the transition callbacks
    rather than wrapping a blocking call. `disconnects`/`reconnects` live on the
    base class now.
    """

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._down_since = None
        self.on_disconnect(self._narrate_disconnect)
        self.on_reconnect(self._narrate_reconnect)

    def _narrate_disconnect(self):
        self._down_since = time.monotonic()
        log(f"!! DISCONNECT #{self.disconnects} detected — staying alive, "
            f"retrying open() on the loop…")

    def _narrate_reconnect(self):
        down = time.monotonic() - (self._down_since or time.monotonic())
        log(f"++ RECONNECTED #{self.reconnects} after {down:.1f}s down")


def main():
    open(LOG, "w").close()
    log(f"=== soak start (log: {LOG}) ===")

    presses = []
    flashes = {}          # index -> monotonic expiry; drives visible feedback

    def on_press(index, long):
        presses.append((index, long))
        flashes[index] = time.monotonic() + 0.8
        log(f"   PRESS idx={index}{' (long)' if long else ''} "
            f"[total {len(presses)}]")
        # paint the flash immediately from the reader thread for snappy feedback
        if index < 8:
            s.set_slot(index, Slot(label="✓", bg="#0A5A0A", fg="#FFFFFF"))

    s = SoakSurface(brightness=70)
    try:
        s.open()
        log(f"opened: {s._deck.deck_type()} serial={s._deck.get_serial_number()}")
    except Exception as e:
        log(f"FAILED to open at startup: {type(e).__name__}: {e}")
        return 1

    s.on_press(on_press)
    log("running. UNPLUG the cable, wait ~10s, REPLUG. Then press a key to")
    log("confirm input survives. Ctrl-C (or it self-stops after 5 min).")

    start = time.monotonic()
    ticks = 0
    last_heartbeat = 0.0
    try:
        while time.monotonic() - start < 300:
            elapsed = time.monotonic() - start
            # a changing clock guarantees a write every second, so a dead
            # device is detected within ~1s rather than silently ignored
            s.set_slot(0, Slot(label=datetime.now().strftime("%H:%M:%S"),
                               sub=f"up {elapsed:.0f}s", bg="#101820",
                               accent="#4CD964"))
            s.set_slot(1, Slot(label=f"D{s.disconnects}",
                               sub=f"R{s.reconnects}", bg="#12263A",
                               accent="#3FA7D6"))
            # clear expired flashes back to a labelled "press me" state
            now = time.monotonic()
            for i in range(2, 8):
                if flashes.get(i, 0) < now:
                    s.set_slot(i, Slot(label="press", bg="#181818", fg="#666"))
            s.set_touch(TOUCH_LEFT, (0, 90, 0))
            ticks += 1
            if elapsed - last_heartbeat >= 15:
                log(f"   heartbeat: up {elapsed:.0f}s, {ticks} ticks, "
                    f"{s.disconnects} disconnects, {s.reconnects} reconnects, "
                    f"{len(presses)} presses")
                last_heartbeat = elapsed
            time.sleep(1.0)
    except KeyboardInterrupt:
        pass
    finally:
        log(f"=== soak end: {s.disconnects} disconnects, {s.reconnects} "
            f"reconnects, {len(presses)} presses ===")
        try:
            s.close()
        except Exception as e:
            log(f"   cleanup note: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
