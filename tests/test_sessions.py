"""Stage 1 dashboard tests — parsing, ordering, labeling, tiles, polling.

Headless. Everything with a device or an osascript in it is stubbed by a fake
adapter, so what's under test is the logic that decides *what the deck says*:
which windows count as sessions, what order they sit in, and what each tile is
called. Those are the decisions worth pinning; the pixels are proven live.

The corpus below is the real observed board (2026-07-21, nine sessions + one
plain shell) — including the three-way `Projects` collision that Stage 1 exists
to solve.
"""
import os
import sys
from dataclasses import replace
import threading
import time

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from deck import BLANK, Slot

from deck.anim import STEPS
from deck.color import luminance, parse
from cockpit import palette
from cockpit import actions as actions_mod
from cockpit.attention import AttentionTracker
from cockpit.actions import default_bar
from cockpit.claude_code import parse_listing, parse_title
from cockpit.dashboard import (
    BREATHE_LO,
    EMPTY,
    STYLE,
    ActionKey,
    CockpitView,
    Dashboard,
    SessionPoller,
    SessionTile,
)
from cockpit.sessions import (
    Session,
    label_sessions,
    order_sessions,
    summarize,
    task_phrase,
)

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


# The live board, verbatim.
LIVE = """53025\t/dev/ttys025\tstreamdeck — ⠐ Build Stream Deck cockpit Stage 1 dashboard — caffeinate ◂ claude — 133×45
35112\t/dev/ttys112\tProjects — ✳ Scope v2 corpus migration to v3 charter — claude — 179×47
50245\t/dev/ttys245\tperegrine — ✳ Implement Peregrine model IR with concrete type definitions — claude — 151×48
111\t/dev/ttys111\tProjects — ✳ Personal OS reorganization and dossier system — claude — 181×48
6299\t/dev/ttys299\tdocland — ✳ Explore and organize docland project — claude — 185×48
31552\t/dev/ttys552\tprovenance — ✳ Transcribe 1804 Wake County deed across two pages — claude — 179×47
49378\t/dev/ttys378\tProjects — ✳ Locate and recover abandoned estate property in NC — claude — 133×45
52844\t/dev/ttys844\tstreamdeck — -bash — 133×45
"""


def mk(sid, cwd, task="", state="idle", handle=None):
    return Session(id=sid, agent="claude", cwd=cwd, task=task, state=state,
                   handle=handle if handle is not None else sid.split(":")[-1])


print("\n[parse_title]")

s = parse_title(53025, "streamdeck — ⠐ Build Stream Deck cockpit Stage 1 dashboard "
                       "— caffeinate ◂ claude — 133×45")
check("parses a working session", s is not None)
check("cwd", s and s.cwd == "streamdeck", s and s.cwd)
check("task drops the state glyph", s and s.task == "Build Stream Deck cockpit Stage 1 dashboard",
      s and s.task)
check("spinner glyph -> working", s and s.state == "working", s and s.state)
check("id is adapter-scoped", s and s.id == "claude:53025", s and s.id)
check("handle is the window id", s and s.handle == "53025")

s2 = parse_title(111, "Projects — ✳ Personal OS reorganization and dossier system "
                      "— claude — 181×48")
check("idle glyph -> idle", s2 and s2.state == "idle", s2 and s2.state)

check("a plain shell is not a session",
      parse_title(52844, "streamdeck — -bash — 133×45") is None)
check("a fresh session with no task parses",
      parse_title(7, "docland — claude — 100×30") is not None)
check("…and its task is empty, not garbage",
      parse_title(7, "docland — claude — 100×30").task == "")
check("proc field matched as a word, not a substring",
      parse_title(8, "notes — ✳ read the claudette memo — -bash — 90×30") is None)
check("a task mentioning claude still parses as its own field",
      parse_title(9, "notes — ✳ upgrade claude config — claude — 90×30").task
      == "upgrade claude config")
check("titleless junk -> None", parse_title(10, "no-dashes-here") is None)

print("\n[parse_listing]")

