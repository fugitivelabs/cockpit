"""Stage 3 tests — reading the prompt off the screen, and answering it safely.

Headless: the AX read is faked, so what's under test is the two things that can
hurt you — **what we decide is a menu**, and **what has to be true before a
keystroke is sent**.

The corpus is real. Every prompt below was captured live on 2026-07-21 from an
actual Claude Code session, which is the only reason we know option `2` means
"No" on one prompt and "permanently widen permissions" on another.
"""
import os
import sys

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from cockpit import actions as actions_mod
from cockpit.actions import ACTION_BG, answer_keys
from cockpit.axread import Prompt, parse_prompt
from cockpit.sessions import Session

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


# --- the three shapes actually observed, verbatim in structure ---------------

BASH = """⏺ I'll write a probe file outside the project.

 Do you want to proceed?
 ❯ 1. Yes
   2. No

 Esc to cancel · Tab to amend · ctrl+e to explain
"""

WRITE = """⏺ Create ~/.claude/.cockpit-probe.txt

 Do you want to create this file?
 ❯ 1. Yes
   2. Yes, and allow Claude to edit its own settings for this session
   3. No

 Esc to cancel · Tab to amend · ctrl+e to explain
"""

FETCH = """⏺ Fetch https://example.com

 ❯ 1. Yes
   2. Yes, and don't ask again for example.com
   3. No, and tell Claude what to do differently

 Esc to cancel · Tab to amend
"""

# The case Grant flagged: rejecting with "tell Claude what to do differently"
# leaves the session blocked on a FREE-TEXT box. A digit here types a literal
# "1" into the message. There are no numbered options, so this must parse None.
FOLLOWUP = """⏺ No problem — what would you like instead?

────────────────────────────────────────────
❯ tell Claude what to do differently
────────────────────────────────────────────
  Opus 4.8 (1M context)  ·  37% ctx  ·  $35.14
"""

# Captured live 2026-07-22. A question menu, and the SAME menu once the user has
# picked "Type something" and is typing into it. Every option is still on screen,
# so options alone cannot tell these apart — only the footer can. Sending a digit
# in the second state types a character into the message (observed: "1234").
QUESTION = """ Which one?
  1. Echo
     A normal option.
  2. Foxtrot
     Another normal option.
  3. Type something.
  4. Chat about this
Enter to select · ↑/↓ to navigate · Esc to cancel
"""

QUESTION_TYPING = """ Which one?
  1. Echo
     A normal option.
  2. Foxtrot
     Another normal option.
❯ 3. did three her
  4. Chat about this
Enter to select · ↑/↓ to navigate · ctrl+g to edit in Vim · Esc to cancel
"""

# Scrollback is full of numbered lists. Without the footer, this is not a menu.
SCROLLBACK = """⏺ Here's the plan:
   1. Read the file
   2. Patch the parser
   3. Run the tests

❯ go ahead
"""


print("\n[parse_prompt] the three real shapes")

p = parse_prompt(BASH)
check("bash prompt: two options", p and len(p.options) == 2)
check("…1 is Yes, 2 is No", p and p.options == ((1, "Yes"), (2, "No")), str(p and p.options))
check("…default selection is read from ❯", p and p.selected == 1)

p3 = parse_prompt(WRITE)
check("write prompt: three options", p3 and len(p3.options) == 3)
check("…option 2 is a permission-widening YES, not No",
      p3 and p3.options[1][1].startswith("Yes, and allow"), str(p3 and p3.options[1]))

pf = parse_prompt(FETCH)
check("fetch prompt: three options", pf and len(pf.options) == 3)
check("…option 3 carries its full text",
      pf and pf.options[2][1] == "No, and tell Claude what to do differently")

check("THE HAZARD: a free-text follow-up is not a menu",
      parse_prompt(FOLLOWUP) is None)

pq = parse_prompt(QUESTION)
check("a four-option question parses", pq and len(pq.options) == 4)
check("…including the 'Type something' option", pq.options[2][1] == "Type something.")
check("THE HAZARD, part 2: a live text field suppresses the menu entirely",
      parse_prompt(QUESTION_TYPING) is None)
check("…even though every option is still on screen",
      "4. Chat about this" in QUESTION_TYPING)
check("…detected from the footer, not from the option labels",
      parse_prompt(QUESTION_TYPING.replace(" · ctrl+g to edit in Vim", "")) is not None)
check("a numbered list in scrollback is not a menu",
      parse_prompt(SCROLLBACK) is None)
check("empty screen is not a menu", parse_prompt("") is None)
check("a single option is not a menu",
      parse_prompt(" ❯ 1. Yes\n\n Esc to cancel\n") is None)
check("non-consecutive numbering is rejected",
      parse_prompt(" 1. Yes\n 3. No\n\n Esc to cancel\n") is None)
check("options below the footer are ignored (stale scrollback)",
      parse_prompt(" Esc to cancel\n 1. Yes\n 2. No\n") is None)

