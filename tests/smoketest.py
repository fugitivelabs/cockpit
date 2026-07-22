#!/usr/bin/env python3
"""
Stream Deck Neo — direct HID smoke test.

Verifies, in order of how badly each would kill the project:
  1. we can claim the device at all (no TCC prompt, no permission wall)
  2. the library recognises it as a Neo with the right geometry
  3. we can render to the 8 keys
  4. we can set the 2 touch point colours
  5. we can write the info bar  (newest / least-proven code path)
  6. we can read input from keys AND touch points

Requires the Elgato app to be FULLY QUIT — HID access is exclusive.
"""

import sys
import time
import traceback

RESULTS = []


def check(name, fn):
    """Run one step, record pass/fail, never abort the whole run."""
    try:
        detail = fn()
        RESULTS.append((True, name, detail or ""))
        print(f"  PASS  {name}" + (f" — {detail}" if detail else ""))
        return True
    except Exception as e:
        RESULTS.append((False, name, f"{type(e).__name__}: {e}"))
        print(f"  FAIL  {name} — {type(e).__name__}: {e}")
        if "-v" in sys.argv:
            traceback.print_exc()
        return False


def main():
    print("\n=== Stream Deck Neo smoke test ===\n")

    # --- 1. imports -------------------------------------------------------
    print("[imports]")
    try:
        from StreamDeck.DeviceManager import DeviceManager
        from PIL import Image, ImageDraw, ImageFont
        from StreamDeck.ImageHelpers import PILHelper
        print("  PASS  imports (StreamDeck, PIL)")
    except Exception as e:
        print(f"  FAIL  imports — {e}")
        print("\n  Fix: pip install streamdeck pillow  (and brew install hidapi)")
        return 1

    # --- 2. enumerate -----------------------------------------------------
    print("\n[enumerate]")
    try:
        decks = DeviceManager().enumerate()
    except Exception as e:
        print(f"  FAIL  enumerate — {type(e).__name__}: {e}")
        print("\n  If this is a hidapi/library-load error, check: brew install hidapi")
        return 1

    print(f"  found {len(decks)} device(s)")
    if not decks:
        print("\n  No decks found. Checklist:")
        print("    - Is the Neo plugged in?")
        print("    - Is the Elgato app FULLY quit? (menu bar > Quit, not just close window)")
        print("    - Try a different USB port / cable.")
        return 1

    deck = decks[0]

    # --- 3. open + identify ----------------------------------------------
    print("\n[open + identify]")
    if not check("open device", lambda: deck.open() or "claimed"):
        print("\n  Could not claim the device. Almost certainly the Elgato app still")
        print("  holds it, or macOS denied HID access. Quit the app fully and retry.")
        return 1

    try:
        check("deck type", lambda: deck.deck_type())
        check("serial number", lambda: deck.get_serial_number())
        check("firmware version", lambda: deck.get_firmware_version())
        check("key count", lambda: f"{deck.key_count()} keys, layout {deck.key_layout()}")
        check("key image format", lambda: str(deck.key_image_format()))

        is_neo = "neo" in str(deck.deck_type()).lower()
        print(f"\n  Neo class in use: {is_neo}")
        if not is_neo:
            print("  WARNING: library did not identify this as a Neo — geometry may be wrong.")

        # touch points / info bar are Neo-specific; probe rather than assume
        check("touch key count",
              lambda: str(getattr(deck, "touch_key_count", lambda: "n/a")()))
        check("screen (info bar) format",
              lambda: str(getattr(deck, "screen_image_format", lambda: "n/a")()))

        # --- 4. brightness + key render ----------------------------------
        print("\n[render]")
        check("set brightness", lambda: deck.set_brightness(60) or "60%")

        def draw_keys():
            font = ImageFont.load_default()
            for i in range(deck.key_count()):
                img = PILHelper.create_key_image(deck)
                d = ImageDraw.Draw(img)
                d.rectangle([0, 0, img.width - 1, img.height - 1], outline="white")
                d.text((img.width // 2, img.height // 2), str(i),
                       anchor="mm", fill="white", font=font)
                deck.set_key_image(i, PILHelper.to_native_key_format(deck, img))
            return f"drew 0..{deck.key_count() - 1}"

        check("draw all keys", draw_keys)

        # --- 5. touch points ----------------------------------------------
        def touch_colors():
            # On the Neo the touch points are key indices 8 and 9, RGB only.
            deck.set_key_color(8, 255, 0, 0)
            deck.set_key_color(9, 0, 0, 255)
            return "8=red, 9=blue"

        check("touch point colours", touch_colors)

        # --- 6. info bar (least-proven path) ------------------------------
        def info_bar():
            img = PILHelper.create_screen_image(deck)
            d = ImageDraw.Draw(img)
            d.text((img.width // 2, img.height // 2), "SMOKE TEST",
                   anchor="mm", fill="white")
            deck.set_screen_image(PILHelper.to_native_screen_format(deck, img))
            return f"{img.width}x{img.height}"

        check("info bar write", info_bar)

        # --- 7. input ------------------------------------------------------
        print("\n[input]  press some keys and BOTH touch points — 15s window")
        seen = set()

        def on_press(_deck, key, state):
            if state:
                seen.add(key)
                label = f"touch point {key}" if key >= 8 else f"key {key}"
                print(f"    got {label}")

        deck.set_key_callback(on_press)
        deadline = time.time() + 15
        while time.time() < deadline:
            time.sleep(0.2)

        keys_hit = sorted(k for k in seen if k < 8)
        touch_hit = sorted(k for k in seen if k >= 8)
        print(f"\n  keys pressed: {keys_hit or 'NONE'}")
        print(f"  touch points pressed: {touch_hit or 'NONE'}")
        RESULTS.append((bool(keys_hit), "key input", str(keys_hit)))
        RESULTS.append((bool(touch_hit), "touch point input", str(touch_hit)))

    finally:
        try:
            deck.reset()
            deck.close()
            print("\n  device reset + released")
        except Exception as e:
            print(f"\n  cleanup warning: {e}")

    # --- summary ----------------------------------------------------------
    print("\n=== summary ===")
    for ok, name, detail in RESULTS:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f" — {detail}" if detail else ""))
    failed = [n for ok, n, _ in RESULTS if not ok]
    print(f"\n  {len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
    if failed:
        print(f"  failed: {', '.join(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
