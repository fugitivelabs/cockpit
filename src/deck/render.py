"""Slot definitions and image rendering.

Rendering is the expensive half of driving the deck — measured at ~2 ms/key to
encode versus ~0.8 ms to push over USB. So images are cached by content, and a
Slot is a plain value object whose equality drives both the cache and the diff.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

from PIL import Image, ImageDraw, ImageFont
from StreamDeck.ImageHelpers import PILHelper

# macOS font candidates, best first. Falls back to PIL's bitmap font, which is
# legible but tiny — if you see cramped labels, the lookup failed.
_FONT_PATHS = (
    "/System/Library/Fonts/SFNSDisplay.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
)

_font_cache: dict[int, ImageFont.ImageFont] = {}


def font(size: int) -> ImageFont.ImageFont:
    """A truetype font at `size`, cached. Falls back to PIL's default."""
    if size in _font_cache:
        return _font_cache[size]
    for path in _FONT_PATHS:
        if os.path.exists(path):
            try:
                f = ImageFont.truetype(path, size)
                break
            except OSError:
                continue
    else:
        f = ImageFont.load_default()
    _font_cache[size] = f
    return f


@dataclass(frozen=True)
class Slot:
    """What one key should show. Immutable so it can be compared and hashed.

    A Slot carries no behaviour and no knowledge of what it represents — that
    belongs to the caller. `key` is an opaque caller-supplied identity used to
    tell "same thing, restyled" from "different thing", which matters when
    deciding whether a change is worth a repaint.
    """

    label: str = ""
    sub: str = ""                      # smaller second line
    bg: str = "#000000"
    fg: str = "#FFFFFF"
    accent: Optional[str] = None       # left edge bar; None = no bar
    badge: str = ""                    # small top-right marker
    bar: Optional[float] = None        # 0..1 progress bar along the bottom
    bar_color: str = "#3FA7D6"
    key: Optional[str] = None          # opaque caller identity

    def blank(self) -> bool:
        return not (self.label or self.sub or self.badge
                    or self.accent or self.bar is not None)


BLANK = Slot()


def error_slot(detail: str = "") -> Slot:
    """A visibly distinct tile for a key whose component failed to render.

    Fault isolation substitutes this for a single misbehaving key so one bad
    render never blanks the deck or crashes the loop. `detail` is truncated to
    fit — usually an exception type name, enough to point at the culprit.
    """
    return Slot(label="ERR", sub=detail[:18], bg="#3A0A0A", fg="#FF6B6B",
                key="__deck_error__")


ERROR_SLOT = error_slot()


def _fit(draw: ImageDraw.ImageDraw, text: str, max_w: int,
         start: int, min_size: int) -> tuple[str, ImageFont.ImageFont]:
    """Shrink then truncate until `text` fits `max_w`."""
    size = start
    while size > min_size:
        f = font(size)
        if draw.textlength(text, font=f) <= max_w:
            return text, f
        size -= 1
    f = font(min_size)
    if draw.textlength(text, font=f) <= max_w:
        return text, f
    ell = "…"
    cut = text
    while cut and draw.textlength(cut + ell, font=f) > max_w:
        cut = cut[:-1]
    return (cut + ell) if cut else "", f


def render(deck, slot: Slot) -> Image.Image:
    """Render a Slot to a PIL image sized for this deck's keys."""
    img = PILHelper.create_key_image(deck, background=slot.bg)
    d = ImageDraw.Draw(img)
    w, h = img.width, img.height

    if slot.accent:
        d.rectangle([0, 0, 5, h], fill=slot.accent)

    pad = 9 if slot.accent else 5
    avail = w - pad - 5

    if slot.label and slot.sub:
        text, f = _fit(d, slot.label, avail, 19, 10)
        d.text((pad, h // 2 - 4), text, anchor="lm", fill=slot.fg, font=f)
        sub, sf = _fit(d, slot.sub, avail, 13, 8)
        d.text((pad, h // 2 + 16), sub, anchor="lm", fill=slot.fg, font=sf)
    elif slot.label:
        text, f = _fit(d, slot.label, avail, 21, 10)
        d.text((pad + avail // 2, h // 2), text, anchor="mm", fill=slot.fg, font=f)

    if slot.badge:
        bf = font(15)
        d.text((w - 6, 5), slot.badge, anchor="ra", fill=slot.fg, font=bf)

    if slot.bar is not None:
        frac = max(0.0, min(1.0, slot.bar))
        y0 = h - 7
        d.rectangle([pad, y0, w - 5, h - 3], fill="#2A2A2A")
        if frac > 0:
            d.rectangle([pad, y0, pad + int((w - 5 - pad) * frac), h - 3],
                        fill=slot.bar_color)

    return img


def tile(deck, image: Image.Image, gap: int = 0) -> dict[int, Image.Image]:
    """Split one image across the whole keypad, one tile per key.

    `gap` inserts virtual spacing so the picture accounts for the physical
    bezel between keys — the image is sampled as if the gaps were part of it,
    which keeps lines continuous to the eye.
    """
    rows, cols = deck.key_layout()
    fmt = deck.key_image_format()
    kw, kh = fmt["size"]

    full_w = cols * kw + (cols - 1) * gap
    full_h = rows * kh + (rows - 1) * gap
    src = image.convert("RGB").resize((full_w, full_h), Image.LANCZOS)

    out = {}
    for r in range(rows):
        for c in range(cols):
            x, y = c * (kw + gap), r * (kh + gap)
            out[r * cols + c] = src.crop((x, y, x + kw, y + kh))
    return out


def render_info(deck, text: str, sub: str = "", bg: str = "#000000",
                fg: str = "#FFFFFF") -> Image.Image:
    """Render the info bar. Neo supports full-panel writes only, no regions."""
    img = PILHelper.create_screen_image(deck, background=bg)
    d = ImageDraw.Draw(img)
    if sub:
        d.text((img.width // 2, img.height // 2 - 10), text,
               anchor="mm", fill=fg, font=font(24))
        d.text((img.width // 2, img.height // 2 + 14), sub,
               anchor="mm", fill=fg, font=font(15))
    else:
        d.text((img.width // 2, img.height // 2), text,
               anchor="mm", fill=fg, font=font(30))
    return img
