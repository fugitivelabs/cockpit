"""Preview the deck headlessly, as PNGs — no hardware, no Elgato app.

Everything here is drawn by the REAL render path (`deck/render.py` plus the
actual cockpit components), so what you see is what the device gets. A fake deck
supplies only the geometry PILHelper asks for. There is no second copy of the
drawing code, deliberately: a mockup that drifts from the renderer is worse than
no mockup, because it lies with confidence.

This is the loop for tuning the visual language — palette hues, rule weights,
BREATHE_PERIOD_S / BREATHE_LO in dashboard.py — without stopping the daemon or
unplugging anything. The one thing it cannot tell you is what the breathe feels
like at the edge of your vision for an hour; only the hardware answers that.

    PYTHONPATH=src ./.venv/bin/python tests/preview.py [outdir]

Writes live.png (the three bar states) and pulse.png (frames of the breathe).
"""
import os
import sys

from PIL import Image, ImageDraw

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "src"))

from deck.render import Slot, font, render, render_info          # noqa: E402
from cockpit import palette                                       # noqa: E402
from cockpit.dashboard import SessionTile                         # noqa: E402
from fleet.sessions import Session, Telemetry                   # noqa: E402
from cockpit import actions                                       # noqa: E402

SCALE, GAP, PAD = 3, 14, 18


class FakeDeck:
    """Just enough deck for PILHelper: key size, screen size, layout."""

    def key_image_format(self):
        return {"size": (96, 96), "format": "JPEG", "rotation": 0,
                "flip": (True, True)}

    def screen_image_format(self):
        return {"size": (248, 58), "format": "JPEG", "rotation": 0,
                "flip": (True, True)}

    def key_layout(self):
        return (2, 4)


DECK = FakeDeck()


def sess(sid, cwd, task, state, ctx=None):
    return Session(id=sid, agent="claude", cwd=cwd, task=task, state=state,
                   handle=sid, telemetry=Telemetry(context_pct=ctx))


BOARD = [
    (sess("s1", "peregrine", "Bash outside project", "blocked", 41), False),
    (sess("s2", "provenance", "1804 Wake deed", "working", 62), False),
    (sess("s3", "docland", "index rebuild", "waiting", 18), False),
    (sess("s4", "cockpit", "visual language", "idle", 84), True),
]


def board_slots(pulse_at=None):
    out = []
    for s, focused in BOARD:
        t = SessionTile(s, s.cwd, s.task, lambda *_: None, focused=focused)
        slot = t.render()
        if pulse_at is not None and t.animating():
            slot = Slot(**{**slot.__dict__, "pulse": pulse_at})
        out.append(slot)
    return out


def info_slots():
    return [
        actions._furniture("Opus 4.8", "model", "m"),
        actions._furniture("84%", "context", "c", bar=0.84,
                           bar_color=palette.meter_color(0.84)),
        actions._furniture("$12.40", "cost", "$"),
        actions._furniture("Firefox", "app", "f"),
    ]


def answer_slots():
    """Built by the real `_answer_key`, never a lookalike.

    An earlier version of this file rebuilt the answer slot inline and promptly
    drifted: the board got restyled and these keys silently kept the old look,
    which is the exact failure a preview is supposed to catch rather than cause.
    `_answer_key` needs no live dashboard to render — only to fire.
    """
    return [actions._answer_key(None, 1, "Yes", None).render(),
            actions._answer_key(None, 2, "Yes, and don't ask again", None).render(),
            actions._answer_key(None, 3, "No", None).render(),
            actions._furniture("Firefox", "app", "f")]


def disabled_slots():
    from cockpit.dashboard import ActionKey
    return [ActionKey(s, None, enabled=lambda: False, name="x").render()
            for s in info_slots()]


def panel(slots, info_args, title, note):
    cw = 4 * 96 + 3 * GAP
    head, body_h = 62, 2 * 96 + GAP
    W, H = cw + PAD * 2, head + body_h + 16 + 58 + PAD
    img = Image.new("RGB", (W, H), "#17181A")
    d = ImageDraw.Draw(img)
    d.text((PAD, 14), title, font=font(21, "ui"), fill="#FFFFFF", anchor="lt")
    d.text((PAD, 41), note, font=font(13, "ui"), fill="#8A9099", anchor="lt")

    for i, s in enumerate(slots):
        r, c = divmod(i, 4)
        x, y = PAD + c * (96 + GAP), head + r * (96 + GAP)
        d.rounded_rectangle([x - 3, y - 3, x + 98, y + 98], radius=7, fill="#000")
        img.paste(render(DECK, s), (x, y))

    iy, ix = head + body_h + 16, PAD + (cw - 248) // 2
    d.rounded_rectangle([ix - 3, iy - 3, ix + 250, iy + 60], radius=5, fill="#000")
    img.paste(render_info(DECK, *info_args), (ix, iy))
    d.ellipse([ix - 34, iy + 20, ix - 16, iy + 38], fill="#20242A")
    d.ellipse([ix + 264, iy + 20, ix + 282, iy + 38], fill="#20242A")
    return img.resize((W * SCALE, H * SCALE), Image.NEAREST)


def stack(images, path, gap=26):
    W = max(i.width for i in images)
    H = sum(i.height for i in images) + gap * (len(images) - 1)
    out = Image.new("RGB", (W, H), "#0E0F10")
    y = 0
    for i in images:
        out.paste(i, ((W - i.width) // 2, y))
        y += i.height + gap
    out.save(path)
    print(path, out.size)


CHIPS = ((palette.WARNING, 1), (palette.CAUTION, 1), (palette.ADVISORY, 1),
         (palette.INERT, 1))
# The bar quotes the screen when something is being asked, and just counts
# otherwise. No banner in either case.
ASKING = ("Fetch https://example.com", "peregrine", "#000000", palette.INK,
          CHIPS, (0, 3))
CALM = ("4 sessions", "", "#000000", palette.INK,
        ((palette.ADVISORY, 2), (palette.INERT, 2)), (0, 3))

if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()
    stack([
        panel(board_slots() + info_slots(), ASKING, "LIVE — action bar",
              "info bar counts when calm; quotes the screen when something is asked"),
        panel(board_slots() + answer_slots(), ASKING, "LIVE — answer bar",
              "green=yes, amber=widens permission, neutral=no · full perimeter = this key types"),
        panel(board_slots() + disabled_slots(), CALM, "LIVE — nothing focused",
              "disabled keys keep their text and lose all colour"),
    ], os.path.join(out, "live.png"))

    stack([
        panel(board_slots(pulse_at=p) + info_slots(),
              ASKING, f"BREATHE — pulse {p:.2f}",
              "warm tiles only; cool tiles are identical across frames")
        for p in (0.62, 1.0, 1.8)
    ], os.path.join(out, "pulse.png"))
