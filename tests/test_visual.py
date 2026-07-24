"""The visual language: colour arithmetic, animation, and the render vocabulary.

Two kinds of assertion live here, and the second kind is the point.

The first is ordinary unit testing of `deck/color.py` and `deck/anim.py` — the
mechanism tier, which knows no meanings.

The second guards the **palette invariants**: one meaning per hue, deck-wide.
That rule is not expressible in a type and it is exactly what rotted last time —
`STYLE["working"]` and `ANSWER_YES` drifted into being the same literal, so red
meant "blocked", "No", and "renderer crashed" simultaneously. A test is the only
thing that will notice it happening again.

    PYTHONPATH=src python3 tests/test_visual.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "src"))

from deck import anim, color                                      # noqa: E402
from deck.render import ELLIPSIS, Slot, font, render, render_info  # noqa: E402
from cockpit import palette                                        # noqa: E402
from cockpit.actions import _answer_style                          # noqa: E402

passed = failed = 0


def check(what, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  PASS  {what}" + (f" — {detail}" if detail else ""))
    else:
        failed += 1
        print(f"  FAIL  {what}" + (f" — {detail}" if detail else ""))


class FakeDeck:
    """Enough deck for PILHelper — geometry only, no USB."""

    def key_image_format(self):
        return {"size": (96, 96), "format": "JPEG", "rotation": 0,
                "flip": (True, True)}

    def screen_image_format(self):
        return {"size": (248, 58), "format": "JPEG", "rotation": 0,
                "flip": (True, True)}

    def key_layout(self):
        return (2, 4)


DECK = FakeDeck()

print("\n[colour arithmetic]")
check("parse round-trips", color.to_hex(color.parse("#4A9EFF")) == "#4A9EFF")
check("shorthand expands", color.parse("#abc") == (0xAA, 0xBB, 0xCC))
check("lighten moves toward white", color.lighten("#000000", 0.5) == "#808080")
check("darken moves toward black", color.darken("#FFFFFF", 0.5) == "#808080")
check("scale preserves hue ratios",
      color.scale("#FF4A47", 0.5) == "#802524", color.scale("#FF4A47", 0.5))
check("scale clamps rather than wrapping",
      color.scale("#FF4A47", 4.0) == "#FFFFFF", color.scale("#FF4A47", 4.0))
check("mix at t=0 is a", color.mix("#FF0000", "#00FF00", 0.0) == "#FF0000")
check("mix at t=1 is b", color.mix("#FF0000", "#00FF00", 1.0) == "#00FF00")
check("over resolves a tint to an opaque colour",
      color.over("#FFFFFF", 0.5, "#000000") == "#808080")
check("readable_on picks dark text on a light field",
      color.readable_on("#FFFFFF") == "#000000")
check("readable_on picks light text on a dark field",
      color.readable_on("#0B0C0E") == "#FFFFFF")

# separation() / distinct() — the mechanism behind "a mark must not vanish into
# what it is drawn on". No meanings here: it is told what to avoid and does not
# ask why. The palette supplies the why; see context_color.
check("separation calls red on red a collision", color.separation("#FF4A47", "#F03A34") < color.COLLIDE)
check("…and red on blue not one, which luminance alone gets wrong",
      color.separation("#FF4A47", "#2A66C8") >= color.COLLIDE
      and abs(color.luminance("#FF4A47") - color.luminance("#2A66C8")) < 0.1)
check("distinct leaves a colour alone when nothing collides",
      color.distinct("#FF4A47", "#2A66C8") == "#FF4A47")
check("…and with nothing to avoid at all", color.distinct("#FF4A47") == "#FF4A47")
check("distinct lightens first — the dark side is usually the occupied one",
      color.distinct("#FF4A47", "#F03A34") == color.mix("#FF4A47", "#FFFFFF", 0.5))
check("…but goes dark when the light candidate is taken too",
      color.distinct("#FF4A47", "#F03A34", "#FFA4A3")
      == color.mix("#FF4A47", "#000000", 0.5))
check("…and keeps the hue either way: a nudged red is still red",
      all(color.parse(c)[0] > color.parse(c)[2]
          for c in (color.distinct("#FF4A47", "#F03A34"),
                    color.distinct("#FF4A47", "#F03A34", "#FFA4A3"))))
check("distinct returns the best it can when nothing clears — never raises",
      color.distinct("#808080", "#808080", "#C0C0C0", "#404040") is not None)

# render() must never raise — Surface's fault isolation turns a raise into an
# ERR tile, so a malformed colour would silently eat a session's key.
check("a malformed colour passes through instead of raising",
      color.lighten("nope") == "nope" and color.scale(None, 2) is None)
check("mix survives a malformed colour", color.mix("nope", "#FFFFFF") == "nope")

print("\n[animation — quantization is what keeps the cache working]")
check("quantize snaps to buckets", anim.quantize(0.5, 3) == 0.5)
check("quantize is idempotent",
      anim.quantize(anim.quantize(0.37, 12), 12) == anim.quantize(0.37, 12))

clock = [0.0]
vals = set()
for i in range(400):
    clock[0] = i * 0.01
    vals.add(anim.breathe(2.4, 0.6, 1.0, clock=lambda: clock[0]))
check("a breathe over 4 s yields a small bounded set of values",
      2 < len(vals) <= anim.STEPS, f"{len(vals)} distinct")
check("…all within [lo, hi]", all(0.6 - 1e-9 <= v <= 1.0 + 1e-9 for v in vals))
check("…reaching both ends of its range",
      min(vals) < 0.65 and max(vals) > 0.95, f"{min(vals):.2f}..{max(vals):.2f}")

check("flash with no timestamp rests at 1.0", anim.flash(None) == 1.0)
clock[0] = 100.0
check("flash peaks at the moment of the event",
      anim.flash(100.0, 1.4, 1.9, clock=lambda: clock[0]) > 1.6)
clock[0] = 100.7
mid = anim.flash(100.0, 1.4, 1.9, clock=lambda: clock[0])
check("…decays through the middle", 1.0 < mid < 1.6, f"{mid:.2f}")
clock[0] = 102.0
check("…and is over once duration elapses",
      anim.flash(100.0, 1.4, 1.9, clock=lambda: clock[0]) == 1.0)
check("flash ignores a timestamp in the future",
      anim.flash(200.0, 1.4, clock=lambda: clock[0]) == 1.0)

print("\n[palette] one meaning per hue, deck-wide")
HUES = {
    "WARNING": palette.WARNING, "CAUTION": palette.CAUTION, "GO": palette.GO,
    "ADVISORY": palette.ADVISORY, "INERT": palette.INERT,
}
check("the five meanings are five distinct colours",
      len(set(HUES.values())) == 5, str(HUES))

state_hues = {st.color for st in palette.STATE.values()}
answer_hues = {palette.ANSWER_AFFIRM, palette.ANSWER_GRANT,
               palette.ANSWER_DECLINE}
check("green never appears on the session board", palette.GO not in state_hues)
# Amended 2026-07-22. The rule was "red never on the answer bar", written when
# both rows flooded their fields — two red FIELDS on screen would have been
# genuinely ambiguous. The answer bar is now a quiet field with the hue in the
# icon, so the invariant that matters is about form, not hue: the board owns
# flooded colour, the answer bar owns glyphs, and no answer key floods.
check("no answer key floods its field",
      palette.ANSWER_BG == palette.FURNITURE)
check("…so the board keeps sole ownership of a coloured FIELD",
      all(st.field != palette.ANSWER_BG or n == "idle"
          for n, st in palette.STATE.items()))
check("decline is red again — over-learned, and now unmistakable as a glyph",
      palette.ANSWER_DECLINE == palette.WARNING)
check("affirm keeps green — the one mapping worth not inventing",
      palette.ANSWER_AFFIRM == palette.GO)
check("a permission-widening yes is caution, not go",
      palette.ANSWER_GRANT == palette.CAUTION)

# The temperature read: warm = act, cool = ignore. This is the property that
# survives peripheral vision, so it is worth pinning.
warm = {n for n, st in palette.STATE.items() if st.needs_you}
check("exactly blocked and waiting are warm", warm == {"blocked", "waiting"})
# Urgency is carried by how loud the flooded field is, not by chrome
# thickness — thin chrome does not survive the trip to a physical key.
# Temperature, measured directly. Luminance is a bad proxy for "loud" — red is
# inherently low-luminance, so a vivid red scores below a mid blue and the
# obvious assertion fails for the wrong reason.
def _temp(hex_):
    r, g, b = color.parse(hex_)
    return r - b


check("warm states are warm: red dominates blue in the field",
      all(_temp(palette.STATE[n].field) > 0 for n in warm),
      str({n: _temp(palette.STATE[n].field) for n in warm}))
check("cool states are cool: blue dominates red",
      all(_temp(st.field) < 0 for n, st in palette.STATE.items()
          if not st.needs_you),
      str({n: _temp(st.field) for n, st in palette.STATE.items()
           if not st.needs_you}))
_lum = {n: color.luminance(st.field) for n, st in palette.STATE.items()}
check("every state field is distinct",
      len({st.field for st in palette.STATE.values()}) == len(palette.STATE))
check("idle recedes furthest", min(_lum, key=_lum.get) == "idle")
check("each state's ink is legible on its own field",
      all(abs(color.luminance(st.ink) - color.luminance(st.field)) > 0.3
          for st in palette.STATE.values()),
      str({n: round(abs(color.luminance(st.ink) - _lum[n]), 2)
           for n, st in palette.STATE.items()}))
check("…and so is its caption — this is what 'washed out' meant",
      all(abs(color.luminance(st.ink_dim) - color.luminance(st.field)) > 0.15
          for st in palette.STATE.values()),
      str({n: round(abs(color.luminance(st.ink_dim) - _lum[n]), 2)
           for n, st in palette.STATE.items()}))
check("only needs-you states carry a badge",
      {n for n, st in palette.STATE.items() if st.badge} == warm)

# A quiet deck must look like ONE quiet surface. idle tiles and action-bar keys
# drifted to #2C323B against #0E0F12 — over three times the luminance — so a
# board with nothing happening read as two different greys stacked on each
# other. Board and controls are already separated by structure; brightness
# saying it again, badly, is just inconsistency.
check("an idle tile and an action key share the same field",
      palette.STATE["idle"].field == palette.FURNITURE,
      f'{palette.STATE["idle"].field} vs {palette.FURNITURE}')
_idle_px = render(DECK, Slot(label="docland", sub="idle", caps=True,
                             bg=palette.STATE["idle"].field))
_act_px = render(DECK, Slot(label="Firefox", sub="app", caps=True,
                            align="center", bg=palette.FURNITURE))
check("…which is identical in the actual pixels, not just the constant",
      _idle_px.getpixel((90, 50)) == _act_px.getpixel((90, 50)),
      f"{_idle_px.getpixel((90, 50))} vs {_act_px.getpixel((90, 50))}")
check("…and it is still darker than every state that wants something",
      all(color.luminance(palette.FURNITURE) < color.luminance(palette.STATE[n].field)
          for n in warm))
check("motion is opt-in per state, not implied by needs_you",
      not palette.STATE["blocked"].breathes
      and not palette.STATE["blocked"].flashes)
check("…and no cool state ever moves",
      not any(st.breathes or st.flashes for n, st in palette.STATE.items()
              if not st.needs_you))
# As of 2026-07-24 that is every state: the board is entirely still, and a
# flooded field is doing all the work the pulse used to.
check("…nor any warm one — nothing on the board moves",
      not any(st.breathes or st.flashes for st in palette.STATE.values()))
check("…but the capability is still wired, one word from coming back",
      all(hasattr(st, "breathes") and hasattr(st, "flashes")
          for st in palette.STATE.values()))
check("every state spells itself out",
      all(st.word for st in palette.STATE.values()))
check("the error tile shares no hue with the palette",
      "#FF6BD6" not in set(HUES.values()) | state_hues | answer_hues)

print("\n[answer styling] colour follows what the option says")
check("a plain Yes is go", _answer_style("Yes") == palette.GO)
check("a No is the neutral", _answer_style("No") == palette.ANSWER_DECLINE)
check("'Yes, and don't ask again' is caution",
      _answer_style("Yes, and don't ask again") == palette.CAUTION)
check("'Yes, allow edits' is caution, not go",
      _answer_style("Yes, allow edits to settings") == palette.CAUTION)
check("an unrecognised option defaults to caution, never go",
      _answer_style("Left") == palette.CAUTION)

print("\n[render vocabulary] every primitive draws without raising")
cases = {
    "bare label": Slot(label="peregrine"),
    "label + caps sub": Slot(label="peregrine", sub="blocked", caps=True),
    "top rule": Slot(label="x", rule=palette.WARNING, rule_h=9),
    "perimeter frame": Slot(label="Yes", frame=palette.GO, frame_w=3),
    "legacy accent": Slot(label="x", accent="#4A6B7C"),
    "centred value": Slot(label="84%", sub="context", caps=True, align="center"),
    "hairline meter": Slot(label="x", bar=0.84, bar_color=palette.CAUTION),
    "badge": Slot(label="x", badge="!"),
    "pulsed": Slot(label="x", bg=palette.FIELD, rule=palette.WARNING, pulse=0.6),
    "everything at once": Slot(label="peregrine", sub="blocked", caps=True,
                               rule=palette.WARNING, rule_h=9,
                               frame=palette.FOCUS, bar=0.4, badge="!",
                               pulse=0.8),
    "overlong label": Slot(label="a" * 200, sub="b" * 200, caps=True),
    "empty": Slot(),
}
for name, slot in cases.items():
    try:
        img = render(DECK, slot)
        check(f"renders: {name}", img.size == (96, 96))
    except Exception as e:
        check(f"renders: {name}", False, f"{type(e).__name__}: {e}")

# Icons are supersampled because PIL does not antialias lines: drawn directly, a
# checkmark's shallow leg stair-steps while a cross's exact 45-degree diagonals
# land on whole pixels, so the two read as coming from different sets.
for _icon in ("check", "check-double", "cross"):
    _px = render(DECK, Slot(icon=_icon, bg="#000000", icon_color="#FFFFFF",
                            align="center"))
    _levels = set(_px.crop((20, 8, 76, 50)).convert("L").tobytes())
    _mid = [v for v in _levels if 40 < v < 215]
    check(f"icon '{_icon}' is antialiased, not stair-stepped",
          len(_mid) > 8, f"{len(_mid)} intermediate levels")

# Centring is measured on the rendered INK, because that is what the eye
# centres on: a checkmark's mass sits low and right of the box it is drawn in,
# so a nominally-centred glyph still looks off. And the icon must sit at the
# same height on every key, or a bar of one- and two-line labels reads ragged.
def _ink_centre(slot):
    px = render(DECK, slot).convert("L").load()
    pts = [(x, y) for x in range(96) for y in range(96) if px[x, y] > 120]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2


for _icon in ("check", "check-double", "cross", "plus", "dot"):
    _cx, _ = _ink_centre(Slot(icon=_icon, bg="#000000", icon_color="#FFFFFF",
                              align="center"))
    check(f"icon '{_icon}' is centred on its ink, not its path box",
          abs(_cx - 48) <= 1.0, f"centre x={_cx}")

_short = Slot(icon="check", label="YES", caps=True, align="center",
              bg="#000000", icon_color="#FFFFFF", fg="#000001")
_long = Slot(icon="check", label="DO NOT ASK AGAIN FOR EXAMPLE", caps=True,
             align="center", bg="#000000", icon_color="#FFFFFF", fg="#000001")
check("a one-line and a two-line key put their icon at the same height",
      _ink_centre(_short)[1] == _ink_centre(_long)[1],
      f"{_ink_centre(_short)[1]} vs {_ink_centre(_long)[1]}")

check("a long label is truncated with an ASCII marker, not U+2026",
      "…" not in ELLIPSIS and ELLIPSIS == "...")

# Regression: tracked text is drawn one glyph at a time, and anchoring each on
# "top" makes PIL align every glyph's INK top to the same line — which lifts a
# period to cap height (it reads as a quote mark) and floats lowercase a pixel
# high. Whole-string draws never show this, so only a per-glyph test catches it.
from PIL import Image, ImageDraw                                   # noqa: E402
from deck.render import _draw_tracked                              # noqa: E402

_f = font(11, "caption")
_probe = Image.new("L", (60, 30), 0)
_draw_tracked(ImageDraw.Draw(_probe), (2, 2), "A.", _f, 255, 1.0)
_a = Image.new("L", (60, 30), 0)
_draw_tracked(ImageDraw.Draw(_a), (2, 2), "A", _f, 255, 1.0)
_dot = Image.new("L", (60, 30), 0)
_draw_tracked(ImageDraw.Draw(_dot), (2, 2), ".", _f, 255, 1.0)
_abox, _dbox = _a.getbbox(), _dot.getbbox()
check("tracked glyphs share a baseline, not an ink top",
      _dbox[3] == _abox[3], f"'.' bottom {_dbox[3]} vs 'A' bottom {_abox[3]}")
check("…so a period sits low, not at cap height",
      _dbox[1] > _abox[1] + (_abox[3] - _abox[1]) / 2,
      f"'.' top {_dbox[1]}, 'A' spans {_abox[1]}..{_abox[3]}")
check("tracking actually advances the pen",
      _probe.getbbox()[2] > _abox[2])

print("\n[render is a pure function of the Slot] the cache depends on it")
# A non-black field on purpose: pulse scales the field, and scaling black by
# anything is still black, so a black-backed slot cannot demonstrate the effect.
a = Slot(label="peregrine", sub="blocked", caps=True, bg=palette.STATE["blocked"].field,
         rule=palette.WARNING, rule_h=9, pulse=0.7)
check("identical slots produce identical bytes",
      render(DECK, a).tobytes() == render(DECK, a).tobytes())
b = Slot(**{**a.__dict__, "pulse": 1.0})
check("a different pulse produces different bytes",
      render(DECK, a).tobytes() != render(DECK, b).tobytes())
check("…and compares unequal, so the diff repaints it", a != b)

# Pulse moves the field and nothing else. A focus frame that dimmed along with
# the state underneath it gave the tile you are looking at the weakest possible
# "you are here" — exactly backwards.
_lo = Slot(label="x", bg="#E03530", frame="#FFFFFF", frame_w=4,
           rule="#FFFFFF", rule_h=14, pulse=0.5)
_hi = Slot(**{**_lo.__dict__, "pulse": 1.0})
_lo_px, _hi_px = render(DECK, _lo), render(DECK, _hi)
check("a pulsed tile still draws its frame at full white",
      _lo_px.getpixel((1, 60)) == _hi_px.getpixel((1, 60)) == (255, 255, 255),
      f"{_lo_px.getpixel((1, 60))} vs {_hi_px.getpixel((1, 60))}")
check("…and its cap at full white",
      _lo_px.getpixel((48, 6)) == _hi_px.getpixel((48, 6)) == (255, 255, 255))
_foot_lo = render(DECK, Slot(label="x", bg="#E03530", foot="#FFFFFF",
                             foot_h=15, pulse=0.5))
check("…and a foot at full white too",
      _foot_lo.getpixel((48, 92)) == (255, 255, 255),
      str(_foot_lo.getpixel((48, 92))))
check("…while the field itself does dim",
      _lo_px.getpixel((48, 60)) != _hi_px.getpixel((48, 60)))
check("Slot stays hashable — Surface caches on it", isinstance(hash(a), int))
check("pulse does not dim the text, only the chrome",
      render(DECK, Slot(label="x", fg="#FFFFFF", pulse=0.2)).getextrema()[0][1]
      == 255)

# Regression: focus used to be a top cap, which pushed the label down by its
# own height — so the project name sat at one of two heights depending on which
# tile you were in, and the row read as disjointed. Whatever focus is, adding it
# must not move a single pixel of type.
from cockpit.dashboard import SessionTile                          # noqa: E402
from fleet.sessions import Session, Telemetry                    # noqa: E402

_s = Session(id="s1", agent="claude", cwd="peregrine", task="index rebuild",
             state="idle", handle="1", telemetry=Telemetry(context_pct=62))
_on = render(DECK, SessionTile(_s, "peregrine", "index rebuild",
                               lambda *_: None, focused=True).render())
_off = render(DECK, SessionTile(_s, "peregrine", "index rebuild",
                                lambda *_: None, focused=False).render())
check("focus changes the tile at all", _on.tobytes() != _off.tobytes())
# The crop stops at the top of the context meter, which is as far up the tile as
# focus mass now reaches. Everything above it — both lines of type AND the meter
# — has to be untouched: the unfocused tile reserves the band's space precisely
# so the gauge doesn't jump 28 px every time you move between sessions.
check("…but moves NO type and no meter: everything above the band is identical",
      _on.crop((0, 0, 96, 62)).tobytes() == _off.crop((0, 0, 96, 62)).tobytes())
check("…and the change is all at the bottom edge",
      _on.crop((0, 80, 96, 96)).tobytes() != _off.crop((0, 80, 96, 96)).tobytes())

check("a foot stacks above the meter rather than overdrawing it",
      render(DECK, Slot(label="x", bg="#245BAE", bar=1.0, bar_color="#FFFFFF",
                        foot="#FF0000", foot_h=10)).getpixel((48, 90))
      == (255, 0, 0))
check("…and an empty foot reserves that space without drawing anything",
      render(DECK, Slot(label="x", bg="#245BAE", bar=1.0, bar_color="#FFFFFF",
                        foot="", foot_h=10)).getpixel((48, 90))
      == render(DECK, Slot(label="x", bg="#245BAE")).getpixel((48, 90)))

# Reverse-rounded shoulders: the band flares UP the edges, so at the extreme
# left there is white ABOVE the band's nominal top, and at mid-key there is not.
_sh = render(DECK, Slot(label="x", bg="#245BAE", foot="#FFFFFF", foot_h=20,
                        foot_r=8))
_lit = lambda x, y: _sh.getpixel((x, y))[0] > 200          # band top is y=76
check("the foot's corners round the WRONG way — white ABOVE the band at the edges",
      _lit(0, 73) and _lit(95, 73),
      f"{_sh.getpixel((0, 73))} / {_sh.getpixel((95, 73))}")
check("…and nothing above it mid-key, so the flare is a corner and not a rule",
      not _lit(48, 73) and not _lit(48, 69))
check("…concave: the shoulder widens as it approaches the band",
      sum(_lit(x, 75) for x in range(20)) > sum(_lit(x, 72) for x in range(20)))
check("…and both shoulders are identical, not two independently drawn arcs",
      _sh.crop((0, 68, 8, 76)).transpose(Image.FLIP_LEFT_RIGHT).tobytes()
      == _sh.crop((88, 68, 96, 76)).tobytes())

print("\n[meters] the context ramp: quiet, then caution, then warning")
check("below 50% the gauge is furniture, in the caller's own quiet colour",
      palette.context_color(0) == palette.METER
      and palette.context_color(49.9) == palette.METER)
check("50-75% is caution", palette.context_color(50) == palette.CAUTION
      and palette.context_color(74.9) == palette.CAUTION)
check("75% and up is warning", palette.context_color(75) == palette.WARNING
      and palette.context_color(100) == palette.WARNING)
# The board floods a tile with its state colour, so the ramp has to survive
# being drawn on its own hue — a red meter on a red tile is no meter at all. It
# also has to survive being drawn beside the SAME gauge below the threshold: a
# meter that goes to caution and doesn't visibly change has not warned anyone.
_dist = lambda a, b: sum((p - q) ** 2 for p, q
                         in zip(color.parse(a), color.parse(b))) ** 0.5
for _state in ("blocked", "waiting", "working", "idle"):
    _st = palette.STATE[_state]
    _quiet = color.over(_st.ink, 0.55, _st.field)      # what the tile draws < 50%
    for _pct in (60.0, 90.0):
        _c = palette.context_color(_pct, base=_quiet, field=_st.field)
        check(f"the {_pct:.0f}% meter is visible on a {_state} tile",
              _dist(_c, _st.field) >= 90,
              f"{_c} on {_st.field}, distance {_dist(_c, _st.field):.0f}")
        check(f"…and differs from the same tile's sub-50% gauge",
              _dist(_c, _quiet) >= 90,
              f"{_c} vs {_quiet}, distance {_dist(_c, _quiet):.0f}")

print("\n[info bar] the tally must not collide with the headline")
for name, args in {
    "plain": ("4 sessions", ""),
    "headline + sub": ("2 NEEDS YOU", "peregrine — Bash outside project"),
    "long headline": ("what an extremely long headline this is", "sub"),
}.items():
    try:
        img = render_info(DECK, *args, "#000000", "#FFFFFF",
                          ((palette.WARNING, 2), (palette.ADVISORY, 1)), (0, 3))
        check(f"info bar renders: {name}", img.size == (248, 58))
    except Exception as e:
        check(f"info bar renders: {name}", False, f"{type(e).__name__}: {e}")
check("a single page draws no dots",
      render_info(DECK, "x", pages=(0, 1)).tobytes()
      == render_info(DECK, "x").tobytes())

print("\n[fonts] the bug that left the deck with no bold")
check("display resolves to a real truetype face, not the bitmap fallback",
      hasattr(font(20, "display"), "path"), str(type(font(20, "display"))))
check("display and caption are different faces",
      font(20, "display").path != font(20, "caption").path
      or font(20, "display").index != font(20, "caption").index)
check("an unknown role falls back rather than raising",
      font(14, "nonsense") is not None)

print(f"\n=== {passed} passed, {failed} failed ===")
sys.exit(1 if failed else 0)