found = parse_listing(LIVE)
check("skips the non-agent window", len(found) == 7, f"{len(found)} of 8 lines")
check("no `-bash` window survived",
      all(x.handle != "52844" for x in found))
check("one working, six idle",
      sum(1 for x in found if x.state == "working") == 1
      and sum(1 for x in found if x.state == "idle") == 6)
check("non-numeric window id skipped",
      parse_listing("abc\t/dev/ttys1\tx — ✳ t — claude — 1×1\n") == [])
check("line without the tty/id columns skipped",
      parse_listing("just a title\n") == [])
check("empty input -> []", parse_listing("") == [])

print("\n[order_sessions] needs-you floats to top, then recency")

board = [mk("claude:300", "c", state="idle"), mk("claude:100", "a", state="idle"),
         mk("claude:200", "b", state="working"), mk("claude:50", "d", state="blocked"),
         mk("claude:400", "e", state="waiting")]
o = order_sessions(board)
check("blocked then waiting lead the board",
      [x.state for x in o][:2] == ["blocked", "waiting"], str([x.state for x in o]))
check("with no recency, the rest fall back to NEWEST window first",
      [x.handle for x in o][2:] == ["300", "200", "100"], str([x.handle for x in o]))
check("ordering is stable across repeated calls", order_sessions(o) == o)
check("reordering the input doesn't reorder the output",
      order_sessions(list(reversed(board))) == o)
# The live bug: ascending window id meant "oldest wins", so a session untouched
# for a week outranked the one in active use every time recency was unavailable.
stale, fresh = mk("claude:6299", "docland"), mk("claude:50245", "peregrine")
check("an old window does not outrank a newer one on a cold start",
      [x.cwd for x in order_sessions([stale, fresh])] == ["peregrine", "docland"],
      str([x.cwd for x in order_sessions([stale, fresh])]))

check("non-numeric handles don't explode",
      len(order_sessions([mk("x:1", "a", handle="win-abc"), mk("claude:2", "b")])) == 2)

# The regression Grant hit live: the session he was working in vanished to page
# two the instant it stopped spinning.
recent = replace(mk("claude:999", "mine", state="idle"), last_active=5000.0)
others = [mk("claude:100", "a", state="idle"), mk("claude:200", "b", state="idle")]
check("a recently-active idle session outranks never-active ones",
      order_sessions(others + [recent])[0].id == "claude:999")
busy = replace(mk("claude:300", "bg", state="working"), last_active=4000.0)
check("…and outranks a working session it was more recently active than",
      order_sessions([busy, recent])[0].id == "claude:999",
      str([x.id for x in order_sessions([busy, recent])]))
needy = mk("claude:400", "urgent", state="blocked")
check("…but never outranks one that actually needs you",
      order_sessions([recent, needy])[0].id == "claude:400")

print("\n[AttentionTracker] recency only — it never invents 'needs input'")

clock = [1000.0]
t = AttentionTracker(clock=lambda: clock[0], state_path=None)
raw = [mk("claude:1", "a", state="working"), mk("claude:2", "b", state="idle")]
got = {s.id: s for s in t.update(raw)}
check("a working session is stamped active", got["claude:1"].last_active == 1000.0)
check("an untouched session is never stamped", got["claude:2"].last_active == 0.0)
check("states pass through untouched",
      [got["claude:1"].state, got["claude:2"].state] == ["working", "idle"])

clock[0] = 1010.0
raw2 = [mk("claude:1", "a", state="idle"), mk("claude:2", "b", state="idle")]
got2 = {s.id: s for s in t.update(raw2)}
check("a session that stops working keeps its recency",
      got2["claude:1"].last_active == 1000.0)
check("finishing a turn does NOT invent a needs-input state",
      got2["claude:1"].state == "idle", got2["claude:1"].state)

clock[0] = 1020.0
got3 = {s.id: s for s in t.update(raw2, focused_handle="2")}
check("the focused window counts as active", got3["claude:2"].last_active == 1020.0)

