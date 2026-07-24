"""Driving another application's windows and tabs over the Accessibility API.

The companion to `axread`, which *reads* a terminal's screen; this *acts* on a
GUI — raise a window, select a tab. App-agnostic by construction: everything
takes a pid and asks the Accessibility tree what is there, so nothing here knows
what Firefox is. Firefox is simply the first caller, and the one that motivated
it.

**Why this exists at all, given `firefox-tabs.md` concluded tabs were out of
reach.** That research was right about AppleScript — Firefox ships no `.sdef`,
`tab` is not a class, and the tracking bugs have been dead for twenty years —
and it deferred tab control to a browser extension plus a native messaging host.
But it evaluated AppleScript, not Accessibility, and the two are unrelated
surfaces. Firefox exposes an `AXTabGroup` whose children are the individual tabs,
each carrying its title, its selected state, and an `AXPress` action. Measured
2026-07-23 on a 129-tab window: locating the group and its children costs 0.036s
and reading every title another 0.004s — cheaper than the one `frontmost()` call
the poller already makes. No extension, no native host.

What Accessibility still does **not** give is a per-tab URL (`AXURL` on a tab is
None); only the active tab's document URL is readable, from the window's
`AXWebArea`. So the extension bridge remains the answer if URLs are ever needed.
Titles and switching do not need it.

**Why `AXPress` rather than synthesizing Cmd-1 / Cmd-9.** Those shortcuts would
work, but a keystroke goes to whatever is frontmost and carries the wrong-window
hazard that `osint.keystroke` is deliberately gated behind. `AXPress` names the
element it acts on, so it cannot land somewhere unintended. It is the safer
mechanism as well as the more capable one.

Every function degrades to a falsy result when the Accessibility grant is
missing, exactly as `axread` does — no exceptions escape into a poll loop or a
key press.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger("fleet.axapp")

try:                                    # pragma: no cover - import shape
    from ApplicationServices import (
        AXUIElementCopyAttributeValue,
        AXUIElementCreateApplication,
        AXUIElementPerformAction,
        kAXErrorSuccess,
    )
    HAVE_AX = True
except ImportError:                     # pragma: no cover
    HAVE_AX = False

# The tab strip sits shallow in the tree; this bounds the walk so a deeply
# nested page (an embedded document, a devtools panel) can't turn a key press
# into a long descent.
MAX_DEPTH = 8

# Firefox models tabs as radio buttons inside an AXTabGroup — one selected at a
# time, which is exactly what a radio group means. Other apps may use AXTab;
# both are accepted so this stays app-agnostic.
TAB_ROLES = ("AXRadioButton", "AXTab")


@dataclass(frozen=True)
class Tab:
    """One tab, as the Accessibility tree describes it."""

    index: int
    title: str
    selected: bool


@dataclass(frozen=True)
class Window:
    """One window. `main` is the one currently in front within its app."""

    index: int
    title: str
    main: bool


# --- the pure half ------------------------------------------------------------

def next_window_index(count: int) -> Optional[int]:
    """Which window to raise to cycle forward. None when there is nothing to do.

    **Raise the BACKMOST window, not the second one.** This is the whole trick,
    and the obvious alternative is wrong. `AXWindows` comes back front-to-back,
    so "the next window" reads naturally as index 1 — but raising index 1 makes
    it index 0, and the window you came from becomes index 1, so the next press
    brings you straight back. With three or more windows you oscillate between
    two of them forever and can never reach the third.

    Raising the last one rotates the whole list instead:

        [A B C] -> raise C -> [C A B] -> raise B -> [B C A] -> raise A -> [A B C]

    which visits every window in order and returns to where it started. With two
    windows it degenerates to the same alternation you would have wanted anyway,
    and with one there is nothing to raise.
    """
    if count < 2:
        return None
    return count - 1


def tab_target(count: int, which: str) -> Optional[int]:
    """Resolve "first"/"last" against a live tab count. None if there are none.

    Deliberately returns None rather than 0 for an empty strip: pressing a key
    that resolves to "tab 0 of 0" should do nothing, not raise.
    """
    if count <= 0:
        return None
    return 0 if which == "first" else count - 1


# --- the impure half ----------------------------------------------------------

def _attr(el, name):
    err, val = AXUIElementCopyAttributeValue(el, name, None)
    return val if err == kAXErrorSuccess else None


def _app(pid: int):
    return AXUIElementCreateApplication(pid) if HAVE_AX else None


def _windows(app):
    return _attr(app, "AXWindows") or []


def _find(el, roles, depth: int = 0):
    """First descendant whose role is in `roles`, breadth-agnostic. None if absent."""
    if el is None or depth > MAX_DEPTH:
        return None
    if _attr(el, "AXRole") in roles:
        return el
    for child in (_attr(el, "AXChildren") or []):
        got = _find(child, roles, depth + 1)
        if got is not None:
            return got
    return None


def _front_window(app):
    """The window in front within this app: AXMain, else the first listed."""
    wins = _windows(app)
    if not wins:
        return None
    for w in wins:
        if _attr(w, "AXMain"):
            return w
    return wins[0]


def _tab_elements(app):
    """The tab elements of the front window, in strip order."""
    win = _front_window(app)
    if win is None:
        return []
    group = _find(win, ("AXTabGroup",))
    if group is None:
        return []
    return [c for c in (_attr(group, "AXChildren") or [])
            if _attr(c, "AXRole") in TAB_ROLES]


def windows(pid: int) -> list[Window]:
    """Every window of that app, front-to-back. [] if unreadable."""
    if not HAVE_AX:
        return []
    try:
        out = []
        for i, w in enumerate(_windows(_app(pid))):
            out.append(Window(index=i, title=str(_attr(w, "AXTitle") or ""),
                              main=bool(_attr(w, "AXMain"))))
        return out
    except Exception:
        log.debug("window enumeration failed for pid %s", pid, exc_info=True)
        return []


def tabs(pid: int) -> list[Tab]:
    """Tabs of the app's front window, in strip order. [] if none or unreadable."""
    if not HAVE_AX:
        return []
    try:
        return [Tab(index=i, title=str(_attr(t, "AXTitle") or ""),
                    selected=bool(_attr(t, "AXValue")))
                for i, t in enumerate(_tab_elements(_app(pid)))]
    except Exception:
        log.debug("tab enumeration failed for pid %s", pid, exc_info=True)
        return []


