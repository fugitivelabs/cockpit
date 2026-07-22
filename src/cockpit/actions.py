"""The action bar — the four fixed keys under the session board.

Everything here is navigation or surface housekeeping. **Nothing types into an
existing session**, which keeps Stage 1's central safety property intact: the
wrong-session hazard needs keystroke synthesis, and there is none here. That
line is deliberate and worth defending — when Stage 3 adds accept/reject, it
gets the focus guard, and it does not get to reuse these keys' machinery
unchanged.

Actions are built as factory functions rather than subclasses because each one
is a slot plus a closure; a class per action would be four near-identical
bodies. `ActionKey` (dashboard.py) supplies the shared behaviour: run the
callback off the loop thread, dim the key when it can't do anything.
"""

from __future__ import annotations

import logging
from typing import Optional

from deck import Slot

from . import palette
from .dashboard import ACTION_KEYS, ActionKey, Dashboard
from .osint import activate, keystroke

log = logging.getLogger("deck.cockpit.actions")

# The action bar is furniture, and it looks like furniture: no state rule, no
# hue, value centred over a small-caps caption. That structural difference is
# what tells you at a glance which row is information and which row is controls
# — the board above it is the only place colour means anything.
ACTION_BG = palette.FURNITURE
ACTION_ACCENT = None


def _furniture(label: str, sub: str, key: str, bar=None, bar_color=None) -> Slot:
    """An action-bar key. One shape, so the whole bar reads as one object."""
    return Slot(label=label, sub=sub, caps=True, align="center",
                bg=ACTION_BG, fg=palette.INK, sub_fg=palette.INK_DIM,
                bar=bar, bar_color=bar_color or palette.METER, key=key)


def jump_to_app(name: str, bundle_id: str, label: Optional[str] = None) -> ActionKey:
    """Bring an app forward. The generic 'go somewhere that isn't a session' key.

    Uses LaunchServices (`open -b`) via osint.activate, so it needs no
    Automation permission and works whether or not the app is already running.
    """
    slot = _furniture(label or name, "app", f"app:{bundle_id}")

    def run(long: bool) -> None:
        if not activate(bundle_id):
            log.warning("could not activate %s (%s)", name, bundle_id)

    return ActionKey(slot, run, name=f"jump:{name}")


def jump_to_top(dashboard: Dashboard) -> ActionKey:
    """Focus the most urgent session without picking a tile.

    The attention-assistant move in one press: you don't read the board, you
    just go where it would have sent you. The key names its own target so it is
    never a mystery press — and it dims when there are no sessions at all.

    Today "most urgent" resolves to the top of the ordering (the working
    session, else the lowest window id). When Stage 2's hooks land it becomes
    literally "the one that needs you", with no change here — urgency lives in
    the ordering, not in this key.
    """
    def slot() -> Slot:
        top = dashboard.top_session()
        if top is None:
            return _furniture("—", "top", "top:none")
        return _furniture(top.cwd, "top", f"top:{top.id}")

    def run(long: bool) -> None:
        top = dashboard.top_session()
        if top is not None:
            dashboard.focus_now(top)      # already off the loop thread

    return ActionKey(slot, run, enabled=lambda: dashboard.top_session() is not None,
                     name="top")


def refresh(dashboard: Dashboard) -> ActionKey:
    """Re-poll the session list right now instead of waiting out the interval.

    Small, but it's the key you want the moment you've just opened or closed a
    session and the board hasn't caught up — two seconds is not long unless
    you're staring at it.
    """
    def slot() -> Slot:
        age = dashboard.poller.age()
        sub = "—" if age is None else f"{int(age)}s ago"
        return _furniture(sub, "refresh", f"refresh:{sub}")

    return ActionKey(slot, lambda long: dashboard.poller.poll_once(),
                     name="refresh")


# Bright → dim → dark. Dark is a real state, not off: the daemon keeps running
# and a press still registers, so the deck can be silenced without stopping it.
BRIGHTNESS_CYCLE = (70, 30, 0)