clock[0] = 1030.0
t.mark_seen("claude:1")
got4 = {s.id: s for s in t.update(raw2)}
check("reaching a session from the deck counts as active",
      got4["claude:1"].last_active == 1030.0)

check("a real blocked state survives enrichment",
      t.update([mk("claude:3", "c", state="blocked")])[0].state == "blocked")
check("repeating a snapshot is idempotent",
      t.update(raw2)[0].last_active == t.update(raw2)[0].last_active)

t.update([mk("claude:1", "a", state="idle")])          # claude:2 closed
check("a session missing from one snapshot keeps its recency",
      t.last_active("claude:2") == 1020.0, str(t.last_active("claude:2")))

print("\n[task_phrase]")

check("drops the leading verb and stopwords",
      task_phrase("Scope v2 corpus migration to v3 charter") == "corpus migration",
      task_phrase("Scope v2 corpus migration to v3 charter"))
check("keeps uppercase acronyms",
      task_phrase("Personal OS reorganization and dossier system") == "Personal OS",
      task_phrase("Personal OS reorganization and dossier system"))
check("respects the char budget",
      len(task_phrase("Locate and recover abandoned estate property in NC")) <= 16,
      task_phrase("Locate and recover abandoned estate property in NC"))
check("never splits a word",
      " " not in task_phrase("Transcribe supercalifragilistic deed"),
      task_phrase("Transcribe supercalifragilistic deed"))
check("empty task -> empty phrase", task_phrase("") == "")
check("all-stopword task -> empty phrase", task_phrase("Fix the and to") == "")
check("a single long word still comes through",
      task_phrase("Implement peregrine") == "peregrine",
      task_phrase("Implement peregrine"))

print("\n[label_sessions] cwd where unique, task where it collides")

labels = label_sessions(found)
by_handle = {x.handle: labels[x.id] for x in found}
check("unique cwd keeps its name",
      by_handle["50245"][0] == "peregrine", str(by_handle["50245"]))
check("…with the task head as its subtitle",
      by_handle["50245"][1] == "Peregrine model", str(by_handle["50245"]))
check("unique cwd: docland", by_handle["6299"][0] == "docland")
check("a collided cwd STILL leads with the project name",
      by_handle["35112"][0] == "Projects" and by_handle["111"][0] == "Projects",
      str([by_handle["35112"], by_handle["111"]]))
check("…the task head moves to the subtitle instead",
      by_handle["35112"][1] == "corpus migration", str(by_handle["35112"]))
check("…and the second collision", by_handle["111"][1] == "Personal OS",
      str(by_handle["111"]))
check("all three `Projects` tiles still read differently",
      len({by_handle[h][1] for h in ("35112", "111", "49378")}) == 3,
      str([by_handle[h][1] for h in ("35112", "111", "49378")]))
check("the big line NEVER inverts — it is a project name on every tile",
      all(labels[x.id][0] == x.cwd for x in found))

collide_no_task = label_sessions([mk("claude:1", "Projects", ""),
                                  mk("claude:2", "Projects", "")])
check("a collision with no task text still names the project",
      collide_no_task["claude:1"] == ("Projects", ""),
      str(collide_no_task["claude:1"]))

print("\n[summarize]")

c = summarize(found)
check("counts every state key", set(c) == {"blocked", "waiting", "working", "idle"})
check("counts are right", c["working"] == 1 and c["idle"] == 6, str(c))
check("empty board is all zeros", set(summarize([]).values()) == {0})


print("\n[SessionTile]")

focused = []
tile = SessionTile(mk("claude:1", "peregrine", "do a thing", "working"),
                   "peregrine", "do a thing", focused.append)
slot = tile.render()
check("renders label and sub", slot.label == "peregrine" and slot.sub == "do a thing")
check("state floods the whole tile", slot.bg == STYLE["working"][0])
check("working is blue, never green — green belongs to the answer bar",
      STYLE["working"][1] == palette.ADVISORY)
check("a calm state carries no badge", slot.badge == "")
check("a calm tile spends its caption on the task, not the state",
      slot.sub == "do a thing", slot.sub)