def focus_next_window(pid: int) -> bool:
    """Cycle to the next window of that app. False if there was nothing to do.

    See `next_window_index` for why this raises the backmost window.
    """
    if not HAVE_AX:
        return False
    try:
        app = _app(pid)
        wins = _windows(app)
        target = next_window_index(len(wins))
        if target is None:
            log.debug("only %d window(s) — nothing to cycle to", len(wins))
            return False
        err = AXUIElementPerformAction(wins[target], "AXRaise")
        if err != kAXErrorSuccess:
            log.warning("AXRaise failed (%s) on window %d", err, target)
            return False
        return True
    except Exception:
        log.debug("focus_next_window failed for pid %s", pid, exc_info=True)
        return False


def select_tab(pid: int, which: str) -> bool:
    """Select the "first" or "last" tab of the front window.

    Named rather than indexed on purpose: the caller wants an end of the strip,
    and resolving that against the live count here means a key never carries a
    stale index from whenever the board was last painted.
    """
    if not HAVE_AX:
        return False
    try:
        els = _tab_elements(_app(pid))
        target = tab_target(len(els), which)
        if target is None:
            log.debug("no tabs to select")
            return False
        err = AXUIElementPerformAction(els[target], "AXPress")
        if err != kAXErrorSuccess:
            log.warning("AXPress failed (%s) on tab %d", err, target)
            return False
        return True
    except Exception:
        log.debug("select_tab(%s) failed for pid %s", which, pid, exc_info=True)
        return False
