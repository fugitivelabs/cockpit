"""Components — self-contained units that render to a Slot and handle presses.

A Component is the composable unit above a bare Slot. The contract is
deliberately tiny:

    render() -> Slot          cheap, pure; formats current state, does NO I/O
    on_press(long) -> bool    optional; return True if the press was handled

The loop re-renders every visible component each tick and after each press.
That is efficient because Surface caches by Slot value and diffs before
pushing — an unchanged component costs a cache hit and a skipped write. The
one rule that keeps it fast: render() must stay cheap and pure. State arrives
from OUTSIDE (a channel updates the component's fields); render() only formats
what is already there.

Nothing here knows about any use case. A Component renders a key; what the key
means is the caller's business.
"""

from __future__ import annotations

from typing import Callable, Optional

from .render import BLANK, Slot


class Component:
    """Base unit. Subclass and implement render(); override on_press if active."""

    def render(self) -> Slot:
        raise NotImplementedError

    def on_press(self, long: bool) -> bool:
        return False


class Static(Component):
    """A fixed Slot. Useful for labels, spacers, decoration."""

    def __init__(self, slot: Slot = BLANK):
        self.slot = slot

    def render(self) -> Slot:
        return self.slot


class Button(Component):
    """A key with press behaviour.

    `slot` may be a Slot or a zero-arg callable returning one, so a button can
    be static or live without a separate class. `on_press` / `on_long` are
    zero-arg callbacks; if only `on_press` is given it also handles long press.
    """

    def __init__(self, slot, on_press: Optional[Callable[[], None]] = None,
                 on_long: Optional[Callable[[], None]] = None):
        self._slot = slot
        self._on_press = on_press
        self._on_long = on_long

    def render(self) -> Slot:
        return self._slot() if callable(self._slot) else self._slot

    def on_press(self, long: bool) -> bool:
        cb = self._on_long if (long and self._on_long) else self._on_press
        if cb:
            cb()
            return True
        return False


class Live(Component):
    """A component whose look is a render function, optionally pressable.

    The workhorse for anything that changes: clocks, meters, status keys. Keep
    the function cheap — it runs every tick for every visible Live component.
    """

    def __init__(self, fn: Callable[[], Slot],
                 on_press: Optional[Callable[[bool], bool]] = None):
        self._fn = fn
        self._on_press = on_press

    def render(self) -> Slot:
        return self._fn()

    def on_press(self, long: bool) -> bool:
        if self._on_press:
            return bool(self._on_press(long))
        return False


def meter(value_fn: Callable[[], float], label_fn=None,
          color: str = "#3FA7D6", warn: float = 0.8,
          warn_color: str = "#E8B923", bg: str = "#101010") -> Live:
    """A labelled 0..1 progress key. `value_fn` returns the fraction to fill.

    Turns `warn_color` past the `warn` threshold — the pattern behind a
    context-window key that goes amber near full. Fully generic: it knows
    nothing about tokens, just a number between 0 and 1.
    """
    def render() -> Slot:
        v = max(0.0, min(1.0, value_fn()))
        lbl = label_fn() if label_fn else f"{int(v * 100)}%"
        c = warn_color if v >= warn else color
        return Slot(label=lbl, bg=bg, bar=v, bar_color=c, fg="#FFFFFF")
    return Live(render)
