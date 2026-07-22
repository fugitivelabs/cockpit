"""Interactive hardware tests for the paths that unit tests can't reach.

Instruments the raw HID callback alongside the dispatched one, so debounce is
measured rather than assumed. Instructions render on the deck itself.

    PYTHONPATH=.. python3 -m deck.hwtest
"""

import collections
import sys
import time

from deck import BLANK, Slot, Surface, TOUCH_LEFT, TOUCH_RIGHT
from deck.surface import KEY_DEBOUNCE_S, LONG_PRESS_S, TOUCH_DEBOUNCE_S

RESULTS = []


class Instrumented(Surface):
    """Surface that also counts raw hardware events."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.raw = collections.Counter()
        self.raw_times = collections.defaultdict(list)

    def _on_raw(self, deck, index, state):
        self.raw[(index, bool(state))] += 1
        self.raw_times[index].append(time.monotonic())
        super()._on_raw(deck, index, state)

    def reset_counts(self):
        self.raw.clear()
        self.raw_times.clear()


def briefing(s, words, seconds=4.0):
    """Show an instruction as one big word per key — legible at arm's length.

    The info bar is only 248x58; a full sentence there is unreadable, which
    invalidated an earlier run of these tests. One word per 96x96 key is not.
    """
    slots = {}
    for i in range(8):
        slots[i] = Slot(label=words[i], bg="#101820", fg="#FFFFFF") \
            if i < len(words) and words[i] else BLANK
    s.show(slots)
    s.set_touch(TOUCH_LEFT, (0, 0, 0))
    s.set_touch(TOUCH_RIGHT, (0, 0, 0))
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        s.set_info("READ THIS", f"starting in {end - time.monotonic():.0f}s")
        time.sleep(0.2)


def phase(s, title, words, seconds, highlight=None, touch=False, hold=False):
    """Brief the user on the keys, then run one timed measurement phase."""
    print(f"\n--- {title} ---")
    print(f"    instruction: {' '.join(w for w in words if w)}")

    briefing(s, words, seconds=7.0)

    # explicit 3-2-1 so the start is never a surprise
    for n in (3, 2, 1):
        s.show({i: (Slot(label=str(n), bg="#2A2000", fg="#E8B923")
                    if i == 3 else BLANK) for i in range(8)})
        s.set_info("GET READY", f"{n}")
        time.sleep(1.0)

    got = []
    s.on_press(lambda i, lp: got.append((i, lp)))
    s.reset_counts()

    slots = {}
    for i in range(8):
        if highlight is not None and i == highlight:
            slots[i] = Slot(label="NOW", bg="#0A3A0A", accent="#4CD964")
        elif touch and i in (0, 4):
            slots[i] = Slot(label="<-", bg="#001830", fg="#3FA7D6")
        else:
            slots[i] = BLANK
    s.show(slots)
    s.set_touch(TOUCH_LEFT, (0, 150, 255) if touch else (0, 0, 0))
    s.set_touch(TOUCH_RIGHT, (0, 0, 0))

    verb = "HOLD" if hold else "GO"
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        s.set_info(verb, f"{end - time.monotonic():.0f}s left")
        time.sleep(0.2)
    s.set_info("STOP", "")
    time.sleep(0.4)
    return got


def report(name, ok, detail):
    RESULTS.append((ok, name, detail))
    print(f"    {'PASS' if ok else 'FAIL'}  {name} — {detail}")


def t_touch_hold(s):
    """Does a held touch pad auto-repeat? Settles the debounce question."""
    got = phase(s, "Touch pad HELD",
                ["HOLD", "LEFT", "TOUCH", "PAD", "WHOLE", "TIME", "", ""],
                8, touch=True, hold=True)
    raw = s.raw[(TOUCH_LEFT, True)]
    disp = [g for g in got if g[0] == TOUCH_LEFT]
    ts = s.raw_times[TOUCH_LEFT]
    if raw >= 2:
        gaps = [b - a for a, b in zip(ts, ts[1:])]
        avg = sum(gaps) / len(gaps)
        print(f"        repeat interval: {avg*1000:.0f} ms (~{1/avg:.0f} Hz)")
        report("touch auto-repeat", True,
               f"REPEATS — {raw} raw events over 8s held, "
               f"dispatched {len(disp)}")
    elif raw == 1:
        report("touch auto-repeat", True,
               "does NOT repeat — 1 raw event for an 8s hold")
    else:
        report("touch auto-repeat", False,
               "no events at all — pad not actually held?")


def t_touch_rapid(s):
    """Do fast repeated taps all get through, or does the guard eat them?"""
    got = phase(s, "Touch pad RAPID TAPS",
                ["TAP", "LEFT", "PAD", "FAST", "COUNT", "YOUR", "TAPS", ""],
                8, touch=True)
    raw = s.raw[(TOUCH_LEFT, True)]
    disp = [g for g in got if g[0] == TOUCH_LEFT]
    report("touch rapid taps not dropped",
           raw > 0 and len(disp) == raw,
           f"raw {raw} -> dispatched {len(disp)} "
           f"(guard {TOUCH_DEBOUNCE_S*1000:.0f}ms; equal == nothing dropped)")


def t_key_rapid(s):
    got = phase(s, "Key RAPID TAPS",
                ["TAP", "TOP", "LEFT", "KEY", "FAST", "", "", ""],
                8, highlight=0)
    down, up = s.raw[(0, True)], s.raw[(0, False)]
    disp = [g for g in got if g[0] == 0]
    report("key rapid taps 1:1",
           down > 0 and len(disp) == down,
           f"raw {down} down / {up} up -> dispatched {len(disp)} "
           f"(guard {KEY_DEBOUNCE_S*1000:.0f}ms)")


def t_long(s):
    got = phase(s, "LONG press",
                ["HOLD", "THIS", "KEY", "2 SEC", "THEN", "LET", "GO", ""],
                10, highlight=3, hold=True)
    longs = [g for g in got if g[0] == 3 and g[1]]
    shorts = [g for g in got if g[0] == 3 and not g[1]]
    report("long press detected", bool(longs) and not shorts,
           f"{len(longs)} long, {len(shorts)} short "
           f"(threshold {LONG_PRESS_S}s)")


def t_short(s):
    got = phase(s, "SHORT press",
                ["TAP", "THIS", "KEY", "3X", "QUICK", "", "", ""],
                8, highlight=5)
    longs = [g for g in got if g[0] == 5 and g[1]]
    shorts = [g for g in got if g[0] == 5 and not g[1]]
    report("short press not misread", bool(shorts) and not longs,
           f"{len(shorts)} short, {len(longs)} long (want 0 long)")


def t_diff(s):
    """No human needed."""
    print("\n--- Diffing (automatic) ---")
    target = {i: Slot(label=str(i), bg="#202020") for i in range(8)}
    s.show(target)
    again, third = s.flush(), s.flush()
    report("diff suppresses redundant writes", again == 0 and third == 0,
           f"repeat flushes wrote {again} and {third} keys (want 0)")
    with s._lock:
        s._desired[4] = Slot(label="X", bg="#4A1010")
    n = s.flush()
    report("diff writes only the change", n == 1,
           f"changed 1 slot -> wrote {n} key(s)")


TESTS = {
    "touch-hold": t_touch_hold,
    "touch-rapid": t_touch_rapid,
    "key-rapid": t_key_rapid,
    "long": t_long,
    "short": t_short,
    "diff": t_diff,
}


def main():
    names = [a for a in sys.argv[1:] if not a.startswith("-")] or list(TESTS)
    unknown = [n for n in names if n not in TESTS]
    if unknown:
        print(f"unknown test(s): {', '.join(unknown)}")
        print(f"available: {', '.join(TESTS)}")
        return 2

    with Instrumented(brightness=75) as s:
        for i, name in enumerate(names):
            if i:
                s.show({j: BLANK for j in range(8)})
                s.set_info("next test", "starting…")
                time.sleep(2.5)
            TESTS[name](s)
        s.show({i: BLANK for i in range(8)})
        s.set_info("done")

    print("\n=== summary ===")
    for ok, name, detail in RESULTS:
        print(f"  {'PASS' if ok else 'FAIL'}  {name} — {detail}")
    bad = [n for ok, n, _ in RESULTS if not ok]
    print(f"\n  {len(RESULTS)-len(bad)}/{len(RESULTS)} passed")
    if bad:
        print(f"  failed: {', '.join(bad)}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main() or 0)