def brightness(surface) -> ActionKey:
    """Cycle panel brightness; long-press jumps straight back to full.

    Deliberately a cycle rather than up/down keys — that would spend two of the
    four action slots on something used a few times a day.
    """
    def slot() -> Slot:
        level = surface.brightness
        return _furniture(f"{level}%", "light", f"bright:{level}",
                          bar=level / 100.0, bar_color=palette.METER)

    def run(long: bool) -> None:
        if long:
            surface.set_brightness(BRIGHTNESS_CYCLE[0])
            return
        current = surface.brightness
        # Nearest cycle step, so a level set elsewhere still advances sensibly.
        nearest = min(range(len(BRIGHTNESS_CYCLE)),
                      key=lambda i: abs(BRIGHTNESS_CYCLE[i] - current))
        surface.set_brightness(BRIGHTNESS_CYCLE[(nearest + 1) % len(BRIGHTNESS_CYCLE)])

    return ActionKey(slot, run, name="brightness")


def _short_model(name: str) -> str:
    """"Opus 4.8 (1M context)" -> "Opus 4.8". The key is 96px wide."""
    if not name:
        return "—"
    head = name.split("(")[0].strip()
    return " ".join(head.split()[:2]) or head


def session_info(dashboard: Dashboard) -> dict:
    """Three read-only keys describing the session you're looking at.

    Grant's call (2026-07-21): under an idle or working session, the bar should
    say model, context %, and API cost. All three come from the statusline, so
    this costs nothing extra and takes no permission.

    They are deliberately inert — no press behaviour. The session they describe
    is already the one in front of you, so "go there" is meaningless, and a key
    that looks pressable but does nothing is worse than one that plainly isn't.
    """
    def focused():
        return dashboard.focused_session()

    def has_focus() -> bool:
        return focused() is not None

    def model_slot() -> Slot:
        s = focused()
        return _furniture(_short_model(s.model if s else ""), "model", "info:model")

    def context_slot() -> Slot:
        s = focused()
        pct = s.telemetry.context_pct if (s and s.telemetry) else None
        if pct is None:
            return _furniture("—", "context", "info:ctx")
        # Amber past 80% — "approaching a limit" is what caution means, the one
        # cross-cutting reuse of a state hue the palette allows.
        return _furniture(f"{int(pct)}%", "context", f"info:ctx:{int(pct)}",
                          bar=pct / 100.0,
                          bar_color=palette.meter_color(pct / 100.0))

    def cost_slot() -> Slot:
        s = focused()
        usd = s.telemetry.cost_usd if (s and s.telemetry) else None
        if usd is None:
            return _furniture("—", "cost", "info:cost")
        text = f"${usd:,.0f}" if usd >= 100 else f"${usd:.2f}"
        return _furniture(text, "cost", f"info:cost:{text}")

    keys = list(ACTION_KEYS)
    return {
        keys[0]: ActionKey(model_slot, None, enabled=has_focus, name="model"),
        keys[1]: ActionKey(context_slot, None, enabled=has_focus, name="context"),
        keys[2]: ActionKey(cost_slot, None, enabled=has_focus, name="cost"),
    }


# Answer keys — the only keys on the deck that change another program's state,
# and the only place green appears.
#
# Green for "Yes" is the most over-learned mapping in computing and it stays.
# Red for "No" does not: red is reserved for "a session needs you" (palette.py),
# and declining a permission prompt is always the safe move, so alarm-colouring
# it would both misspend the deck's scarcest signal and push you toward
# approving. Decline is a bright neutral instead — easy to find, not an alarm.
ANSWER_YES = palette.ANSWER_AFFIRM
ANSWER_NO = palette.ANSWER_DECLINE
ANSWER_OTHER = palette.ANSWER_GRANT


