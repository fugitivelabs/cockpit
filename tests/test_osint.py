"""OS-introspection tests — the pure parse half, no GUI needed.

frontmost() itself needs a windowing session and TCC permission, so it's proven
live, not in CI. parse_focus() carries the logic and is fully testable here.
"""
import os
import sys

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from cockpit.osint import Focus, parse_focus

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


print("\n[parse_focus]")

f = parse_focus("Terminal\tcom.apple.Terminal\t1234\tProjects — build deck — 120x40\n")
check("parses a full reply", f is not None)
check("app name", f and f.app == "Terminal", f and f.app)
check("bundle id", f and f.bundle_id == "com.apple.Terminal")
check("pid is int", f and f.pid == 1234 and isinstance(f.pid, int))
check("window title", f and f.window_title.startswith("Projects — build"), f and f.window_title)

f2 = parse_focus("Finder\tcom.apple.finder\t567\t")
check("empty window title is fine (Accessibility not granted)", f2 and f2.window_title == "")

f3 = parse_focus("iTerm\tcom.googlecode.iterm2\t99\ta\tb\tc")
check("title keeps embedded tabs", f3 and f3.window_title == "a\tb\tc", f3 and f3.window_title)

check("blank input -> None", parse_focus("") is None)
check("whitespace-only -> None", parse_focus("\n  \n") is None)
check("too few fields -> None", parse_focus("Terminal\tcom.apple.Terminal") is None)
check("non-numeric pid -> None", parse_focus("A\tb\tNOTPID\ttitle") is None)
check("missing app name -> None", parse_focus("\tcom.x\t12\tt") is None)

check("Focus is a frozen value object",
      parse_focus("A\tb\t1\tt") == Focus(app="A", bundle_id="b", pid=1, window_title="t"))

print(f"\n=== {ok} passed, {fail} failed ===")
sys.exit(1 if fail else 0)
