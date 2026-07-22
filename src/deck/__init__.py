"""deck — a light declarative layer over a Stream Deck.

Describe what the keys should show; the Surface works out what needs to hit USB.

    from deck import Surface, Slot

    with Surface() as s:
        s.on_press(lambda i, long: print("pressed", i, "long" if long else ""))
        s.show({0: Slot(label="hello"), 1: Slot(label="world", accent="#4A9")})
        s.set_info("ready")
        s.run()

Knows nothing about what slots mean — that belongs to the layer above.
"""

import logging as _logging

from .app import App, PagedView, View
from .components import Button, Component, Live, Static, meter
from .lifecycle import AlreadyRunning, SingleInstance, configure_logging
from .render import BLANK, Slot, error_slot, render, render_info, tile
from .surface import (
    LONG_PRESS_S,
    TOUCH_LEFT,
    TOUCH_RIGHT,
    DeckUnavailable,
    Surface,
)

# Library convention: never configure logging ourselves; just make sure the
# stdlib stays quiet until a consumer opts in via configure_logging().
_logging.getLogger("deck").addHandler(_logging.NullHandler())

__all__ = [
    # device + rendering
    "Surface",
    "Slot",
    "BLANK",
    "render",
    "render_info",
    "tile",
    "TOUCH_LEFT",
    "TOUCH_RIGHT",
    "LONG_PRESS_S",
    "DeckUnavailable",
    "error_slot",
    # component framework
    "Component",
    "Static",
    "Button",
    "Live",
    "meter",
    "View",
    "PagedView",
    "App",
    # lifecycle
    "configure_logging",
    "SingleInstance",
    "AlreadyRunning",
]