check("a calm state does not animate", tile.animating() is False)
check("slot identity leads with the session, not the key position",
      slot.key.startswith("claude:1"))
check("press hands the session to the callback",
      tile.on_press(False) is True and focused and focused[0].id == "claude:1")

blocked_slot = SessionTile(mk("claude:2", "x", "y", "blocked"), "x", "y",
                           focused.append).render()
check("blocked is visually distinct from working", blocked_slot.bg != slot.bg)
check("blocked is the warning hue", STYLE["blocked"][1] == palette.WARNING)
# Temperature, not luminance: red is inherently low-luminance, so a vivid red
# scores at or below a mid blue and the obvious assertion fails for the wrong
# reason. Warm-vs-cool is the property the board actually trades on.
_r, _g, _b = parse(blocked_slot.bg)
_wr, _wg, _wb = parse(slot.bg)
check("…and is the warm one, where working is cool",
      (_r - _b) > 0 > (_wr - _wb), f"blocked {_r - _b}, working {_wr - _wb}")
check("…and carries a badge rather than spending the caption on its state",
      blocked_slot.badge == "!" and blocked_slot.sub == "y",
      f"badge={blocked_slot.badge!r} sub={blocked_slot.sub!r}")
# Motion is opt-in per state, NOT implied by needs-you. blocked is the loudest
# thing on the deck and deliberately does not move (Grant, on living with it);
# waiting is the quieter warm state and still breathes.
_blocked = SessionTile(mk("claude:2", "x", "y", "blocked"), "x", "y", focused.append)
check("blocked does NOT animate — loud and still beats loud and moving",
      _blocked.animating() is False)
check("…so its field sits at full brightness", _blocked.render().pulse == 1.0)
_waiting = SessionTile(mk("claude:3", "x", "y", "waiting"), "x", "y", focused.append)
check("waiting still breathes", _waiting.animating() is True)
# Sampled over time, not once: a breathe legitimately passes through 1.0 at the
# top of its cycle, so a single render can catch it at full brightness. The
# property that matters is that the value MOVES.
_seen = set()
_t0 = time.monotonic()
while time.monotonic() - _t0 < 1.2:
    _seen.add(_waiting.render().pulse)
    time.sleep(0.01)
check("…which shows up as a field brightness that varies over time",
      len(_seen) > 1, f"{len(_seen)} distinct in 1.2s")
# One quantization step of slack: values are snapped onto STEPS buckets, and
# the bucket nearest the floor can land just under it.
_tol = 1.0 / (STEPS - 1)
check("…within the configured floor and ceiling",
      all(BREATHE_LO - _tol <= v <= 1.0 + 1e-9 for v in _seen),
      f"{min(_seen):.3f}..{max(_seen):.3f} (floor {BREATHE_LO})")
check("the pulse capability is intact, just switched off for blocked",
      palette.STATE["waiting"].breathes and not palette.STATE["blocked"].breathes)
check("cool states never move",
      not any(palette.STATE[n].breathes or palette.STATE[n].flashes
              for n in ("working", "idle")))

# The collision that started the redesign: no hue may mean two things.
_hues = {name: st.color for name, st in palette.STATE.items()}
check("every state hue is distinct", len(set(_hues.values())) == len(_hues))
check("no session state uses the answer bar's green",
      palette.GO not in _hues.values())
check("no answer colour reuses the warning red",
      palette.WARNING not in (palette.ANSWER_AFFIRM, palette.ANSWER_GRANT,
                              palette.ANSWER_DECLINE))
check("warm states are exactly the needs-you states",
      {n for n, st in palette.STATE.items() if st.needs_you}
      == {"blocked", "waiting"})


print("\n[Dashboard] with a fake adapter — no osascript, no device")


class FakeAdapter:
    """Stands in for a real CLI adapter: scriptable, and can misbehave."""

    name = "fake"

    def __init__(self, sessions=None):
        self._sessions = list(sessions or [])
        self.focus_calls = []
        self.raise_on_poll = False
        self.focus_ok = True
        self.calls = 0

    def set(self, sessions):
        self._sessions = list(sessions)

    def sessions(self):
        self.calls += 1
        if self.raise_on_poll:
            raise RuntimeError("adapter exploded")
        return list(self._sessions)

    def focus(self, session):
        self.focus_calls.append(session.id)
        return self.focus_ok


