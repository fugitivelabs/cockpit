# Prompt shapes — what the deck may answer, and what it must not

The most safety-critical knowledge in this project, extracted from
[design.md](design.md) because it is the thing most likely to be forgotten and
most expensive to get wrong. Everything here was **observed live**, not inferred
from documentation.

## The rule

A key that answers a prompt is a key that types into a session you may not be
looking at. So the deck never *guesses* what a prompt looks like — it reads the
options off the screen ([axread.py](../src/cockpit/axread.py)) and labels each
key with the screen's own words. If it can't read a menu, it shows no answer
keys and falls back to "this session needs you, go look."

## Why guessing was unsafe — measured, not theorized

Three prompts, captured 2026-07-21:

| Prompt | 1 | 2 | 3 |
|---|---|---|---|
| Bash, outside project | Yes | **No** | — |
| Write, outside project | Yes | Yes, +allow settings this session | **No** |
| WebFetch | Yes | Yes, +don't ask again for example.com | **No**, and tell Claude… |

**Option 2 is a rejection on the first and a permanent permission grant on the
third**, and the option count itself varies. A fixed accept/always/reject bar
sends the same digit to all three. That design was one build session from
shipping.

## Interaction facts

- **Digits fire immediately.** No Enter. Verified on both prompt families.
- **Escape cancels**, and is the only shape-independent answer. It must be sent
  as `key code 53` — `keystroke "\x1b"` is silently ignored by System Events.
- **Question menus advertise `Enter to select · ↑/↓ to navigate`** and never
  mention digits. Digits work anyway, but we are relying on an undocumented
  shortcut; if answering ever breaks silently, look here first.
- **Permission prompts have a different footer** (`Tab to amend · ctrl+e to
  explain`), which is how the parser tells a live menu from scrollback.

## The trap: a menu can be open with a text field live inside it

Picking "Type something" on a question **leaves every option on screen**. The
menu still parses. Digits then land in the text box as characters — observed
live, the deck happily typed `1234` into a message. Worse, the option's own
label mutates into the half-typed sentence, so a key would relabel itself with
your prose.

The only reliable tell is one footer fragment:

```
menu idle       … ↑/↓ to navigate · Esc to cancel
field ACTIVE    … ↑/↓ to navigate · ctrl+g to edit in Vim · Esc to cancel
                                    ^^^^^^^^^^^^^^^^^^^^^^
```

`ctrl+g to edit in Vim` appears only while a text input is active. The parser
suppresses all answer keys when it sees it. **Detection must come from the
footer, not the option labels** — the labels cannot distinguish these states.

## Solved

| Shape | Options | Deck behaviour |
|---|---|---|
| Tool permission | 2 or 3 | options + Esc on the spare key |
| Question, single-select | 4 (incl. "Type something", "Chat about this") | all four keys; Firefox displaced |
| Any menu, text field active | — | **no answer keys** |
| Free-text follow-up | none | **no answer keys** |

## NOT solved — do not assume these behave like the above

- **Question, multi-select.** The deck can render the options, but answering is
  a different interaction: a digit almost certainly *toggles* rather than
  submits, it needs a separate confirm, and we have no way to show what is
  currently selected. A key that toggles when you believe it submits is exactly
  the failure this document exists to prevent. Until verified, it must show no
  answer keys.
- **More than four options.** Renders nothing rather than a subset — a subset
  would put "1. Yes" beside a key that isn't option 1. Likely common with
  generated answer sets; paging the menu across the bar is the obvious fix.
- **Plan-mode and other confirmations.** Never observed. Unknown shape.

The parser's conservatism is what makes every unsolved case safe by default: no
recognisable menu means no answer keys.

## The guard

Before any keystroke, at press time — not from cached state:

1. re-read that window's screen,
2. require the **identical** option list to still be there,
3. re-check that the front window is still that session,

then send. Any mismatch is a no-op with the reason logged. Refusals are
unit-tested for menu-gone, menu-changed, and focus-moved.
