"""Exercise the deck library against real Terminal sessions.

Read-only: enumerates windows, renders them, and focuses one when you press a
key. Nothing is typed into any session. This is the legibility test as much as
the library test — the question it answers is whether a real session name is
readable at 96x96.

    PYTHONPATH=.. python3 -m deck.demo
"""

import re
import subprocess
import sys

from deck import BLANK, Slot, Surface, TOUCH_LEFT, TOUCH_RIGHT

# Braille spinner frames — Claude Code cycles these while working.
SPINNER = set("⠁⠂⠃⠄⠅⠆⠇⠈⠉⠊⠋⠌⠍⠎⠏⠐⠑⠒⠓⠔⠕⠖⠗⠘⠙⠚⠛⠜⠝⠞⠟⠠⠡⠢⠣⠤⠥⠦⠧⠨⠩⠪⠫⠬⠭⠮⠯"
              "⠰⠱⠲⠳⠴⠵⠶⠷⠸⠹⠺⠻⠼⠽⠾⠿⡀⡄⡆⡇⣀⣄⣆⣇⣠⣤⣦⣧⣰⣴⣶⣷⣸⣼⣾⣿")
IDLE_MARK = "✳"

LIST_SCRIPT = '''
tell application "Terminal"
  set out to ""
  repeat with w in windows
    set out to out & (id of w) & "\\t" & (name of w) & "\\n"
  end repeat
  return out
end tell
'''


def sessions():
    """[(window_id, cwd, task, state)] for every Terminal window."""
    try:
        raw = subprocess.run(["osascript", "-e", LIST_SCRIPT],
                             capture_output=True, text=True, timeout=5).stdout
    except subprocess.SubprocessError:
        return []

    out = []
    for line in raw.strip().splitlines():
        if "\t" not in line:
            continue
        wid, _, title = line.partition("\t")
        try:
            wid = int(wid.strip())
        except ValueError:
            continue

        state = "idle"
        if any(c in SPINNER for c in title):
            state = "working"
        elif IDLE_MARK in title:
            state = "waiting"

        # "cwd — GLYPH task — proc — 133x45"
        parts = [p.strip() for p in title.split("—")]
        cwd = parts[0] if parts else "?"
        task = ""
        if len(parts) > 1:
            task = re.sub(r"^[\W_]+", "", parts[1]).strip()
        out.append((wid, cwd, task, state))
    return out


def focus(window_id: int) -> None:
    subprocess.run(
        ["osascript", "-e",
         f'tell application "Terminal" to set index of window id {window_id} to 1',
         "-e", 'tell application "Terminal" to activate'],
        capture_output=True, timeout=5)


STYLE = {
    "working": ("#12263A", "#3FA7D6"),
    "waiting": ("#2A2000", "#E8B923"),
    "idle":    ("#141414", "#3A3A3A"),
}


def main():
    page = [0]

    with Surface(brightness=70) as s:
        slots_per_page = 8
        current = {}

        def paint():
            found = sessions()
            pages = max(1, (len(found) + slots_per_page - 1) // slots_per_page)
            page[0] %= pages
            start = page[0] * slots_per_page
            chunk = found[start:start + slots_per_page]

            current.clear()
            slots = {}
            for i, (wid, cwd, task, state) in enumerate(chunk):
                bg, accent = STYLE[state]
                slots[i] = Slot(
                    label=cwd,
                    sub=task,
                    bg=bg,
                    accent=accent,
                    badge="●" if state == "working" else "",
                    key=str(wid),
                )
                current[i] = wid
            for i in range(len(chunk), slots_per_page):
                slots[i] = BLANK

            n = s.show(slots) or 0
            waiting = sum(1 for _, _, _, st in found if st == "waiting")
            working = sum(1 for _, _, _, st in found if st == "working")
            s.set_info(f"{len(found)} sessions",
                       f"{working} working  ·  {waiting} idle"
                       + (f"  ·  pg {page[0]+1}/{pages}" if pages > 1 else ""))
            s.set_touch(TOUCH_LEFT, (60, 60, 60) if pages > 1 else (0, 0, 0))
            s.set_touch(TOUCH_RIGHT, (60, 60, 60) if pages > 1 else (0, 0, 0))
            return n

        def on_press(index, long_press):
            if index == TOUCH_LEFT:
                page[0] -= 1
                paint()
                return
            if index == TOUCH_RIGHT:
                page[0] += 1
                paint()
                return
            wid = current.get(index)
            if wid:
                print(f"  -> focusing window {wid}"
                      + ("  (long press)" if long_press else ""))
                focus(wid)

        s.on_press(on_press)

        print("Painting… press keys to focus, touch pads to page, Ctrl-C to quit.")
        first = paint()
        print(f"  initial paint wrote {first} keys")
        print(f"  repeat flush wrote  {s.flush()} keys  (0 == diffing works)")

        s.run(tick=paint, interval=2.0)


if __name__ == "__main__":
    sys.exit(main() or 0)