fake = FakeAdapter(parse_listing(LIVE))
d = Dashboard(fake, key_count=8, interval=99)   # long interval: we poll by hand
d.poller.poll_once()
d.refresh()

check("session region holds 4 keys", len(d.view.components()) == 4,
      str(len(d.view.components())))
check("sessions occupy the top row only", set(d.view.components()) == {0, 1, 2, 3})
check("seven sessions need two pages", d.pages == 2, f"pages={d.pages}")
check("info bar names the count", d.info()[0] == "7 sessions", d.info()[0])
check("info bar tallies state as coloured chips, not prose",
      (palette.ADVISORY, 1) in d.info()[4], str(d.info()[4]))
check("…and the headline is a plain count, never a red banner",
      d.info()[0].endswith("session") or d.info()[0].endswith("sessions"),
      d.info()[0])
check("…with no alarm colour on it",
      d.info()[3] != palette.WARNING, d.info()[3])

check("refresh is a no-op when nothing changed", d.refresh() is False)
fake.set(parse_listing(LIVE)[:3])
d.poller.poll_once()
check("refresh rebuilds when the board changes", d.refresh() is True)
check("…and the view shrank", len(d.view.components()) == 3)

# state change alone must repaint — this is the whole point of the dashboard
one = mk("claude:1", "solo", "a task", "idle")
fake.set([one])
d.poller.poll_once()
d.refresh()
check("single session, single page", d.pages == 1 and len(d.view.components()) == 1)
before = d.view.components()[0].render()
fake.set([mk("claude:1", "solo", "a task", "blocked")])
d.poller.poll_once()
check("a state flip triggers a rebuild", d.refresh() is True)
after = d.view.components()[0].render()
check("…and the tile actually changed color", before.bg != after.bg)

fake.set([])
d.poller.poll_once()
d.refresh()
check("empty board renders the placeholder, not a blank deck",
      d.view.components()[0].render() == EMPTY)
check("empty board info bar says so", d.info()[0] == "no sessions")

fake.set([mk(f"claude:{i}", f"w{i}", "t", "idle") for i in range(20)])
d.poller.poll_once()
d.refresh()
check("overflow pages over the session region, not the grid",
      d.pages == 5, f"pages={d.pages}")
check("a page shows at most 4 tiles", len(d.view.components()) == 4)
d.view.on_touch("right")
check("touch pages forward", d.view.page == 1)
check("page shows in the info bar", d.info()[5] == (1, 5), str(d.info()[5]))

print("\n[Dashboard] press routing")

fake.set([one])
d.poller.poll_once()
d.refresh()
d.view.press(0, False)
for _ in range(100):                       # focus runs on its own thread
    if fake.focus_calls:
        break
    time.sleep(0.01)
check("a press reaches the adapter", fake.focus_calls == ["claude:1"], str(fake.focus_calls))
check("focus does not block the loop thread",
      threading.current_thread() is threading.main_thread())

fake.focus_ok = False
fake.focus_calls.clear()
d.view.press(0, False)
for _ in range(100):
    if fake.focus_calls:
        break
    time.sleep(0.01)
check("a failed focus is logged, not raised", fake.focus_calls == ["claude:1"])

print("\n[SessionPoller] fault isolation and staleness")

bad = FakeAdapter([one])
p = SessionPoller(bad, interval=99)
p.check_age = None
check("age is None before the first poll", p.age() is None)
p.poll_once()
check("snapshot after a good poll", len(p.snapshot()) == 1)
check("age is a small number after a poll", 0 <= p.age() < 5)

bad.raise_on_poll = True
p.poll_once()
check("a raising adapter does not kill the poller", len(p.snapshot()) == 1)
bad.raise_on_poll = False