def _answer_icon(label: str) -> str:
    """A shape for what the option says, or "" when we cannot tell.

    The empty case is load-bearing. Real menus are not always yes/no — Grant hit
    a Left / Right / type something / Chat prompt — and stamping a checkmark on
    an option whose meaning we have not established would be inventing exactly
    the semantics prompts.md forbids inferring. No icon is the honest render.
    """
    low = label.lower()
    if low.startswith("no"):
        return "cross"
    if low.startswith("yes"):
        # A second tick for the approval that also widens permission: it says
        # "and again, and again", which is precisely what it does.
        return "check-double" if ("," in label or " and " in low) else "check"
    return ""


def _answer_text(label: str) -> str:
    """The part of the option worth putting on a 96 px key.

    Previously this was `label.split(",")[0]`, which threw away the only thing
    that distinguished the options: "Yes" and "Yes, and don't ask again for
    example.com" both rendered as the word `Yes`. Two identical green keys, one
    of which grants a standing permission.

    So for an approval the TAIL is the label — "don't ask again for example.com"
    — because the head is the part every option shares. For a decline the head
    is the decision and the tail is a follow-up detail ("and tell Claude what to
    do differently"), so the head wins. Everything here is the screen's own
    words; nothing is invented.
    """
    head, _, tail = label.partition(",")
    head, tail = head.strip(), tail.strip()
    if head.lower().startswith("no") or not tail:
        return head
    if tail.lower().startswith("and "):
        tail = tail[4:]
    return tail


def _answer_style(label: str) -> str:
    """Colour by what the option *says*, since that's all we can trust.

    A plain "Yes" is green and a "No" is neutral, but anything of the
    "Yes, and don't ask again…" family is amber — it is an approval that also
    widens permissions, and it must not look like the safe one. This is
    presentation only; the label on the key is always the screen's own text.
    """
    low = label.lower()
    if low.startswith("no"):
        return ANSWER_NO
    if low.startswith("yes") and ("," in label or "and" in low):
        return ANSWER_OTHER
    if low.startswith("yes"):
        return ANSWER_YES
    return ANSWER_OTHER


def answer_keys(dashboard: Dashboard, reader=None) -> Optional[dict]:
    """Keys for the menu actually on screen, or None if there isn't one.

    This is the payoff for reading the screen: the keys are labelled with the
    options themselves, so nothing is inferred. `2` is "No" on a Bash prompt and
    "allow settings edits" on a Write prompt — a fixed bar sends the same digit
    to both, and this cannot.

    The guard is the strongest one available and it is why this is safe at all:
    at press time we **re-read the screen** and require the identical option
    list to still be there. Not a stale flag, not a timer — the menu itself.
    If anything changed, the press does nothing.
    """
    prompt = dashboard.focused_prompt()
    if prompt is None:
        return None

    keys = list(ACTION_KEYS)
    n = len(prompt.options)

    if n > len(keys):
        # Never a subset: showing three of five would put "1. Yes" beside a key
        # that isn't option 1 on screen. Nothing is the honest render.
        log.info("menu has %d options, more than %d keys — no answer keys", n, len(keys))
        return None

    bar: dict = {}
    if n == len(keys):
        # A four-option menu takes the whole bar, **displacing Firefox**
        # (Grant's call, 2026-07-22). Firefox is the most valuable *fixed* key
        # precisely because it never changes — but a prompt on screen is the
        # one moment when answering beats navigating, and a menu we can only
        # half-show is a menu we must not show at all. Escape stays available
        # on the keyboard, which is where your hands already are.
        for slot, (digit, label) in zip(keys, prompt.options):
            bar[slot] = _answer_key(dashboard, digit, label, prompt)
        return bar

    # Otherwise options fill from the left, Escape takes the spare, and the
    # last key stays Firefox.
    for slot, (digit, label) in zip(keys[:3], prompt.options):
        bar[slot] = _answer_key(dashboard, digit, label, prompt)
    spare = keys[:3][n:]
    if spare:
        bar[spare[0]] = _answer_key(dashboard, None, "Esc", prompt)
    return bar


