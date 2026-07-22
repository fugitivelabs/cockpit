"""Framework logic tests — no device needed. Exercises components, views,
paging, and press routing purely against return values."""
import os
import sys

# project root is the parent of tests/ — portable, no absolute path
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from deck import BLANK, Button, Live, PagedView, Slot, Static, View, meter

ok = 0
fail = 0

def check(name, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  PASS  {name}" + (f" — {detail}" if detail else ""))
    else:
        fail += 1
        print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))


print("\n[components]")
s = Static(Slot(label="x"))
check("Static renders its slot", s.render().label == "x")
check("Static inert on press", s.on_press(False) is False)

pressed = []
b = Button(Slot(label="go"), on_press=lambda: pressed.append("short"),
           on_long=lambda: pressed.append("long"))
b.on_press(False); b.on_press(True)
check("Button short+long route", pressed == ["short", "long"], str(pressed))
check("Button handled returns True", Button(Slot(), on_press=lambda: None).on_press(False))
check("Button no-cb returns False", Button(Slot()).on_press(False) is False)

n = [0]
live = Live(lambda: Slot(label=str(n[0])))
n[0] = 5
check("Live re-renders from state", live.render().label == "5")

print("\n[meter]")
m = meter(lambda: 0.5)
check("meter fills to value", m.render().bar == 0.5)
check("meter default label is pct", m.render().label == "50%")
check("meter normal color below warn", m.render().bar_color == "#3FA7D6")
mh = meter(lambda: 0.9)
check("meter warns past threshold", mh.render().bar_color == "#E8B923", mh.render().bar_color)
mover = meter(lambda: 1.7)
check("meter clamps >1", mover.render().bar == 1.0)

print("\n[view routing]")
hits = []
v = View([Button(Slot(label=str(i)), on_press=lambda i=i: hits.append(i))
          for i in range(3)])
sl = v.slots()
check("view slots fill grid", len(sl) == 8 and sl[0].label == "0")
check("view unfilled keys blank", sl[7].blank())
v.press(1, False)
check("view routes press to component", hits == [1], str(hits))
check("view press on empty key safe", v.press(6, False) is False)

print("\n[paged view]")
pv = PagedView([Static(Slot(label=str(i))) for i in range(20)])
check("pages computed", pv.pages == 3, f"{pv.pages} pages for 20 @ 8")
check("page 0 shows first 8", pv.slots()[0].label == "0" and pv.slots()[7].label == "7")
check("right pages forward", pv.on_touch("right") and pv.slots()[0].label == "8")
check("second page shows 8..15", pv.slots()[7].label == "15")
pv.on_touch("right")
check("third page partial", pv.slots()[3].label == "19" and pv.slots()[4].blank())
check("wraps around", pv.on_touch("right") and pv.page == 0)
check("left wraps back", pv.on_touch("left") and pv.page == 2)

single = PagedView([Static(Slot(label="a"))])
check("single page ignores touch", single.on_touch("right") is False)

print("\n[paged routing to correct component]")
routed = []
pv2 = PagedView([Button(Slot(label=str(i)), on_press=lambda i=i: routed.append(i))
                 for i in range(16)])
pv2.on_touch("right")          # page 1 -> components 8..15
pv2.press(0, False)            # slot 0 of page 1 == component 8
check("press maps through paging", routed == [8], str(routed))

print(f"\n=== {ok} passed, {fail} failed ===")
sys.exit(1 if fail else 0)
