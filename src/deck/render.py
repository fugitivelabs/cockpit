"""Slot definitions and image rendering.

Rendering is the expensive half of driving the deck — measured at ~2 ms/key to
encode versus ~0.8 ms to push over USB. So images are cached by content, and a
Slot is a plain value object whose equality drives both the cache and the diff.

A Slot carries a small drawing vocabulary rather than a picture: a field, an
edge rule, a perimeter frame, a left accent, two lines of type, a badge, and a
hairline meter. Those are primitives, not roles — this module has no opinion on
what a red rule means. Consumers assemble them into a language; see
`cockpit/dashboard.py` for the one this repo ships.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from PIL import Image, ImageDraw, ImageFont
from StreamDeck.ImageHelpers import PILHelper

from .color import scale

# Font roles, each a fallback chain of (path, face index). The chain matters:
# the previous version listed only SFNSDisplay.ttf, which has not shipped on
# macOS for several releases, so every lookup fell through to Helvetica *Regular*
# and the deck had no bold on it at all. Faces inside a .ttc need an index —
# asking for Helvetica.ttc without one silently gets you Regular.
#
# Condensed is the workhorse: a 96 px key is width-bound, not height-bound, so a
# condensed face buys several points of size on the same string, and size is what
# makes a label readable from a foot away.
_FACES = {
    "display": (                                   # names, answers — heavy
        ("/System/Library/Fonts/Avenir Next Condensed.ttc", 8),   # Heavy
        ("/System/Library/Fonts/HelveticaNeue.ttc", 9),           # Condensed Black
        ("/System/Library/Fonts/HelveticaNeue.ttc", 1),           # Bold
        ("/System/Library/Fonts/Helvetica.ttc", 1),               # Bold
    ),
    "value": (                                     # action-key values — demibold
        ("/System/Library/Fonts/Avenir Next Condensed.ttc", 2),   # Demi Bold
        ("/System/Library/Fonts/HelveticaNeue.ttc", 4),           # Condensed Bold
        ("/System/Library/Fonts/Helvetica.ttc", 1),
    ),
    "caption": (                                   # tracked small caps
        ("/System/Library/Fonts/Avenir Next Condensed.ttc", 5),   # Medium
        ("/System/Library/Fonts/HelveticaNeue.ttc", 10),          # Medium
        ("/System/Library/Fonts/Helvetica.ttc", 0),
    ),
    "num": (                                       # tabular numerals
        ("/System/Library/Fonts/Menlo.ttc", 1),                   # Bold
        ("/System/Library/Fonts/SFNSMono.ttf", 0),
        ("/System/Library/Fonts/Courier.ttc", 1),
    ),
    "ui": (                                        # everything else
        ("/System/Library/Fonts/HelveticaNeue.ttc", 10),          # Medium
        ("/System/Library/Fonts/Helvetica.ttc", 0),
    ),
}

_font_cache: dict[tuple, ImageFont.ImageFont] = {}


def font(size: int, role: str = "ui") -> ImageFont.ImageFont:
    """A truetype font at `size` for a named role, cached.

    Falls back through the role's chain and finally to PIL's bitmap default,
    which is legible but tiny — cramped labels mean every candidate was missing.
    """
    key = (role, size)
    if key in _font_cache:
        return _font_cache[key]
    f = None
    for path, index in _FACES.get(role, _FACES["ui"]):
        if not os.path.exists(path):
            continue
        try:
            f = ImageFont.truetype(path, size, index=index)
            break
        except OSError:
            continue
    if f is None:
        f = ImageFont.load_default()
    _font_cache[key] = f
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
    accent_w: int = 6
    badge: str = ""                    # small top-right marker
    bar: Optional[float] = None        # 0..1 meter along the bottom edge
    bar_color: str = "#3FA7D6"
    bar_track: str = "#1A1D21"         # unfilled remainder; needs to suit `bg`
    key: Optional[str] = None          # opaque caller identity

    # --- the added vocabulary -------------------------------------------
    rule: Optional[str] = None         # top edge rule; thickness carries weight
    rule_h: int = 4
    # Bottom edge band. The mirror of `rule`, and the one to reach for when a
    # marker must NOT disturb the text: `rule` pushes the label down by its own
    # height, so toggling it makes the title jump between two positions. `foot`
    # costs the label nothing, because the label is anchored to the top.
    foot: Optional[str] = None
    foot_h: int = 6
    frame: Optional[str] = None        # full perimeter — a distinct key *class*
    frame_w: int = 3
    icon: str = ""                     # a drawn glyph; see ICONS
    icon_color: Optional[str] = None   # defaults to fg
    caps: bool = False                 # draw `sub` as tracked small caps
    align: str = "left"                # "left" (two lines) | "center" (value+label)
    sub_fg: Optional[str] = None       # defaults to a dimmed fg
    # Brightness multiplier for the FIELD ONLY. This is the animation hook; see
    # deck/anim.py.
    #
    # Text and edges (rule, frame, accent) are deliberately excluded. Text,
    # because a pulse must never make a label harder to read at its dim end.
    # Edges, because they carry structure rather than state — and structure
    # belongs to a different question than whatever is animating. Dimming them
    # too meant a white "you are here" frame turned grey every time the tile
    # underneath it happened to breathe, which is precisely backwards: the one
    # tile you are looking at got the weakest focus indicator.
    pulse: float = 1.0

    def blank(self) -> bool:
        return not (self.label or self.sub or self.badge or self.accent
                    or self.rule or self.foot or self.frame
                    or self.bar is not None)


BLANK = Slot()


def error_slot(detail: str = "") -> Slot:
    """A visibly distinct tile for a key whose component failed to render.

    Fault isolation substitutes this for a single misbehaving key so one bad
    render never blanks the deck or crashes the loop. `detail` is truncated to
    fit — usually an exception type name, enough to point at the culprit.

    Magenta, not red: red is a colour a consumer is likely to have spent on
    something meaningful, and "the renderer broke" must not be mistakable for a
    real state. Nothing else on this deck is magenta.
    """
    return Slot(label="ERR", sub=detail[:18], bg="#2A0A24", fg="#FF6BD6",
                rule="#FF6BD6", rule_h=4, caps=True, key="__deck_error__")


ERROR_SLOT = error_slot()


# Truncation marker. Deliberately three ASCII periods rather than U+2026: the
# condensed faces draw their ellipsis as raised dots, which at 11 px reads as a
# stray quote mark rather than "there is more text here".
ELLIPSIS = "..."


# Icons are DRAWN, never set as text. The condensed faces this renderer uses
# have no ✓ or ✗ — asking for one yields a .notdef box, and a tofu square on a
# key that answers a permission prompt is worse than no icon at all. Vectors
# also stay crisp at any size and need no font to be installed.
#
# Shapes only. What a check *means* is the consumer's business.
ICONS = ("check", "check-double", "cross", "plus", "dot", "bar")


def draw_icon(img: Image.Image, name: str, cx: float, cy: float,
              size: float, color: str) -> None:
    """Draw a named shape centred on (cx, cy), fitting a `size` box.

    **Supersampled, because PIL does not antialias lines.** Drawn directly, a
    stroke at an arbitrary angle stair-steps: the cross reads crisp because its
    two segments are exactly 45 degrees and land on whole pixels, while a
    checkmark's shallow leg does not, so the two sat side by side looking like
    they came from different sets. Rendering the shape into a mask at SS times
    the size and downsampling with LANCZOS gives every angle the same clean
    edge, then the colour is pasted through that mask.

    The cost is a few hundred microseconds on a cache MISS only — an icon is
    part of the Slot value, so a key that keeps showing the same icon never
    redraws it at all.
    """
    ss = 4
    box = max(8, int(size))
    pad = 4
    dim = (box + pad * 2) * ss
    mask = Image.new("L", (dim, dim), 0)
    m = ImageDraw.Draw(mask)
    mid = dim / 2.0
    s = (box / 2.0) * ss
    w = max(2, int(size * 0.155)) * ss

    def tick(ox: float = 0.0, scale: float = 1.0) -> None:
        m.line([(mid + (-0.50 * scale + ox) * s, mid + 0.02 * scale * s),
                (mid + (-0.13 * scale + ox) * s, mid + 0.40 * scale * s),
                (mid + (0.52 * scale + ox) * s, mid - 0.42 * scale * s)],
               fill=255, width=w, joint="curve")

    if name == "check":
        tick()
    elif name == "check-double":
        # Two ticks, the trailing one behind: "and again, and again" — the shape
        # for an approval that also widens permission.
        tick(-0.46, 0.80)
        tick(0.34, 0.80)
    elif name == "cross":
        m.line([(mid - 0.46 * s, mid - 0.46 * s), (mid + 0.46 * s, mid + 0.46 * s)],
               fill=255, width=w)
        m.line([(mid - 0.46 * s, mid + 0.46 * s), (mid + 0.46 * s, mid - 0.46 * s)],
               fill=255, width=w)
    elif name == "plus":
        m.line([(mid - 0.5 * s, mid), (mid + 0.5 * s, mid)], fill=255, width=w)
        m.line([(mid, mid - 0.5 * s), (mid, mid + 0.5 * s)], fill=255, width=w)
    elif name == "dot":
        m.ellipse([mid - s * 0.4, mid - s * 0.4, mid + s * 0.4, mid + s * 0.4],
                  fill=255)
    elif name == "bar":
        m.rectangle([mid - s * 0.5, mid - w / 2, mid + s * 0.5, mid + w / 2],
                    fill=255)
    else:
        return

    flat = mask.resize((box + pad * 2, box + pad * 2), Image.LANCZOS)
    img.paste(color, (int(cx - (box / 2 + pad)), int(cy - (box / 2 + pad))), flat)


def ellipsize(d: ImageDraw.ImageDraw, text: str, f, max_w: float,
              track: float = 0.0) -> str:
    """Clamp one line to `max_w`, appending the marker if it had to cut.

    Needed even after wrap(): word wrapping cannot break a single token, so a
    long path or URL is one "word" that sails past the right edge. Every line
    that reaches a draw call goes through here.
    """
    if _tracked_w(d, text, f, track) <= max_w:
        return text
    cut = text
    while cut and _tracked_w(d, cut + ELLIPSIS, f, track) > max_w:
        cut = cut[:-1]
    return (cut + ELLIPSIS) if cut else ""


def wrap(d: ImageDraw.ImageDraw, text: str, role: str, max_w: float,
         max_lines: int, start: int, floor: int, track: float = 0.0):
    """Break `text` onto at most `max_lines`, shrinking until it fits.

    Returns (lines, font). The last line is ellipsized if even `floor` cannot
    hold the remainder — truncation is still possible, it is just the last
    resort rather than the first thing that happens at 25 characters.
    """
    words = text.split()
    if not words:
        return [], font(start, role)
    for size in range(start, floor - 1, -1):
        f = font(size, role)
        lines, cur = [], ""
        for word in words:
            trial = f"{cur} {word}".strip()
            if cur and _tracked_w(d, trial, f, track) > max_w:
                lines.append(cur)
                cur = word
                if len(lines) == max_lines:
                    break
            else:
                cur = trial
        else:
            if cur:
                lines.append(cur)
            if len(lines) <= max_lines:
                return lines, f
    f = font(floor, role)
    lines, cur = [], ""
    for word in words:
        trial = f"{cur} {word}".strip()
        if cur and _tracked_w(d, trial, f, track) > max_w:
            lines.append(cur)
            cur = word
        else:
            cur = trial
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        last = lines[-1]
        while last and _tracked_w(d, last + ELLIPSIS, f, track) > max_w:
            last = last[:-1]
        lines[-1] = last + ELLIPSIS
    return lines, f


def _tracked_w(d: ImageDraw.ImageDraw, text: str, f, track: float) -> float:
    if not text:
        return 0.0
    return sum(d.textlength(c, font=f) + track for c in text) - track


def _draw_tracked(d: ImageDraw.ImageDraw, xy, text: str, f, fill: str,
                  track: float) -> None:
    """Letter-spaced text, drawn glyph by glyph. `xy` is the top-left.

    PIL has no tracking, and small caps without it turn into a smear at 10 px.
    Tracking must be part of the fit calculation too — measuring with
    textlength and *then* adding spacing per glyph is how a label that "fits"
    runs off the edge of a 96 px key.

    **Each glyph is anchored on the baseline, not the top.** Drawing them one at
    a time with a "top" anchor makes PIL align every glyph's *ink* top to the
    same line, which silently lifts short glyphs: a period floats up to cap
    height and reads as a quote mark, an 'o' sits a pixel high. Whole-string
    draws never show this because PIL lays the run out on one baseline itself —
    it only appears once you split the string to add tracking.
    """
    x, y = xy
    baseline = y + f.getmetrics()[0]        # ascent
    for ch in text:
        d.text((x, baseline), ch, font=f, fill=fill, anchor="ls")
        x += d.textlength(ch, font=f) + track


def _fit(d: ImageDraw.ImageDraw, text: str, max_w: int, start: int,
         min_size: int, role: str = "ui", track: float = 0.0):
    """Shrink then truncate until `text` fits `max_w`."""
    size = start
    while size > min_size:
        f = font(size, role)
        if _tracked_w(d, text, f, track) <= max_w:
            return text, f
        size -= 1
    f = font(min_size, role)
    if _tracked_w(d, text, f, track) <= max_w:
        return text, f
    cut = text
    while cut and _tracked_w(d, cut + ELLIPSIS, f, track) > max_w:
        cut = cut[:-1]
    return (cut + ELLIPSIS) if cut else "", f


def _dim(color: Optional[str], pulse: float) -> Optional[str]:
    if color is None or pulse == 1.0:
        return color
    return scale(color, pulse)


def render(deck, slot: Slot) -> Image.Image:
    """Render a Slot to a PIL image sized for this deck's keys."""
    img = PILHelper.create_key_image(deck, background=_dim(slot.bg, slot.pulse))
    d = ImageDraw.Draw(img)
    w, h = img.width, img.height

    top = 0
    if slot.rule:
        d.rectangle([0, 0, w, slot.rule_h - 1], fill=slot.rule)
        top = slot.rule_h

    left = 0
    if slot.accent:
        d.rectangle([0, 0, slot.accent_w - 1, h], fill=slot.accent)
        left = slot.accent_w

    # Text clears the frame rather than tucking under it — at frame_w 4 a
    # label drawn at a fixed inset collides with its own border.
    inset = slot.frame_w if slot.frame else 0
    pad = left + inset + (8 if left else 7)
    avail = w - pad - 6 - inset
    if slot.badge:
        avail -= 15                    # reserve the top-right corner
    sub_fg = slot.sub_fg or "#7A828C"

    if slot.align == "center":
        cx = left + (w - left) / 2
        if slot.icon:
            # Icon over wrapped text. The icon says what kind of thing this is
            # at a glance; the text carries the detail that distinguishes it
            # from its neighbours, and gets two lines because at 96 px one line
            # of anything specific is a truncated stub.
            draw_icon(img, slot.icon, cx, top + 26, 30,
                      slot.icon_color or slot.fg)
            body = slot.label.upper() if slot.caps else slot.label
            track = 0.7 if slot.caps else 0.0
            lines, f = wrap(d, body, "caption" if slot.caps else "value",
                            avail, 2, 13, 9, track)
            y = 52
            for line in lines:
                lw = _tracked_w(d, line, f, track)
                _draw_tracked(d, (cx - lw / 2, y), line, f, slot.fg, track)
                y += f.size + 2
        else:
            # Value large and centred, caption beneath it: the action-bar shape.
            if slot.label:
                text, f = _fit(d, slot.label, avail, 26, 12, "value")
                d.text((int(cx), 40), text, anchor="mm", fill=slot.fg, font=f)
            if slot.sub:
                text = slot.sub.upper() if slot.caps else slot.sub
                cf = font(11, "caption")
                track = 0.9 if slot.caps else 0.0
                while text and _tracked_w(d, text, cf, track) > avail:
                    text = text[:-1]
                x = left + (w - left - _tracked_w(d, text, cf, track)) / 2
                _draw_tracked(d, (x, 58), text, cf, sub_fg, track)
    else:
        # Name on top, qualifier beneath: the session-tile shape. Anchored to
        # the top rather than centred so tiles line up with each other however
        # long their labels are — a ragged board is harder to scan.
        y = top + 7
        if slot.label and slot.sub:
            text, f = _fit(d, slot.label, avail, 26, 13, "display")
            d.text((pad, y), text, anchor="lt", fill=slot.fg, font=f)
            y += f.size + 3
            sub = slot.sub.upper() if slot.caps else slot.sub
            role = "caption"
            track = 0.7 if slot.caps else 0.0
            sf = font(12 if not slot.caps else 11, role)
            while sub and _tracked_w(d, sub + ELLIPSIS, sf, track) > avail:
                sub = sub[:-1]
                if sub and _tracked_w(d, sub + ELLIPSIS, sf, track) <= avail:
                    sub += ELLIPSIS
                    break
            _draw_tracked(d, (pad, y), sub, sf, sub_fg, track)
        elif slot.label:
            text, f = _fit(d, slot.label, avail, 26, 13, "display")
            d.text((pad, h // 2), text, anchor="lm", fill=slot.fg, font=f)

    if slot.badge:
        d.text((w - 6 - inset, top + inset + 2), slot.badge, anchor="ra",
               fill=slot.fg, font=font(21, "display"))

    if slot.bar is not None:
        # A band on the bottom edge, full width. Deliberately not inset: it is a
        # gauge, not a control, and running it edge to edge keeps it from
        # reading as a fifth line of content. It stacks *above* `foot` when both
        # are present, so the two never overdraw each other.
        frac = max(0.0, min(1.0, slot.bar))
        y1 = h - (slot.foot_h if slot.foot else 0)
        y0 = y1 - 6
        d.rectangle([0, y0, w, y1], fill=_dim(slot.bar_track, slot.pulse))
        if frac > 0:
            d.rectangle([0, y0, int(w * frac), y1],
                        fill=_dim(slot.bar_color, slot.pulse))
        # The meter rides with the field: it is drawn from the field's own ink,
        # so holding it steady while the field moved would make it drift in and
        # out of contrast.

    if slot.foot:
        # Structural, like `rule` and `frame` — never pulsed.
        d.rectangle([0, h - slot.foot_h, w, h], fill=slot.foot)

    if slot.frame:
        # Drawn last so it is never clipped by the field or the rule. A full
        # perimeter is the strongest structural signal available on a 96 px key
        # — worth reserving for one meaning per surface. Never pulsed.
        c = slot.frame
        for i in range(slot.frame_w):
            d.rectangle([i, i, w - 1 - i, h - 1 - i], outline=c)

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
                fg: str = "#FFFFFF", marks=(), pages=None) -> Image.Image:
    """Render the info bar. Neo supports full-panel writes only, no regions.

    `marks` is a sequence of (colour, text) chips drawn right of the headline —
    a generic "coloured tally" with no idea what it is tallying. `pages` is
    (current, total), drawn as dots, and is skipped when there is only one page.
    """
    img = PILHelper.create_screen_image(deck, background=bg)
    d = ImageDraw.Draw(img)
    w, h = img.width, img.height

    # Marks and dots are laid out first and the text is fitted into what is
    # left. Drawing the headline first and hoping it clears the chips is how the
    # two ended up overlapping — at 248 px there is no slack to gamble with.
    right = w - 11
    if marks:
        # Tight: at 248 px the tally is competing with the headline for the only
        # line that can hold a sentence, so every pixel it gives back is a
        # character the headline keeps.
        x = right
        cf = font(16, "num")
        for color, chip in reversed(list(marks)):
            x -= d.textlength(str(chip), font=cf)
            d.text((x, 12), str(chip), fill=color, font=cf, anchor="lt")
            x -= 5
            d.rectangle([x, 13, x + 3, 29], fill=color)
            x -= 10
        right = x

    sub_right = w - 12
    if pages and pages[1] > 1:
        cur, total = pages
        total = min(total, 8)
        dx = w - 12 - total * 11
        for i in range(total):
            c = fg if i == cur else "#33373D"
            d.ellipse([dx + i * 11, h - 13, dx + i * 11 + 6, h - 7], fill=c)
        sub_right = dx - 10

    if sub:
        # Headline plus caption: one line each.
        head, hf = _fit(d, text, max(24, right - 15), 27, 12, "display")
        d.text((11, 9), head, fill=fg, font=hf, anchor="lt")
        s, sf = _fit(d, sub.upper(), max(24, sub_right - 11), 12, 9,
                     "caption", track=0.8)
        _draw_tracked(d, (11, h - 21), s, sf, "#7A828C", 0.8)
    else:
        # No caption, so the headline may use both lines. A 248 px bar holds
        # about 25 characters on one line, which turns any real sentence into a
        # stub; two lines roughly doubles that and is the difference between
        # quoting a question and hinting at one. The second line runs full width
        # because only the first has to clear the tally.
        # Each line has a different obstacle: the tally sits beside line one,
        # the page dots beside line two. Clamping both to the narrower of the
        # two would waste width, so they are clamped separately.
        w1 = max(24, right - 15)
        w2 = max(24, sub_right - 13)
        one, f1 = _fit(d, text, w1, 27, 20, "display")
        if one == text:
            d.text((11, 9), one, fill=fg, font=f1, anchor="lt")
        else:
            lines, hf = wrap(d, text, "display", w2, 2, 22, 11)
            y = 8 if len(lines) > 1 else 14
            for i, line in enumerate(lines[:2]):
                d.text((11, y), ellipsize(d, line, hf, w1 if i == 0 else w2),
                       fill=fg, font=hf, anchor="lt")
                y += hf.size + 3
    return img