def _answer_key(dashboard: Dashboard, digit, label: str, prompt) -> ActionKey:
    """One option as a key. `digit` of None means send Escape instead."""
    color = palette.ANSWER_CANCEL if digit is None else _answer_style(label)
    # Flooded like the board, but structurally unmistakable against it: centred
    # rather than left-aligned, one big word rather than project-over-task, and
    # ringed by a dark perimeter that nothing else on the deck has. Role has to
    # be legible without reading and without depending on hue — these are the
    # only keys that type into a live session.
    #
    # The caption says which key gets sent, not the option text again — the
    # label already carries that, and repeating it truncated ("2 · Yes, and d…")
    # spends the one line that could tell you what pressing actually does.
    # "Esc", not "cancel": it names the key it sends, which is the one thing
    # about it that is literally true and the thing you can also press yourself.
    text = "Esc" if digit is None else _answer_text(label)
    icon = "" if digit is None else _answer_icon(label)
    # A quiet field with the hue in the icon and the perimeter. The frame is
    # what still says "this key types into a session" — without it these become
    # nearly indistinguishable from the action-bar furniture, which is also dark
    # and centred. Nothing else on the deck is framed, and unlike the focus
    # marker this frame is always present, so it never shifts text by appearing.
    slot = Slot(label=text,
                icon=icon,
                caps=True, align="center",
                bg=palette.ANSWER_BG,
                fg=palette.ANSWER_INK,
                icon_color=color,
                sub_fg=palette.ANSWER_INK_DIM,
                # The digit rides in the corner rather than in a caption line,
                # so the two lines of body text can carry the option itself.
                badge=str(digit) if digit is not None else "",
                frame=color,
                frame_w=palette.ANSWER_FRAME_W,
                key=f"ans:{digit}:{label[:24]}")

    def run(long: bool) -> None:
        session = dashboard.focused_session()
        if session is None:
            log.warning("answer key pressed with nothing focused — ignored")
            return
        # Re-read the screen NOW. The menu we drew may be seconds old; the only
        # acceptable evidence for typing into a session is that the same menu is
        # on screen at this instant.
        live = dashboard.read_prompt_now(session)
        if live is None:
            log.warning("answer %s: no menu on screen any more — ignored", label)
            return
        if live.options != prompt.options:
            log.warning("answer %s: menu changed under us — ignored", label)
            return
        if not dashboard.verify_focus(session):
            log.warning("answer %s: focus moved — ignored", label)
            return
        keys = "\x1b" if digit is None else str(digit)
        log.info("answering %r in %s", label, session.id)
        keystroke(keys)

    return ActionKey(slot, run, name=f"answer:{digit or 'esc'}")


def default_bar(dashboard: Dashboard, surface) -> "callable":
    """The action bar as a *provider* — it depends on what you're looking at.

    Fixed: the far-right key is always Firefox (Grant's call). It is the one
    action that never depends on session state, so it earns the one slot that
    never changes under your finger.

    The other three follow the focused session. Today only the idle/working
    case is built — model, context, cost. A session holding a permission prompt
    or a question will get accept/reject and answer keys, but those synthesize
    keystrokes and are gated behind the Accessibility grant (operations.md);
    until then they show the same info keys, which is honest rather than
    offering a key that cannot fire.
    """
    info = session_info(dashboard)
    firefox = {list(ACTION_KEYS)[3]: jump_to_app("Firefox", "org.mozilla.firefox")}

    def provider() -> dict:
        # Answer keys win when a menu is genuinely on screen; otherwise the
        # info keys. Note the fallback is info, never a disabled accept/reject —
        # a key that looks like it answers a prompt when none is showing is the
        # failure mode this whole design exists to prevent.
        bar = dict(answer_keys(dashboard) or info)
        # Firefox only if the menu didn't claim its slot.
        for slot, key in firefox.items():
            bar.setdefault(slot, key)
        return bar

    return provider
