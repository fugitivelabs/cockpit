"""View + App — arrange components on the grid and run the loop.

Layering, deliberately thin:

    Surface     owns the device: draw images, read raw input          (verified)
    Component   renders one key, handles its press                    (components.py)
    View        maps components onto the 8 content keys, routes presses
    App         binds a Surface + View, runs tick/press, wires paging

App reserves the two touch points for paging when the View is paged, and
otherwise leaves them free. None of this knows a use case — a View is a list of
Components; what they mean is the caller's concern.
"""

from __future__ import annotations

import logging

from typing import Callable, Optional

from .components import Component, Static
from .render import BLANK, Slot, error_slot
from .surface import LONG_PRESS_S, TOUCH_LEFT, TOUCH_RIGHT, Surface

log = logging.getLogger(__name__)


class View:
    """A fixed placement of components onto content keys 0..n-1.

    `slots` may be a dict {index: Component} or a list (positional). Missing
    keys render blank. Override on_touch to use the touch points.
    """

    def __init__(self, components=None, key_count: int = 8):
        self.key_count = key_count
        self._by_index: dict[int, Component] = {}
        if isinstance(components, dict):
            self._by_index = dict(components)
        elif components:
            self._by_index = {i: c for i, c in enumerate(components)}

    def set(self, index: int, component: Component) -> None:
        self._by_index[index] = component

    def components(self) -> dict[int, Component]:
        """The components currently mapped to content keys (override for paging)."""
        return self._by_index

    def slots(self) -> dict[int, Slot]:
        out = {i: BLANK for i in range(self.key_count)}
        for i, c in self.components().items():
            if 0 <= i < self.key_count:
                # Per-component fault isolation: a render() that raises yields a
                # visible error tile for that one key, never a blank deck or a
                # dead loop. render() is contracted to be cheap and pure, so a
                # raise here is a real bug — surface it, don't swallow it.
                try:
                    out[i] = c.render()
                except Exception as e:
                    log.exception("component at key %d failed to render", i)
                    out[i] = error_slot(type(e).__name__)
        return out

    def press(self, index: int, long: bool) -> bool:
        c = self.components().get(index)
        if c is None:
            return False
        # A press handler that raises must not kill the loop either.
        try:
            return c.on_press(long)
        except Exception:
            log.exception("component at key %d failed to handle press", index)
            return False

    def on_touch(self, side: str) -> bool:
        """side is 'left' or 'right'. Return True if handled. Base: no-op."""
        return False


class PagedView(View):
    """Any number of components, shown a page at a time; touch points page."""

    def __init__(self, components=None, key_count: int = 8):
        seq = []
        if isinstance(components, dict):
            seq = [components[k] for k in sorted(components)]
        elif components:
            seq = list(components)
        super().__init__(None, key_count)
        self._all = seq
        self.page = 0

    def set_all(self, components) -> None:
        self._all = list(components)
        self.page %= max(1, self.pages)

    @property
    def pages(self) -> int:
        return max(1, (len(self._all) + self.key_count - 1) // self.key_count)

    def components(self) -> dict[int, Component]:
        self.page %= self.pages
        start = self.page * self.key_count
        chunk = self._all[start:start + self.key_count]
        return {i: c for i, c in enumerate(chunk)}

    def on_touch(self, side: str) -> bool:
        if self.pages <= 1:
            return False
        self.page = (self.page + (1 if side == "right" else -1)) % self.pages
        return True


class App:
    """Binds a Surface to a View and runs the loop.

    view can be swapped at runtime (app.view = other) — enough for drilling in
    and out. A formal view stack can come later if it earns it.
    """

    def __init__(self, surface: Optional[Surface] = None, view: Optional[View] = None,
                 interval: float = 1.0, fast_interval: float = 0.09):
        self.surface = surface or Surface()
        self.view = view or View()
        self.interval = interval
        self.fast_interval = fast_interval
        self._owns_surface = surface is None
        self._info: Optional[Callable[[], tuple]] = None

    def _interval(self) -> float:
        """Tick fast only while the view says something is moving.

        A View opts in by growing an `animating()` predicate; one that doesn't
        keeps the slow tick and behaves exactly as before. The fast rate is
        affordable because it does not imply repainting: renders are cached by
        Slot value and `flush()` diffs before writing, so a tick where nothing
        changed costs a few pure render() calls and no USB traffic at all.
        """
        wants = getattr(self.view, "animating", None)
        try:
            if callable(wants) and wants():
                return self.fast_interval
        except Exception:
            log.exception("view.animating() raised; falling back to slow tick")
        return self.interval

    def info(self, fn: Callable[[], tuple]) -> None:
        """Register an info-bar provider: fn() -> (text, sub) or (text, sub, bg, fg)."""
        self._info = fn

    def _paint(self) -> None:
        if self.view.key_count != self.surface.key_count:
            self.view.key_count = self.surface.key_count
        self.surface.show(self.view.slots())
        if self._info:
            got = self._info()
            if got:
                self.surface.set_info(*got)

    def _route(self, index: int, long: bool) -> None:
        if index == TOUCH_LEFT:
            if self.view.on_touch("left"):
                self._paint()
            return
        if index == TOUCH_RIGHT:
            if self.view.on_touch("right"):
                self._paint()
            return
        self.view.press(index, long)
        self._paint()   # reflect any state change immediately, not next tick

    def run(self) -> None:
        if self._owns_surface and self.surface._deck is None:
            self.surface.open()
        self.surface.on_press(self._route)
        # light the touch points if the view can page
        pages = getattr(self.view, "pages", 1)
        glow = (0, 90, 140) if pages > 1 else (0, 0, 0)
        self.surface.set_touch(TOUCH_LEFT, glow)
        self.surface.set_touch(TOUCH_RIGHT, glow)
        self._paint()
        self.surface.run(tick=self._paint, interval=self._interval)

    def __enter__(self):
        if self._owns_surface:
            self.surface.open()
        return self

    def __exit__(self, *exc):
        if self._owns_surface:
            self.surface.close()
        return False
