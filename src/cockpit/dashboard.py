"""The read-only session dashboard — Stage 1.

One key per agent session, colored by state, labeled so you can tell them
apart, and a press jumps to that window. That is the entire scope: it reads and
navigates, and it never sends a keystroke into a session. Every genuinely
dangerous problem (stale state, prompt shape, the wrong-session hazard) is
downstream of *sending*, so this stage sidesteps all of them by construction.

Two things here are not just "draw the sessions", and both exist because this
is an always-on daemon rather than a script:

  - **Polling happens on a background thread.** A Component's render() is
    contracted to be cheap and pure, and the run loop paints every second.
    Enumerating windows is a subprocess + AppleScript bridge that can block for
    seconds if Terminal is busy — doing it inline would freeze the deck, which
    is exactly the failure the non-blocking reconnect work fixed (see
    ../../docs/operations.md). So a poller thread refreshes a snapshot and render()
    only ever formats the snapshot it already has.
  - **Presses act on a background thread too**, for the same reason: focusing a
    window is another osascript round-trip, and the loop must keep ticking
    while it runs.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Optional

from deck import BLANK, Component, Slot, Static, View

from .attention import AttentionTracker
from .sessions import Adapter, Session, label_sessions, order_sessions, summarize

log = logging.getLogger("deck.cockpit.dashboard")

POLL_EVERY_S = 2.0
# Floor between polls when hooks are firing in bursts — a single tool call
# produces several events, and each one asking for its own osascript would be
# wasteful without looking any faster.
MIN_POLL_GAP_S = 0.35
STALE_AFTER_S = 15.0

# The 4+4 split (Grant's call, 2026-07-21): top row is the session board, bottom
# row is a fixed action bar. Actions are always in the same place — muscle memory
# is worth more on a key that does a thing than on a key that names a session,
# because the action's meaning never changes underneath you.
#
# The cost, accepted: four session slots means paging is the normal case at ~9
# live sessions, not the exception. Only the session region pages; the action bar
# is invariant across pages, so paging can never move an action under your finger.
SESSION_KEYS = (0, 1, 2, 3)
ACTION_KEYS = (4, 5, 6, 7)

# bg, accent — urgency reads as color, which is what makes the board glanceable.
#
# Grant's mapping (2026-07-21): green = actively working, blue = needs your
# input, red = blocked on a permission prompt, near-black = quiet. Note that
# **blue and red are currently unreachable**: nothing sets `waiting` or
# `blocked` yet, because window titles cannot tell "Claude is asking you
# something" apart from "sitting at the prompt" (see attention.py). Those two
# colors light up when the hooks channel lands, not before — the styling is
# here so that lands as a wiring change rather than a redesign.
STYLE = {
    "blocked": ("#3A0A0A", "#FF6B6B"),
    "waiting": ("#12263A", "#3FA7D6"),
    "working": ("#0E2A16", "#4CD964"),
    "idle":    ("#141414", "#3A3A3A"),
}
BADGE = {"blocked": "!", "waiting": "?", "working": "●", "idle": ""}

EMPTY = Slot(label="no sessions", sub="waiting…", bg="#141414", fg="#666")

# How far a focused tile is lifted toward white. Enough to read as "lit" from a
# foot away, not so far that the state colour stops being the state colour —
# which one you're in must never compete with which one needs you.
FOCUS_LIFT = 0.30


def lighten(hex_color: str, amount: float = FOCUS_LIFT) -> str:
    """Move a colour toward white by `amount`, preserving its hue."""
    try:
        raw = hex_color.lstrip("#")
        r, g, b = (int(raw[i:i + 2], 16) for i in (0, 2, 4))
    except (ValueError, IndexError):
        return hex_color
    lift = lambda c: min(255, int(c + (255 - c) * amount))
    return "#%02X%02X%02X" % (lift(r), lift(g), lift(b))
STALE_FG = "#7A7A7A"


class SessionTile(Component):
    """One session on one key. Pure render; the press hands off to a callback."""

    def __init__(self, session: Session, label: str, sub: str, on_focus,
                 focused: bool = False):
        self.session = session
        self.label = label
        self.sub = sub
        self.focused = focused
        self._on_focus = on_focus

    def render(self) -> Slot:
        bg, accent = STYLE.get(self.session.state, STYLE["idle"])
        if self.focused:
            # "You are here." Rendered as a lift of the state colour rather
            # than a separate colour or a badge: state already owns colour and
            # the badge, and focus is a different question from urgency — it
            # should be legible without competing with either.
            bg, accent = lighten(bg), lighten(accent, 0.15)
        return Slot(
            label=self.label,
            sub=self.sub,
            bg=bg,
            accent=accent,
            badge=BADGE.get(self.session.state, ""),
            # Slot identity is the session, not the position — so a tile that
            # merely moves pages doesn't read as a different thing.
            # Focus is part of the slot's identity, so a tile that gains or
            # loses focus actually repaints instead of being diffed away.
            key=f"{self.session.id}{':focus' if self.focused else ''}",
        )

    def on_press(self, long: bool) -> bool:
        log.info("focus %s (%s) long=%s", self.session.id, self.label, long)
        self._on_focus(self.session)
        return True


class ActionKey(Component):
    """A fixed key that does something. The action bar's unit.

    The callback runs on its own thread, for the same reason the session poll
    does: an action is usually an osascript or a subprocess, and the run loop
    must keep painting while it happens. A press therefore reports "started",
    never "succeeded" — an action that wants to show its result does so by
    changing state that `slot_fn` reads on a later tick.

    `slot_fn` may be a Slot or a zero-arg callable, so an action key can be
    static ("Firefox") or reflect live state ("top: peregrine").
    """

    def __init__(self, slot_fn, run, enabled=None, name: str = ""):
        self._slot_fn = slot_fn
        self._run = run
        self._enabled = enabled
        self.name = name

    def enabled(self) -> bool:
        return True if self._enabled is None else bool(self._enabled())

    def render(self) -> Slot:
        slot = self._slot_fn() if callable(self._slot_fn) else self._slot_fn
        if self.enabled():
            return slot
        # design.md's rule, applied to actions: never offer a press that can't
        # do anything. A dimmed key reads as "not now" rather than "broken".
        return Slot(label=slot.label, sub=slot.sub, bg="#0C0C0C", fg="#4A4A4A",
                    accent=None, key=slot.key)

    def on_press(self, long: bool) -> bool:
        if self._run is None:
            return False          # an inert key: it displays, it does not act
        if not self.enabled():
            log.debug("action %s pressed while disabled — ignored", self.name)
            return False
        log.info("action %s long=%s", self.name, long)
        threading.Thread(target=self._run_guarded, args=(long,),
                         name=f"action-{self.name}", daemon=True).start()
        return True

    def _run_guarded(self, long: bool) -> None:
        try:
            self._run(long)
        except Exception:
            log.exception("action %s raised", self.name)


class CockpitView(View):
    """The 4+4 grid: a paged session region plus a fixed action bar.

    Not `PagedView`, because that pages the whole grid — here only the session
    region pages while the action keys stay put. That distinction is the layout,
    so it lives here rather than in `deck/`; if a second consumer ever wants a
    paged sub-region it can graduate into the library then.
    """

    def __init__(self, session_keys=SESSION_KEYS, key_count: int = 8):
        super().__init__(None, key_count)
        self._session_keys = tuple(session_keys)
        self._sessions: list[Component] = []
        self._actions: dict[int, Component] = {}
        self.page = 0

    @property
    def per_page(self) -> int:
        return len(self._session_keys)

    @property
    def pages(self) -> int:
        return max(1, (len(self._sessions) + self.per_page - 1) // self.per_page)

    def set_sessions(self, components) -> None:
        self._sessions = list(components)
        self.page %= self.pages

    def set_actions(self, actions) -> None:
        """A dict of {index: Component}, or a callable returning one.

        A callable lets the bar depend on what is focused — the keys under an
        idle session are not the keys under one holding a permission prompt.
        """
        self._actions = actions

    def components(self) -> dict[int, Component]:
        self.page %= self.pages
        start = self.page * self.per_page
        chunk = self._sessions[start:start + self.per_page]
        actions = self._actions() if callable(self._actions) else self._actions
        out = dict(actions or {})
        for slot, c in zip(self._session_keys, chunk):
            out[slot] = c
        return out

    def on_touch(self, side: str) -> bool:
        if self.pages <= 1:
            return False
        self.page = (self.page + (1 if side == "right" else -1)) % self.pages
        return True


class SessionPoller:
    """Keeps a fresh snapshot of an adapter's sessions, off the render loop.

    `snapshot()` never blocks and never raises — worst case it returns the last
    good list, which is why `age()` exists: a stale board should say so rather
    than quietly show yesterday's truth.
    """

    def __init__(self, adapter: Adapter, interval: float = POLL_EVERY_S,
                 tracker: Optional[AttentionTracker] = None,
                 prompt_reader=None):
        self._adapter = adapter
        self._interval = interval
        # Reads the on-screen menu for a session. Injected so it can be faked in
        # tests, and so the AX dependency stays optional — without it the board
        # simply never offers answer keys.
        self._prompt_reader = prompt_reader
        self.tracker = tracker or AttentionTracker()
        self._lock = threading.Lock()
        self._sessions: list[Session] = []
        self._focused_handle: Optional[str] = None
        self._prompt = None
        self._updated_at: Optional[float] = None
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._last_poll = 0.0
        self._thread: Optional[threading.Thread] = None

    def start(self) -> "SessionPoller":
        self.poll_once()          # so the first paint has real content
        self._thread = threading.Thread(target=self._loop, name="session-poll",
                                        daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()

    def request_poll(self) -> None:
        """Ask for a refresh now — called from the listener when a hook fires.

        Only wakes the poll thread; the work never happens on the HTTP thread,
        which must return to Claude Code promptly. Without this the channels
        were instant but the *board* still waited out the poll interval, so a
        tile could sit stale for two seconds after the event that changed it —
        very visible when you are the one pressing the key.
        """
        self._wake.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            # Wake on either the interval or a hook. `wait` returning True
            # means something asked for a poll.
            self._wake.wait(self._interval)
            if self._stop.is_set():
                break
            self._wake.clear()
            # A burst of hooks (a tool call fires several) must not turn into a
            # burst of osascript. One poll per MIN_GAP is plenty to look instant.
            since = time.monotonic() - self._last_poll
            if since < MIN_POLL_GAP_S:
                self._stop.wait(MIN_POLL_GAP_S - since)
                if self._stop.is_set():
                    break
            self.poll_once()

    def poll_once(self) -> None:
        try:
            found = self._adapter.sessions()
            # Which one you're actually looking at. Best-effort by contract —
            # it needs a TCC grant the session list doesn't, so it degrades to
            # None rather than costing us the board.
            focused = None
            getter = getattr(self._adapter, "focused", None)
            if callable(getter):
                focused = getter(found)
            found = self.tracker.update(found, focused)
            # Read the on-screen menu only for the session you're looking at,
            # and only while it is actually holding something. Reading every
            # session every poll would be wasted work, and reading an unfocused
            # one is pointless: you can only answer the window in front of you.
            prompt = None
            if focused is not None and self._prompt_reader is not None:
                target = next((x for x in found if x.handle == focused), None)
                if target is not None and target.state in ("blocked", "waiting"):
                    prompt = self._prompt_reader(target)
        except Exception:
            # An adapter is third-party-ish code from this thread's point of
            # view; one bad poll must not kill the poller for the day.
            log.exception("adapter %s raised while polling", self._adapter.name)
            return
        with self._lock:
            self._sessions = list(found)
            self._focused_handle = focused
            self._prompt = prompt
            self._updated_at = time.monotonic()
        self._last_poll = time.monotonic()

    def snapshot(self) -> list[Session]:
        with self._lock:
            return list(self._sessions)

    def focused_handle(self) -> Optional[str]:
        with self._lock:
            return self._focused_handle

    def prompt(self):
        """The menu on screen in the focused session, if any."""
        with self._lock:
            return self._prompt

    def age(self) -> Optional[float]:
        """Seconds since the last successful poll, or None if never."""
        with self._lock:
            if self._updated_at is None:
                return None
            return time.monotonic() - self._updated_at


class Dashboard:
    """Binds a poller to a PagedView, rebuilding tiles only when they change."""

    def __init__(self, adapter: Adapter, key_count: int = 8,
                 interval: float = POLL_EVERY_S, session_keys=SESSION_KEYS,
                 prompt_reader=None):
        self._adapter = adapter
        self._prompt_reader = prompt_reader
        self.poller = SessionPoller(adapter, interval, prompt_reader=prompt_reader)
        self.view = CockpitView(session_keys, key_count=key_count)
        self._signature: Optional[tuple] = None
        self._sessions: list[Session] = []

    # --- lifecycle ---------------------------------------------------------

    def set_actions(self, actions: dict) -> None:
        """Mount the action bar: {key index: Component}."""
        self.view.set_actions(actions)

    def start(self) -> "Dashboard":
        self.poller.start()
        self.refresh()
        return self

    def stop(self) -> None:
        self.poller.stop()

    # --- the per-tick work -------------------------------------------------

    def refresh(self) -> bool:
        """Rebuild the tiles if the snapshot changed. Returns True if it did.

        The signature covers everything a tile displays, so an unchanged board
        costs one tuple comparison per tick and no allocation. (Even a needless
        rebuild would be cheap — Surface diffs by Slot value — but the loop runs
        forever, so cheap beats free-enough.)
        """
        found = order_sessions(self.poller.snapshot())
        sig = (self.poller.focused_handle(),
               tuple((s.id, s.cwd, s.task, s.state) for s in found))
        if sig == self._signature:
            return False
        self._signature = sig
        self._sessions = found

        labels = label_sessions(found)
        focused = self.poller.focused_handle()
        tiles = [SessionTile(s, *labels[s.id], self.focus,
                             focused=(s.handle == focused and focused is not None))
                 for s in found]
        self.view.set_sessions(tiles or [Static(EMPTY)])
        log.debug("dashboard rebuilt — %d sessions, %d pages",
                  len(tiles), self.view.pages)
        return True

    def focused_session(self) -> Optional[Session]:
        """The session you are looking at right now, or None.

        None is a real answer, not a failure: you may be in Firefox, or in a
        plain shell. The action bar dims rather than guessing, because every
        context-sensitive action it offers acts on *this* session.
        """
        handle = self.poller.focused_handle()
        if handle is None:
            return None
        for s in self._sessions:
            if s.handle == handle:
                return s
        return None

    def focused_prompt(self):
        """The live menu in the focused session — the basis for answer keys."""
        return self.poller.prompt()

    def read_prompt_now(self, session: Session):
        """Re-read this session's screen synchronously. The press-time guard.

        Deliberately not the cached poll value: the cache can be two seconds
        old, and two seconds is long enough for a menu to be answered at the
        keyboard and replaced by something else.
        """
        if self._prompt_reader is None:
            return None
        try:
            return self._prompt_reader(session)
        except Exception:
            log.exception("prompt re-read failed for %s", session.id)
            return None

    def verify_focus(self, session: Session) -> bool:
        """Is that session still the window in front, right now?

        Re-asks the adapter rather than trusting the last poll. Combined with
        the screen re-read, this is the full guard: the right window, showing
        the same menu, at the moment of the keystroke.
        """
        fresh = getattr(self._adapter, "front_window_now", None)
        try:
            if callable(fresh):
                return fresh() == session.handle
            getter = getattr(self._adapter, "focused", None)
            return callable(getter) and getter(self.poller.snapshot()) == session.handle
        except Exception:
            log.exception("focus verification failed")
            return False

    def top_session(self) -> Optional[Session]:
        """The most urgent session — whatever currently sits in the first slot.

        Today that means "the one working, else the lowest-numbered window";
        once Stage 2's hooks populate `blocked`, it means "the one that needs
        you" with no change here, because urgency is baked into the ordering.
        """
        return self._sessions[0] if self._sessions else None

    def focus(self, session: Session) -> None:
        """Navigate, off the loop thread — osascript can block for seconds.

        This is the entry point for a *key press*, which arrives on the run
        loop. Code that is already on a worker thread should call `focus_now`
        rather than spawning a second one.
        """
        threading.Thread(
            target=self.focus_now, args=(session,),
            name=f"focus-{session.id}", daemon=True).start()

    def focus_now(self, session: Session) -> None:
        # Reaching a session from the deck counts as being active in it, so it
        # keeps its place at the top of the board rather than sorting away
        # under you the moment it stops spinning.
        self.poller.tracker.mark_seen(session.id)
        try:
            ok = self._adapter.focus(session)
        except Exception:
            log.exception("focus %s raised", session.id)
            return
        if not ok:
            log.warning("focus %s did not take", session.id)
            return
        # The window we just raised may have changed state; don't wait out the
        # poll interval to show it.
        self.poller.poll_once()

    # --- presentation ------------------------------------------------------

    @property
    def sessions(self) -> list[Session]:
        return list(self._sessions)

    @property
    def pages(self) -> int:
        return self.view.pages

    def info(self) -> tuple:
        """(text, sub) for the info bar — the aggregate the keys can't show."""
        counts = summarize(self._sessions)
        n = len(self._sessions)
        text = "no sessions" if n == 0 else f"{n} session{'' if n == 1 else 's'}"

        bits = []
        if counts["blocked"]:
            bits.append(f"{counts['blocked']} blocked")
        if counts["waiting"]:
            bits.append(f"{counts['waiting']} waiting")
        bits.append(f"{counts['working']} working")
        bits.append(f"{counts['idle']} idle")
        if self.pages > 1:
            bits.append(f"pg {self.view.page + 1}/{self.pages}")

        age = self.poller.age()
        fg = "#FFFFFF"
        if age is None or age > STALE_AFTER_S:
            # Say so rather than presenting a stale board as current.
            bits.append("stale")
            fg = STALE_FG
        return (text, "  ·  ".join(bits), "#000000", fg)


__all__ = ["Dashboard", "SessionPoller", "SessionTile", "STYLE", "EMPTY", "BLANK"]
