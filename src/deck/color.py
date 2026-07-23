"""Colour arithmetic. No palette, no meanings — just the maths.

Deliberately empty of semantics. `deck/` must never learn that red means a
blocked session; it only knows how to move a colour toward white, mix two
colours, or scale a brightness. What any colour *means* is the consumer's
business, which is what keeps this library shareable.

Everything takes and returns `#RRGGBB` strings, because that is what `Slot`
carries and what PIL accepts, and because a hex string is hashable — the render
cache is keyed on Slot equality, so colours have to be values.
"""

from __future__ import annotations

from typing import Tuple

RGB = Tuple[int, int, int]


def parse(color: str) -> RGB:
    """'#RRGGBB' -> (r, g, b). Raises ValueError on anything else."""
    raw = color.lstrip("#")
    if len(raw) == 3:                      # '#abc' shorthand
        raw = "".join(c * 2 for c in raw)
    if len(raw) != 6:
        raise ValueError(f"not a hex colour: {color!r}")
    return (int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16))


def to_hex(rgb: RGB) -> str:
    r, g, b = (max(0, min(255, int(round(c)))) for c in rgb)
    return "#%02X%02X%02X" % (r, g, b)


def _safe(fn):
    """Colour helpers are called from render(), which must not raise.

    A malformed colour is a bug in the caller, but the cost of surfacing it as
    an exception is a dead key — or, through Surface's fault isolation, an ERR
    tile where a session should be. Passing the input through unchanged makes
    the mistake visible without breaking the board.
    """
    def wrapped(color, *a, **kw):
        try:
            return fn(color, *a, **kw)
        except (ValueError, TypeError, AttributeError):
            return color
    return wrapped


@_safe
def lighten(color: str, amount: float = 0.3) -> str:
    """Move toward white by `amount` (0..1).

    Note this desaturates: at amount=0.3 a deep red lands on a muted mauve. That
    is fine for furniture and wrong for anything whose hue carries meaning — use
    `scale` or a frame if the colour has a job.
    """
    r, g, b = parse(color)
    f = lambda c: c + (255 - c) * amount
    return to_hex((f(r), f(g), f(b)))


@_safe
def darken(color: str, amount: float = 0.3) -> str:
    """Move toward black by `amount` (0..1)."""
    r, g, b = parse(color)
    f = lambda c: c * (1.0 - amount)
    return to_hex((f(r), f(g), f(b)))


@_safe
def scale(color: str, factor: float) -> str:
    """Multiply brightness by `factor`, preserving hue and saturation.

    This is the one to animate on: unlike `lighten`, scaling keeps the ratios
    between channels, so a pulsing red stays red instead of washing to pink.
    """
    r, g, b = parse(color)
    return to_hex((r * factor, g * factor, b * factor))


def mix(a: str, b: str, t: float = 0.5) -> str:
    """Blend `a` toward `b` by `t` (0..1). t=0 is a, t=1 is b."""
    try:
        ar, ag, ab = parse(a)
        br, bg, bb = parse(b)
    except (ValueError, TypeError, AttributeError):
        return a
    t = max(0.0, min(1.0, t))
    return to_hex((ar + (br - ar) * t,
                   ag + (bg - ag) * t,
                   ab + (bb - ab) * t))


def over(color: str, alpha: float, base: str = "#000000") -> str:
    """Composite `color` at `alpha` over an opaque `base`.

    Slots are opaque — there is no alpha channel on a key image — so a "10%
    tint" has to be resolved to a concrete colour at declaration time. This is
    that resolution.
    """
    return mix(base, color, alpha)


def luminance(color: str) -> float:
    """Perceptual luminance, 0..1. Rec. 709 coefficients."""
    try:
        r, g, b = parse(color)
    except (ValueError, TypeError, AttributeError):
        return 0.0
    return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255.0


def readable_on(bg: str, light: str = "#FFFFFF", dark: str = "#000000") -> str:
    """Pick whichever of `light`/`dark` will read on `bg`."""
    return dark if luminance(bg) > 0.5 else light


__all__ = ["parse", "to_hex", "lighten", "darken", "scale", "mix", "over",
           "luminance", "readable_on", "RGB"]
