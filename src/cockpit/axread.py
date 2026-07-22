"""Reading a terminal's visible text over the Accessibility API.

This is the capability that dissolves the oldest constraint in this project.
[design.md](../../docs/design.md) concluded that hooks cannot detect a prompt's *shape*,
so accept/reject keys were unsafe — a key labelled "Yes" might send `1` into a
menu where `1` means something else entirely. That conclusion was right about
hooks and wrong about the ceiling: the screen itself is readable, and a prompt
we can read is a prompt we do not have to guess at.

Verified live (2026-07-21) against three tool prompts, which is what made the
danger concrete rather than theoretical:

    Bash outside project   1. Yes                              2. No
    Write outside project  1. Yes  2. Yes, +allow settings…    3. No
    WebFetch               1. Yes  2. Yes, +don't ask again…   3. No

**Option 2 is "No" in the first and "permanently widen permissions" in the
third.** Any fixed accept/always/reject bar sends the same digit to both. Only
reading the actual lines makes the keys mean what they say.

**Why pyobjc.** The visible text comes from `AXStringForRange`, a *parameterized*
attribute. AppleScript and System Events cannot call parameterized attributes at
all — `AXValue` on a terminal returns empty, and no amount of osascript gets
past that. This is the one thing in the project that genuinely needs a native
binding.

**Scoped to one window, deliberately.** An early probe read "the main window"
and silently captured a *different* session when focus moved mid-read — reading
the wrong session is the same class of bug as acting on the wrong one. The
scoping now comes from the caller proving the front window's **id** (stable)
through Terminal scripting before reading, rather than from matching a window
**title** (which mutates with the spinner glyph several times a second and made
every read fail — see `visible_text`).

Needs the Accessibility grant; `cockpit doctor` reports it, and ../../docs/operations.md
documents granting it. Every function degrades to None when it is missing.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger("deck.cockpit.axread")

try:                                    # pragma: no cover - import shape
    from ApplicationServices import (
        AXUIElementCopyAttributeValue,
        AXUIElementCopyParameterizedAttributeValue,
        AXUIElementCreateApplication,
        kAXErrorSuccess,
    )
    HAVE_AX = True
except ImportError:                     # pragma: no cover
    HAVE_AX = False

MAX_DEPTH = 6
# A prompt lives at the bottom of the screen; more than this is scrollback.
TAIL_LINES = 25


# --- the pure half: parsing what we read --------------------------------------

@dataclass(frozen=True)
class Prompt:
    """A menu currently on screen: its options, exactly as rendered.

    `options` is [(digit, label)] in screen order. `selected` is the digit the
    UI highlights with `❯`, if any. Nothing here is inferred — a label is the
    text the user is looking at, which is what makes it safe to put on a key.
    """

    options: tuple
    selected: Optional[int] = None
    # What the menu is *about*, lifted verbatim from the line Claude Code prints
    # above it ("Fetch https://example.com", "Create ~/.claude/probe.txt"). Empty
    # when the screen didn't offer one — it is presentation only.
    #
    # **Deliberately excluded from the press-time guard.** `_answer_key` compares
    # `options` and nothing else, because that is the part whose meaning a
    # keystroke depends on. Including this would let a cosmetic redraw of the
    # context line veto an answer that is still perfectly valid.
    subject: str = ""

    def digits(self) -> tuple:
        return tuple(d for d, _ in self.options)


# "❯ 1. Yes" / "  2. No, and tell Claude what to do differently"
_OPTION = re.compile(r"^\s*(❯)?\s*(\d)\.\s+(\S.*?)\s*$")
# The line Claude Code prints to announce what it is about to do. This is the
# genuinely informative text on the screen — "Fetch https://example.com" tells
# you what you are approving; "Do you want to proceed?" does not.
_SUBJECT = re.compile(r"^\s*⏺\s*(\S.*?)\s*$")
# The footer Claude Code renders under an interactive menu. Requiring it is the
# difference between "a menu is open" and "the scrollback happens to contain a
# numbered list" — transcripts are full of numbered lists.
_FOOTER = re.compile(r"Esc to (cancel|interrupt)", re.I)

# A menu can be open *and* have a text field live inside it — pick the "Type
# something" option and the menu stays on screen while your cursor moves into
# it. Digits then land in the field as characters. Observed live: the deck
# happily typed "1234" into a message box, and would also have relabelled its
# keys with the half-typed sentence, because the option's own label mutates
# into whatever you are writing.
#
# The footer gives it away: this hint appears only while a text input is
# active. Two states, one menu, and the difference is a single line.
_TEXT_INPUT = re.compile(r"ctrl\+g to edit|edit in Vim", re.I)


def prompt_ui_present(text: str) -> bool:
    """Is ANY prompt UI on screen — not necessarily an answerable menu?

    Deliberately weaker than `parse_prompt`, and the difference is the whole
    point. `parse_prompt` returns None for several screens that are still very
    much holding you: a live text field ("tell Claude what to do differently"),
    a menu with more options than we will render, a shape we refuse to parse.
    Treating "no answerable menu" as "no prompt" would clear a flag on a session
    that is still waiting for you to type.

    This asks only whether Claude Code's prompt footer is on screen. Its
    ABSENCE is evidence that nothing is being asked — the missing clearing edge
    for a denial, where no hook fires at all.

    **Known limit, accepted deliberately.** The free-text follow-up ("tell
    Claude what to do differently") renders no footer either, so this reads it
    as "no prompt" and its flag clears — a session that is still waiting for you
    to type goes quiet on the board. Two things bound that:

      - the probe only ever runs against the FOCUSED session, so the window it
        can mislead you about is the one already in front of you; and
      - submitting the text fires UserPromptSubmit, which puts the session back
        to working immediately.

    The cost is a half-typed box you walked away from showing idle instead of
    waiting. The alternative was pattern-matching the follow-up's box-drawing,
    which is brittle against a UI that has already changed once — and a fragile
    detector here fails toward a permanent false red, which is strictly worse
    than this. Revisit if it ever bites.
    """
    if not text:
        return False
    return any(_FOOTER.search(l) for l in text.splitlines()[-TAIL_LINES:])


def parse_prompt(text: str) -> Optional[Prompt]:
    """Find the live menu in a terminal's visible text, or None.

    Conservative on purpose — every rule here exists to make a false positive
    (offering answer keys when no menu is open) impossible rather than unlikely:

      - the `Esc to cancel` footer must be present, and the options must sit
        *above* it, so old prompts in scrollback can't match;
      - at least two options, numbered consecutively from 1;
      - no gaps or repeats.

    Returning None is the safe answer and the common one. Two ways a screen
    that *looks* answerable isn't:

      - a free-text follow-up ("tell Claude what to do differently") has no
        numbered options at all; and
      - a menu with its text field active still shows every option, but a
        digit typed there becomes a character. The footer betrays it.
    """
    if not text:
        return None
    lines = text.splitlines()[-TAIL_LINES:]

    footer_at = None
    for i in range(len(lines) - 1, -1, -1):
        if _FOOTER.search(lines[i]):
            footer_at = i
            break
    if footer_at is None:
        return None

    # A live text field beats a visible menu: whatever we send becomes text.
    if any(_TEXT_INPUT.search(l) for l in lines):
        log.debug("text input active — suppressing answer keys")
        return None

    options, selected = [], None
    first_option_at = None
    for i, line in enumerate(lines[:footer_at]):
        m = _OPTION.match(line)
        if not m:
            continue
        if first_option_at is None:
            first_option_at = i
        marker, digit, label = m.group(1), int(m.group(2)), m.group(3)
        options.append((digit, label))
        if marker:
            selected = digit

    # The subject is read from *above* the first option, nearest-first, so a
    # menu that follows several announcements picks up the one it belongs to.
    # Purely additive: it can only ever be "" and never changes whether a menu
    # is recognised, which keeps the safety rules above untouched.
    subject = ""
    for line in reversed(lines[:first_option_at if first_option_at is not None
                               else footer_at]):
        m = _SUBJECT.match(line)
        if m:
            subject = m.group(1)
            break

    if len(options) < 2:
        return None
    # Consecutive from 1. Anything else means we mis-read the screen, and a
    # mis-read menu is worse than no menu.
    if [d for d, _ in options] != list(range(1, len(options) + 1)):
        log.debug("discarding non-consecutive options: %s", options)
        return None
    return Prompt(options=tuple(options), selected=selected,
                  subject=subject)


# --- the impure half: getting the text ----------------------------------------

def _attr(el, name):
    err, val = AXUIElementCopyAttributeValue(el, name, None)
    return val if err == kAXErrorSuccess else None


def _find(el, role, depth: int = 0):
    if el is None or depth > MAX_DEPTH:
        return None
    if _attr(el, "AXRole") == role:
        return el
    for child in (_attr(el, "AXChildren") or []):
        got = _find(child, role, depth + 1)
        if got is not None:
            return got
    return None


def visible_text(pid: int, window_title: Optional[str] = None) -> Optional[str]:
    """Visible text of the app's frontmost window, or of a titled one.

    **Title matching is the fallback, not the default, and that was a bug worth
    recording.** The first cut matched `AXTitle` against the title the poller
    captured — but a Claude Code window title contains the live spinner glyph
    (`⠂ ⠃ ⠄ …`), which changes several times a second. So the cached title
    almost never equalled the current one, `visible_text` returned None, and
    every answer key refused with "no menu on screen any more" while the menu
    was plainly on screen. It only ever worked when the glyph happened to match.

    The caller instead proves *which* window this is through Terminal scripting
    (a stable window id, not a mutating string) and then reads the frontmost
    window here. Same scoping guarantee, no dependence on a volatile title.
    """
    if not HAVE_AX:
        return None
    try:
        app = AXUIElementCreateApplication(pid)
        windows = _attr(app, "AXWindows") or []
        for win in windows:
            if window_title is not None:
                if _attr(win, "AXTitle") != window_title:
                    continue
            elif not _attr(win, "AXMain"):
                continue
            area = _find(win, "AXTextArea")
            if area is None:
                return None
            rng = _attr(area, "AXVisibleCharacterRange")
            if rng is None:
                return None
            err, text = AXUIElementCopyParameterizedAttributeValue(
                area, "AXStringForRange", rng, None)
            if err != kAXErrorSuccess or not text:
                return None
            return str(text)
    except Exception:
        # A denied grant, a window closing mid-read, a pyobjc surprise — none of
        # them may take down the poll loop. No text means no answer keys.
        log.debug("AX read failed for %r", window_title, exc_info=True)
    return None


def read_prompt(pid: int, window_title: Optional[str] = None) -> Optional[Prompt]:
    """visible_text + parse_prompt, the way callers actually use this."""
    return parse_prompt(visible_text(pid, window_title) or "")