d2 = Dashboard(bad, interval=99)
check("stale board says so in the info bar", "stale" in d2.info()[1], d2.info()[1])

# Note the MIN_POLL_GAP_S floor: bursts of hooks must not become bursts of
# osascript, so the loop paces itself regardless of how often it is woken.
from cockpit.dashboard import MIN_POLL_GAP_S

p2 = SessionPoller(FakeAdapter([one]), interval=0.02).start()
time.sleep(MIN_POLL_GAP_S * 3)
p2.stop()
polls = p2._adapter.calls
check("the poller thread actually polls", polls >= 2, f"{polls} polls")

# A hook firing must move the board now, not at the next interval.
p3 = SessionPoller(FakeAdapter([one]), interval=30).start()
before = p3._adapter.calls
p3.request_poll()
for _ in range(200):
    if p3._adapter.calls > before:
        break
    time.sleep(0.01)
check("request_poll() wakes the poller without waiting out the interval",
      p3._adapter.calls > before, f"{p3._adapter.calls} vs {before}")
burst = p3._adapter.calls
for _ in range(20):
    p3.request_poll()
time.sleep(MIN_POLL_GAP_S * 1.5)
check("…and a burst of hooks does not become a burst of polls",
      p3._adapter.calls - burst <= 3, f"{p3._adapter.calls - burst} polls for 20 hooks")
p3.stop()
time.sleep(0.08)
check("stop() ends the thread", p2._adapter.calls == polls
      or p2._adapter.calls <= polls + 1, f"{p2._adapter.calls} after stop")

print("\n[CockpitView] the 4+4 grid")

fake.set([mk(f"claude:{i}", f"w{i}", "t", "idle") for i in range(6)])
d.poller.poll_once()
d.refresh()
bar = {4: ActionKey(Slot(label="A"), lambda long: None, name="a"),
       5: ActionKey(Slot(label="B"), lambda long: None, name="b"),
       6: ActionKey(Slot(label="C"), lambda long: None, name="c"),
       7: ActionKey(Slot(label="D"), lambda long: None, name="d")}
d.set_actions(bar)
comps = d.view.components()
check("all 8 keys are occupied", set(comps) == {0, 1, 2, 3, 4, 5, 6, 7})
check("action bar is the bottom row",
      [comps[i].render().label for i in (4, 5, 6, 7)] == ["A", "B", "C", "D"])

page0 = [comps[i].render().key for i in (0, 1, 2, 3)]
d.view.on_touch("right")
comps2 = d.view.components()
page1 = [comps2[i].render().key for i in sorted(set(comps2) & {0, 1, 2, 3})]
check("paging changes the session region", page0 != page1)
check("a partial last page leaves its spare session slots empty",
      set(comps2) & {0, 1, 2, 3} == {0, 1}, str(sorted(set(comps2) & {0, 1, 2, 3})))
check("…which View.slots() renders as blank, not stale",
      d.view.slots()[3] == BLANK)
check("paging leaves the action bar alone",
      [comps2[i].render().label for i in (4, 5, 6, 7)] == ["A", "B", "C", "D"])
check("actions survive a session rebuild",
      (fake.set([mk("claude:1", "solo", "t")]), d.poller.poll_once(),
       d.refresh(), set(d.view.components()) >= {4, 5, 6, 7})[-1])

d.view.page = 0
fake.set([])
d.poller.poll_once()
d.refresh()
check("empty board still shows the action bar",
      set(d.view.components()) == {0, 4, 5, 6, 7}, str(sorted(d.view.components())))
check("…with the placeholder in the first session slot",
      d.view.components()[0].render() == EMPTY)


print("\n[ActionKey]")

ran = []
a = ActionKey(Slot(label="go", bg="#101820", accent="#4A6B7C"),
              lambda long: ran.append(long), name="go")
check("renders its slot when enabled", a.render().label == "go")
check("press returns handled", a.on_press(False) is True)
for _ in range(100):
    if ran:
        break
    time.sleep(0.01)
