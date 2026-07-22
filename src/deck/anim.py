"""Time-varying values for animated slots — the mechanism, not the policy.

The problem this solves: `Surface` caches rendered images by `Slot` value and
skips the USB write when a slot is unchanged. That is what makes the loop cheap,
and a naively animated slot destroys it — a continuously-varying brightness
means every tick produces a slot nothing has ever seen, so every tick pays a
full encode (~2 ms/key) and a full write.

**The fix is quantization.** These helpers return values snapped to a small
number of buckets, so an animated key cycles through a bounded set of distinct
Slots — at `STEPS`=12 and a 2.4 s period, twelve cached images, then it repeats
forever with a 100% cache hit rate. The diff also keeps working: within a bucket
the slot compares equal and no write happens at all, so a slow breathe costs a
handful of writes per second rather than one per tick.

Nothing here knows what is being animated or why. A consumer decides that a
blocked session breathes; this module only knows how to make a number wobble.
"""

from __future__ import annotations

import math
import time

# Buckets per cycle. This is the one number trading smoothness against cache
# size, and measurement says smoothness is nearly free: at 24 a breathing key
# resolves to ~11 distinct Slots — eleven cached images, then a 100% hit rate
# forever — while re-rendering all eight keys costs 25 us per tick, or 0.03% of
# a core at the fast tick rate. A raised cosine is symmetric, so the distinct
# count lands near half of STEPS rather than at it.
#
# Do not read this as "quantization barely matters". WITHOUT it every frame is a
# slot nothing has seen, so every frame pays a full ~1.3 ms rasterise per key
# and a full USB write — roughly a hundredfold increase, on a loop that never
# stops.
STEPS = 24


def quantize(value: float, steps: int = STEPS) -> float:
    """Snap a 0..1 value onto `steps` buckets. The whole trick lives here."""
    if steps <= 1:
        return 0.0
    return round(value * (steps - 1)) / (steps - 1)


def phase(period: float, clock=time.monotonic, steps: int = STEPS,
          offset: float = 0.0) -> float:
    """Position within a repeating `period`-second cycle, as a 0..1 bucket.

    `offset` shifts the cycle, so several keys animating on the same period can
    be deliberately in phase (a board that breathes together) or deliberately
    not. In phase is usually right: keys pulsing out of step read as noise.
    """
    if period <= 0:
        return 0.0
    t = (clock() / period + offset) % 1.0
    return quantize(t, steps)


def breathe(period: float = 2.4, lo: float = 0.6, hi: float = 1.0,
            clock=time.monotonic, steps: int = STEPS,
            offset: float = 0.0) -> float:
    """A smooth `lo`..`hi` oscillation — the sustain signal.

    Raised cosine rather than a triangle wave: the eased turnaround at each end
    is what makes it read as breathing rather than blinking, and blinking is the
    thing that becomes unbearable to sit next to for a working day.
    """
    p = phase(period, clock, steps, offset)
    eased = (1.0 - math.cos(2.0 * math.pi * p)) / 2.0
    return quantize(lo + (hi - lo) * eased, steps)


def flash(since, duration: float = 1.4, peak: float = 1.9, rest: float = 1.0,
          clock=time.monotonic, steps: int = STEPS) -> float:
    """A decaying spike from `peak` back to `rest` — the onset signal.

    `since` is a monotonic timestamp of the moment that deserves attention, or
    None for "nothing happened". Returns `rest` once `duration` has elapsed, so
    a caller can hold the timestamp forever and let this decide when the flash
    is over.

    Onset and sustain do different jobs and you want both: a flash alone is
    missed if you were not looking, and a sustain alone is wallpaper by the end
    of the first day.
    """
    if since is None:
        return rest
    elapsed = clock() - since
    if elapsed < 0 or elapsed >= duration:
        return rest
    decay = 1.0 - (elapsed / duration)
    return quantize(rest + (peak - rest) * (decay ** 2), steps)


def blink(period: float = 1.0, duty: float = 0.5, clock=time.monotonic) -> bool:
    """A hard on/off square wave. For a genuine alarm, not for ambience."""
    if period <= 0:
        return True
    return ((clock() / period) % 1.0) < duty


__all__ = ["STEPS", "quantize", "phase", "breathe", "flash", "blink"]