# Option 2 means opposite things on two real prompts — the reason a fixed bar
# is unsafe, pinned as a test so nobody re-introduces one.
check("option 2 is 'No' on bash but an approval on fetch",
      parse_prompt(BASH).options[1][1] == "No"
      and parse_prompt(FETCH).options[1][1].startswith("Yes"))


print("\n[answer_keys] labelled with the screen's own text")


class FakeDash:
    """Only the surface `answer_keys` touches."""

    _UNSET = object()

    def __init__(self, prompt=None, session=None, live=_UNSET, focus_ok=True):
        self._prompt = prompt
        self._session = session or Session(
            id="claude:1", agent="claude", cwd="peregrine", task="t",
            state="blocked", handle="1", title="peregrine — ✳ t — claude")
        # Sentinel, not None: `live=None` must mean "the menu is GONE", which
        # is the single most important refusal to test.
        self._live = prompt if live is FakeDash._UNSET else live
        self._focus_ok = focus_ok
        self.reads = 0

    def focused_prompt(self):
        return self._prompt

    def focused_session(self):
        return self._session

    def read_prompt_now(self, session):
        self.reads += 1
        return self._live

    def verify_focus(self, session):
        return self._focus_ok


sent = []
actions_mod.keystroke = lambda keys: sent.append(keys) or True

d = FakeDash(parse_prompt(BASH))
bar = answer_keys(d)
check("two options -> two answer keys plus Esc in the spare slot",
      sorted(bar) == [4, 5, 6], str(sorted(bar)))
check("key4 is labelled from the screen", bar[4].render().label == "Yes")
check("key5 is labelled from the screen", bar[5].render().label == "No")
check("spare slot becomes Esc", bar[6].render().label == "Esc")
check("Yes is green", bar[4].render().accent == "#4CD964")
check("No is red", bar[5].render().accent == "#FF6B6B")

d3 = FakeDash(parse_prompt(FETCH))
bar3 = answer_keys(d3)
check("three options fill all three slots", sorted(bar3) == [4, 5, 6])
check("a permission-widening YES is amber, not green",
      bar3[5].render().accent == "#E8B923", bar3[5].render().accent)
check("…and is visibly distinct from the plain Yes",
      bar3[4].render().accent != bar3[5].render().accent)

check("no menu on screen -> no answer keys at all",
      answer_keys(FakeDash(None)) is None)

# The real question shape Grant hit: Left / Right / type something / Chat.
four = Prompt(options=((1, "Left"), (2, "Right"), (3, "type something"),
                       (4, "Chat about this")), selected=1)
bar4 = answer_keys(FakeDash(four))
check("a four-option menu takes all four keys", sorted(bar4) == [4, 5, 6, 7])
check("…displacing Firefox", bar4[7].render().label != "Firefox")
check("…and key7 is the fourth option", bar4[7].render().sub.startswith("4 ·"),
      bar4[7].render().sub)
check("…with no Esc key (keyboard Esc still works)",
      not any(c.render().label == "Esc" for c in bar4.values()))

big = Prompt(options=tuple((i, f"opt{i}") for i in range(1, 6)))
check("a menu too big for the bar shows nothing rather than a subset",
      answer_keys(FakeDash(big)) is None)


print("\n[the guard] what must be true before a keystroke is sent")

sent.clear()
d = FakeDash(parse_prompt(BASH))
bar = answer_keys(d)
bar[4].on_press(False)
import time
for _ in range(100):
    if sent:
        break
    time.sleep(0.01)
check("pressing Yes sends digit 1", sent == ["1"], str(sent))
check("…after re-reading the screen", d.reads == 1)

sent.clear()
bar[5].on_press(False)
for _ in range(100):
    if sent:
        break
    time.sleep(0.01)
check("pressing No sends digit 2", sent == ["2"])

sent.clear()
bar[6].on_press(False)
for _ in range(100):
    if sent:
        break
    time.sleep(0.01)
check("pressing Esc sends escape", sent == ["\x1b"], repr(sent))

# The three refusals. Each of these would be a keystroke into the wrong state.
sent.clear()
gone = FakeDash(parse_prompt(BASH), live=None)
answer_keys(gone)[4].on_press(False)
time.sleep(0.15)
check("REFUSES when the menu is gone (answered at the keyboard)", sent == [])

sent.clear()
changed = FakeDash(parse_prompt(BASH), live=parse_prompt(FETCH))
answer_keys(changed)[4].on_press(False)
time.sleep(0.15)
check("REFUSES when the menu changed under us", sent == [])

sent.clear()
moved = FakeDash(parse_prompt(BASH), focus_ok=False)
answer_keys(moved)[4].on_press(False)
time.sleep(0.15)
check("REFUSES when focus moved away", sent == [])

sent.clear()
nofocus = FakeDash(parse_prompt(BASH))
nofocus._session = None
answer_keys(nofocus)[4].on_press(False)
time.sleep(0.15)
check("REFUSES when nothing is focused", sent == [])

print(f"\n=== {ok} passed, {fail} failed ===")
sys.exit(1 if fail else 0)