check("callback ran off the loop thread", ran == [False], str(ran))
a.on_press(True)
for _ in range(100):
    if len(ran) > 1:
        break
    time.sleep(0.01)
check("long press is passed through", ran[-1] is True)

off = ActionKey(Slot(label="go", bg="#101820", accent="#4A6B7C"),
                lambda long: ran.append("nope"), enabled=lambda: False, name="off")
check("disabled key dims rather than disappearing", off.render().label == "go")
check("…and drops its accent", off.render().accent is None)
check("disabled press does nothing", off.on_press(False) is False)

boom = ActionKey(Slot(label="x"), lambda long: 1 / 0, name="boom")
check("a raising action does not escape the press", boom.on_press(False) is True)
time.sleep(0.05)


print("\n[actions] the bar itself")


class FakeSurface:
    """Just the brightness contract ActionKeys touch."""

    def __init__(self, level=70):
        self._b = level

    @property
    def brightness(self):
        return self._b

    def set_brightness(self, level):
        self._b = max(0, min(100, int(level)))
        return self._b


fake.set(parse_listing(LIVE))
d.poller.poll_once()
d.refresh()
surf = FakeSurface()
# default_bar is now a *provider*: the bar depends on the focused session.
mounted = default_bar(d, surf)()
check("default bar fills exactly the action keys", set(mounted) == {4, 5, 6, 7})

top_key = actions_mod.jump_to_top(d)
check("top key names its target", top_key.render().label == d.top_session().cwd,
      top_key.render().label)
fake.focus_calls.clear()
top_key.on_press(False)
for _ in range(200):
    if fake.focus_calls:
        break
    time.sleep(0.01)
check("top key focuses the most urgent session",
      fake.focus_calls == [d.top_session().id], str(fake.focus_calls))

fake.set([])
d.poller.poll_once()
d.refresh()
check("top key dims when there are no sessions", top_key.enabled() is False)
check("…and shows an em dash", top_key.render().label == "—")
fake.focus_calls.clear()
top_key.on_press(False)
time.sleep(0.05)
check("…and pressing it does nothing", fake.focus_calls == [])

b = actions_mod.brightness(surf)
check("brightness key shows the level", b.render().label == "70%", b.render().label)
b.on_press(False)
time.sleep(0.05)
check("press steps down the cycle", surf.brightness == 30, str(surf.brightness))
b.on_press(False)
time.sleep(0.05)
check("…then to dark", surf.brightness == 0)
b.on_press(False)
time.sleep(0.05)
check("…then wraps to full", surf.brightness == 70)
surf.set_brightness(45)
b.on_press(False)
time.sleep(0.05)
check("an off-cycle level advances from the nearest step",
      surf.brightness == 0, str(surf.brightness))
b.on_press(True)
time.sleep(0.05)
check("long press jumps back to full", surf.brightness == 70)

r = actions_mod.refresh(d)
before_calls = fake.calls
r.on_press(False)
for _ in range(200):
    if fake.calls > before_calls:
        break
    time.sleep(0.01)
check("refresh key forces a poll", fake.calls > before_calls)

app = actions_mod.jump_to_app("Firefox", "org.mozilla.firefox")
check("app key renders its name", app.render().label == "Firefox")
check("app key is always enabled", app.enabled() is True)

print("\n[focus indicator] which tile am I actually in")


class FocusAdapter(FakeAdapter):
    """A fake that also reports which session has focus."""

    def __init__(self, sessions=None, focused_handle=None):
        super().__init__(sessions)
        self.focused_handle = focused_handle

    def focused(self, sessions):
        return self.focused_handle


from deck.color import lighten

check("lighten moves toward white", lighten("#000000", 0.5) == "#808080",
      lighten("#000000", 0.5))
check("lighten preserves hue direction",
      lighten("#0E2A16") != "#0E2A16" and lighten("#0E2A16").startswith("#5"))
check("lighten survives a malformed colour", lighten("nope") == "nope")

ff = FocusAdapter([mk("claude:10", "here", "t", "idle"),
                   mk("claude:20", "there", "t", "idle")], focused_handle="10")
