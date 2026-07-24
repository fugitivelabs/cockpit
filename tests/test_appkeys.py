"""Browser action-bar tests — the window/tab index rules and the bar swap.

Headless. The Accessibility calls themselves need a real GUI and a TCC grant, so
they are proven live; what is tested here is the logic that decides *which*
window to raise and *which* tab to select, plus the rule that swaps the action
bar when you are not in a session at all.

The window-cycling rule is the one worth pinning: the obvious implementation
oscillates between two windows forever and can never reach a third.
"""
import os
import sys
import time
from types import SimpleNamespace

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from cockpit import actions as A
from cockpit.dashboard import ACTION_KEYS
from fleet.macos.axapp import next_window_index, tab_target

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


def _settled(read, tries: int = 200) -> bool:
    """Wait for a key's action to land. Presses run on their own thread — the
    run loop must keep painting while an osascript round-trip blocks."""
    for _ in range(tries):
        if read():
            return True
        time.sleep(0.01)
    return False


print("\n[next_window_index] raise the BACKMOST window, or you oscillate")

check("no windows means nothing to do", next_window_index(0) is None)
check("one window means nothing to do", next_window_index(1) is None,
      "there is nowhere to cycle to")
check("two windows raises the other one", next_window_index(2) == 1)
check("three windows raises the backmost, NOT index 1",
      next_window_index(3) == 2,
      "raising index 1 would swap the front pair forever")
check("five windows likewise", next_window_index(5) == 4)

# The property that matters: repeatedly raising the backmost window visits
# every window and returns to the start. Modelled on a list, front-to-back.
order = ["A", "B", "C"]
seen = [order[0]]
for _ in range(3):
    t = next_window_index(len(order))
    order = [order[t]] + [w for i, w in enumerate(order) if i != t]
    seen.append(order[0])
check("cycling three windows visits all of them and returns",
      seen == ["A", "C", "B", "A"], " -> ".join(seen))

order2 = ["A", "B"]
seen2 = [order2[0]]
for _ in range(2):
    t = next_window_index(len(order2))
    order2 = [order2[t]] + [w for i, w in enumerate(order2) if i != t]
    seen2.append(order2[0])
check("…and two windows alternate correctly",
      seen2 == ["A", "B", "A"], " -> ".join(seen2))


print("\n[tab_target] resolving first/last against a live count")

check("no tabs is None, not 0", tab_target(0, "first") is None,
      "pressing must do nothing, not raise")
check("no tabs is None for last too", tab_target(0, "last") is None)
check("one tab: first and last are the same",
      tab_target(1, "first") == 0 and tab_target(1, "last") == 0)
check("first is always 0", tab_target(129, "first") == 0)
check("last is count-1", tab_target(129, "last") == 128,
      "the real strip observed live")


print("\n[same_bundle] macOS is not consistent about bundle-id casing")

check("System Events casing vs LaunchServices casing must match",
      A.same_bundle("com.google.Chrome", "com.google.chrome"),
      "the exact pair macOS actually produces")
check("Safari likewise", A.same_bundle("com.apple.Safari", "com.apple.safari"))
check("Firefox is lowercase either way — which is why this hid",
      A.same_bundle("org.mozilla.firefox", "org.mozilla.firefox"))
check("different apps still differ",
      not A.same_bundle("com.google.Chrome", "org.mozilla.firefox"))
check("None is never a match", not A.same_bundle(None, "com.google.Chrome"))
check("empty is never a match", not A.same_bundle("", ""))


print("\n[resolve_browser] explicit setting, else the system default")

import fleet.macos.osint as osint_mod                                # noqa: E402

_real_default = A.default_browser
try:
    A.default_browser = lambda: "com.google.chrome"     # as LaunchServices stores it
    check("auto follows the system default",
          A.resolve_browser("auto") == ("com.google.Chrome", "Chrome"),
          "and returns the CANONICAL casing, not the plist's")

    check("an explicit setting overrides the default",
          A.resolve_browser("firefox") == ("org.mozilla.firefox", "Firefox"))
    check("…case-insensitively", A.resolve_browser("CHROME")[1] == "Chrome")
    check("safari resolves too",
          A.resolve_browser("safari") == ("com.apple.Safari", "Safari"))

    A.default_browser = lambda: None
    check("no recorded default falls back to Firefox",
          A.resolve_browser("auto")[1] == "Firefox",
          "macOS records no handler until you change it")

    A.default_browser = lambda: "com.operasoftware.opera"
    check("an unrecognised default falls back rather than breaking",
          A.resolve_browser("auto")[1] == "Firefox")

    A.default_browser = lambda: "com.google.chrome"
    check("an unknown explicit name falls back too",
          A.resolve_browser("netscape")[1] == "Firefox",
          "a bad flag costs the browser keys, not the deck")
