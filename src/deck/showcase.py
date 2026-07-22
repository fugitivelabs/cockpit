"""Capability showcase — what the panel can actually do.

Not the use case; just the hardware and the library being put through their
paces. Every scene pre-encodes its frames before playing, because encoding is
~2.5x the cost of pushing and would otherwise set the frame ceiling.

    PYTHONPATH=.. python3 -m deck.showcase
"""

import colorsys
import math
import sys

from PIL import Image, ImageDraw

from deck import Slot, Surface, TOUCH_LEFT, TOUCH_RIGHT
from deck.render import font, render_info, tile

W, H = 384, 192          # the 4x2 keypad treated as one canvas
GAP = 26                 # approximate bezel, so lines stay continuous


def canvas(color=(0, 0, 0)):
    return Image.new("RGB", (W, H), color)


def hsv(h, s=1.0, v=1.0):
    r, g, b = colorsys.hsv_to_rgb(h % 1.0, s, v)
    return int(r * 255), int(g * 255), int(b * 255)


# -- scenes ---------------------------------------------------------------

def scene_wave(deck, n=48):
    """A colour wave rolling diagonally across the whole panel."""
    frames = []
    for f in range(n):
        img = canvas()
        d = ImageDraw.Draw(img)
        phase = f / n * 2 * math.pi
        for x in range(0, W, 8):
            for y in range(0, H, 8):
                v = math.sin(x / 70 + y / 50 - phase)
                d.rectangle([x, y, x + 8, y + 8],
                            fill=hsv(0.55 + v * 0.12, 0.85, 0.25 + 0.75 * (v + 1) / 2))
        frames.append(tile(deck, img, GAP))
    return frames


def scene_sweep(deck, n=40):
    """A bright bar sweeping left to right, leaving a fading trail."""
    frames = []
    for f in range(n):
        img = canvas((6, 6, 10))
        d = ImageDraw.Draw(img)
        head = (f / n) * (W + 120) - 60
        for t in range(70, -1, -3):
            x = head - t
            if -20 < x < W + 20:
                fade = 1.0 - (t / 70.0)
                c = hsv(0.5 + t / 500, 0.7, fade)
                d.rectangle([x - 5, 0, x + 5, H], fill=c)
        frames.append(tile(deck, img, GAP))
    return frames


def scene_ripple(deck, n=44):
    """Concentric rings expanding from the centre of the panel."""
    frames = []
    cx, cy = W / 2, H / 2
    for f in range(n):
        img = canvas()
        d = ImageDraw.Draw(img)
        for ring in range(5):
            r = ((f / n) * 150 + ring * 42) % 210
            if r < 4:
                continue
            fade = max(0.0, 1.0 - r / 210)
            d.ellipse([cx - r, cy - r, cx + r, cy + r],
                      outline=hsv(0.45 + r / 600, 0.8, fade), width=5)
        frames.append(tile(deck, img, GAP))
    return frames


def scene_marquee(deck, text="  STREAM DECK NEO  ·  8 KEYS  ·  2 TOUCH PADS  ·  248x58 INFO BAR  "):
    """Text scrolling across the info bar, keys pulsing underneath."""
    fmt = deck.screen_image_format()["size"]
    f = font(34)
    probe = Image.new("RGB", (10, 10))
    text_w = int(ImageDraw.Draw(probe).textlength(text, font=f))

    frames = []
    steps = max(1, text_w // 4)
    for i in range(steps):
        bar = Image.new("RGB", fmt, (0, 0, 0))
        d = ImageDraw.Draw(bar)
        x = -i * 4
        d.text((x, fmt[1] // 2), text, anchor="lm", fill=hsv(i / steps), font=f)
        d.text((x + text_w, fmt[1] // 2), text, anchor="lm", fill=hsv(i / steps), font=f)

        img = canvas()
        dd = ImageDraw.Draw(img)
        pulse = (math.sin(i / 6) + 1) / 2
        for k in range(8):
            r, c = divmod(k, 4)
            x0, y0 = c * (96 + GAP), r * (96 + GAP)
            local = (math.sin(i / 6 - k * 0.5) + 1) / 2
            dd.rectangle([x0, y0, x0 + 96, y0 + 96], fill=hsv(0.55, 0.9, 0.15 + local * 0.5))
        frame = tile(deck, img, GAP)
        frame["info"] = bar
        frames.append(frame)
    return frames


def scene_boot(deck, n=30):
    """Keys illuminating in sequence, like a panel powering up."""
    frames = []
    order = [0, 1, 2, 3, 7, 6, 5, 4]
    for f in range(n):
        img = canvas()
        d = ImageDraw.Draw(img)
        lit = (f / n) * 10
        for pos, k in enumerate(order):
            r, c = divmod(k, 4)
            x0, y0 = c * (96 + GAP), r * (96 + GAP)
            level = max(0.0, min(1.0, lit - pos))
            if level > 0:
                d.rectangle([x0, y0, x0 + 96, y0 + 96], fill=hsv(0.33, 0.8, level * 0.9))
        frames.append(tile(deck, img, GAP))
    return frames


SCENES = [
    ("boot",    scene_boot,    30, 1),
    ("wave",    scene_wave,    30, 3),
    ("sweep",   scene_sweep,   45, 3),
    ("ripple",  scene_ripple,  30, 3),
    ("marquee", scene_marquee, 30, 1),
]


def main():
    with Surface(brightness=80) as s:
        deck = s._deck
        s.set_touch(TOUCH_LEFT, (0, 90, 140))
        s.set_touch(TOUCH_RIGHT, (140, 40, 0))

        for name, builder, fps, loops in SCENES:
            frames = builder(deck)
            prepared = s.prepare(frames)
            got = s.play(prepared, fps=fps, loops=loops)
            print(f"  {name:8s} {len(frames):3d} frames  target {fps:2d} fps  "
                  f"achieved {got:5.1f} fps")

        # land on something static so the deck isn't left mid-animation
        s.show({i: Slot(label=t, bg=b, accent=a) for i, (t, b, a) in enumerate([
            ("deck", "#12263A", "#3FA7D6"), ("lib", "#12263A", "#3FA7D6"),
            ("works", "#12263A", "#3FA7D6"), ("", "#000000", None),
            ("", "#000000", None), ("", "#000000", None),
            ("", "#000000", None), ("", "#000000", None),
        ])})
        s.set_info("showcase complete")
        print("\n  done — deck left on a static frame")


if __name__ == "__main__":
    sys.exit(main() or 0)