fdd = Dashboard(ff, interval=99)
fdd.poller.poll_once(); fdd.refresh()
tiles = {c.session.handle: c for c in fdd.view.components().values()
         if hasattr(c, "session")}
check("the focused tile is marked", tiles["10"].focused is True)
check("…and only that one", tiles["20"].focused is False)
check("focus is a white bar, and only the focused tile has one",
      tiles["10"].render().foot == palette.FOCUS
      and tiles["20"].render().foot is None)
check("…thick enough to read as a block, not a line",
      tiles["10"].render().foot_h >= 12, tiles["10"].render().foot_h)
check("…anchored to the BOTTOM, so it cannot displace the project name",
      tiles["10"].render().rule is None and tiles["10"].render().frame is None)
check("focus does NOT tint the field — that would desaturate the state",
      tiles["10"].render().bg == tiles["20"].render().bg,
      f'{tiles["10"].render().bg} vs {tiles["20"].render().bg}')
check("focus is part of slot identity, so it repaints",
      ":focus" in tiles["10"].render().key)

ff.focused_handle = "20"
fdd.poller.poll_once()
check("a focus change alone triggers a rebuild", fdd.refresh() is True)
tiles = {c.session.handle: c for c in fdd.view.components().values()
         if hasattr(c, "session")}
check("…and the marker moved", tiles["20"].focused and not tiles["10"].focused)

ff.focused_handle = None
fdd.poller.poll_once(); fdd.refresh()
tiles = {c.session.handle: c for c in fdd.view.components().values()
         if hasattr(c, "session")}
check("no focus (you're in Firefox) marks nothing",
      not any(t.focused for t in tiles.values()))


print("\n[session_info] the context-sensitive action bar")

from cockpit.actions import _short_model, session_info
from cockpit.sessions import Telemetry


rich = replace(mk("claude:900", "peregrine", "t", "idle"),
               model="Opus 4.8 (1M context)",
               telemetry=Telemetry(tokens=5000, cost_usd=38.83, context_pct=96.0))
fa = FocusAdapter([rich], focused_handle="900")
fd = Dashboard(fa, interval=99)
fd.set_actions(default_bar(fd, None))
fd.poller.poll_once()
fd.refresh()

check("focused_session resolves via the adapter",
      fd.focused_session() is not None and fd.focused_session().cwd == "peregrine")
bar = {i: c for i, c in fd.view.components().items() if i >= 4}
check("bar occupies all four action keys", set(bar) == {4, 5, 6, 7})
check("key4 shows the model, shortened",
      bar[4].render().label == "Opus 4.8", bar[4].render().label)
check("key5 shows context percent", bar[5].render().label == "96%")
check("…as a bar", bar[5].render().bar == 0.96)
check("…amber past 80%", bar[5].render().bar_color == palette.CAUTION)
check("key6 shows cost", bar[6].render().label == "$38.83", bar[6].render().label)
check("key7 is always Firefox", bar[7].render().label == "Firefox")
check("info keys are inert — they describe, they don't act",
      bar[4].on_press(False) is False and bar[6].on_press(False) is False)
check("Firefox key IS pressable", bar[7]._run is not None)

fa.focused_handle = None
fd.poller.poll_once()
fd.refresh()
bar = {i: c for i, c in fd.view.components().items() if i >= 4}
check("no focused session dims the info keys", bar[4].enabled() is False)
check("…and Firefox stays available", bar[7].enabled() is True)

check("a large cost drops the cents", _short_model("Sonnet 5") == "Sonnet 5")
big = replace(rich, cwd="peregrine2", telemetry=Telemetry(cost_usd=251.19))
fa.set([big]); fa.focused_handle = "900"
fd.poller.poll_once(); fd.refresh()
check("…rendering as $251, not $251.19",
      {i: c for i, c in fd.view.components().items()}[6].render().label == "$251")

check("model with no statusline yet shows an em dash", _short_model("") == "—")

print(f"\n=== {ok} passed, {fail} failed ===")
sys.exit(1 if fail else 0)