finally:
    A.default_browser = _real_default


print("\n[browser_keys] the row shown while a browser is in front")

class FakeDash:
    """Only what the browser row asks a dashboard: what to go back to."""

    def __init__(self, top=None):
        self.top = top
        self.focused = []

    def top_session(self):
        return self.top

    def focus_now(self, s):
        self.focused.append(s.id)


_back = SimpleNamespace(id="s1", cwd="peregrine")
dash = FakeDash(_back)
bar = A.browser_keys(dash)
keys = list(ACTION_KEYS)
check("fills all four action slots", sorted(bar) == sorted(keys))

labels = [(bar[k].render().label, bar[k].render().sub) for k in keys[:3]]
check("the three keys are the ones asked for",
      labels == [("next", "window"), ("first", "tab"), ("last", "tab")],
      str(labels))
check("the fourth slot goes back to a session, and names which",
      (bar[keys[3]].render().label, bar[keys[3]].render().sub)
      == ("peregrine", "back"),
      str((bar[keys[3]].render().label, bar[keys[3]].render().sub)))
check("…and pressing it focuses that session",
      bar[keys[3]].on_press(False) is True and _settled(lambda: dash.focused)
      and dash.focused == ["s1"], str(dash.focused))
check("…while with no sessions at all it dims rather than lying",
      not A.browser_keys(FakeDash(None))[keys[3]].enabled())
check("…and shows an em dash",
      A.browser_keys(FakeDash(None))[keys[3]].render().label == "—")
check("the three keys ARE pressable",
      all(bar[k].on_press is not None for k in keys[:3]))

chrome_bar = A.browser_keys(dash, "com.google.Chrome", "Chrome")
check("the keys are identical for another browser",
      [(chrome_bar[k].render().label, chrome_bar[k].render().sub) for k in keys[:3]]
      == labels,
      "same three moves; only the app they act on changes")
check("…but the action names identify the browser",
      chrome_bar[keys[0]].name == "chrome-next-window")
check("…and the slot cache keys differ, so a switch repaints",
      chrome_bar[keys[0]].render().key != bar[keys[0]].render().key)


print("\n[_is_tab] a tab says so — role alone is not enough")

from fleet.macos import axapp                                        # noqa: E402

check("web content is never descended into",
      "AXWebArea" in axapp.WEB_ROLES,
      "a page's ARIA tablist is indistinguishable from a tab strip by role")
check("the depth bound reaches Chrome's strip at 7-8",
      axapp.MAX_DEPTH >= 8, f"MAX_DEPTH={axapp.MAX_DEPTH}")
check("a tab is identified by role description",
      axapp.TAB_ROLE_DESCRIPTION == "tab")


print("\n[_app_action] the press-time guard")

import fleet.macos.osint as osint_mod                                # noqa: E402
from fleet.macos.osint import Focus                                  # noqa: E402

FF = Focus("Firefox", "org.mozilla.firefox", 61134, "")
TERM = Focus("Terminal", "com.apple.Terminal", 42, "")

called = []
_real = osint_mod.frontmost
# actions.py imported frontmost by name, so patch it where it is used
_real_in_actions = A.frontmost
try:
    act = A._app_action("org.mozilla.firefox", "test",
                        lambda pid: called.append(pid) or True)

    A.frontmost = lambda: FF
    act(False)
    check("acts when the target app is frontmost", called == [61134],
          "and passes the LIVE pid, not a cached one")

    called.clear()
    A.frontmost = lambda: TERM
    act(False)
    check("refuses when another app took focus since the paint", called == [],
          "the bar may be two seconds old")

    called.clear()
    A.frontmost = lambda: None
    act(False)
    check("refuses when focus is unreadable", called == [],
          "a denied TCC grant must not read as permission")
finally:
    A.frontmost = _real_in_actions
    osint_mod.frontmost = _real


print(f"\n=== {ok} passed, {fail} failed ===")
sys.exit(1 if fail else 0)
