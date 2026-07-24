"""The colour language. One meaning per hue, for the whole surface.

This module exists because the previous palette was not a palette — it was the
same hex literals typed into three files with three different meanings.
`STYLE["working"]` and `ANSWER_YES` were both `("#0E2A16", "#4CD964")`;
`STYLE["blocked"]`, `ANSWER_NO` and `error_slot()` were all
`("#3A0A0A", "#FF6B6B")`; the `waiting` accent, `Slot.bar_color`'s default and
`meter()`'s default were all `#3FA7D6`. So red meant *blocked*, *No*, and
*renderer crashed*, all on screen at once. Colour cannot carry status if three
unrelated things wear the same coat.

**The rule: a hue means one thing, deck-wide.**

    red      warning    a session has stopped and cannot continue without you
    amber    caution    wants attention, but is not blocking
    green    go         affirmative — the answer bar only, never a session state
    blue     advisory   in motion; nothing for you to do
    grey     inert      idle, declined, disabled, furniture

Two consequences worth stating, because they are the point rather than side
effects:

**Green never appears on the session board, and red never appears on the answer
bar.** Green is the most over-learned "yes" in computing and red the most
over-learned "no", so the answer keys keep them — but that means the board must
give them up, which is why `working` is blue here and not green. Decline is grey
rather than red: declining a permission prompt is always the safe move, and
alarm-colouring the safe option both misleads and spends the one hue that means
"a session needs you" on a key that never does.

**Warm means act; cool means ignore.** blocked and waiting are the warm pair,
working and idle the cool pair. That gives a second, coarser read that survives
peripheral vision and colour-blindness: you can tell whether the board wants
anything from you by its temperature, without resolving a single hue.

**The hue is the whole tile.** Revised 2026-07-22 after seeing it on the
hardware: the first cut carried state in a 4-9 px top rule over a near-black
field with a ~15% tint, which reads fine on a monitor and nearly vanishes on a
96 px key a foot away. Thin chrome does not survive the trip to real glass. So
the field itself is now the state colour at full strength, and brightness
carries urgency — blocked and waiting are loud, working is present but calm,
idle recedes to slate. Text colour is declared per state rather than computed,
because each field needs its own answer to "what stays legible on this".

Ink was lifted again on 2026-07-22: beside the action bar — which is near-white
on near-black, or near-black on a pure hue, both about as contrasty as a screen
gets — the board's muted captions read as washed out. Every state's ink and
ink_dim now sit much further from its field.

This mapping follows the aviation annunciator convention — red warning, amber
caution, blue advisory — which is both well-trodden and, given what this project
is called, hard to argue with.
"""

from __future__ import annotations

from dataclasses import dataclass

from deck.color import distinct

# --- the five meanings -----------------------------------------------------

WARNING = "#FF4A47"      # red    — stopped, needs you now
CAUTION = "#E8A21C"      # amber  — attention wanted, not blocking
GO = "#3BD07C"           # green  — affirmative
ADVISORY = "#4A9EFF"     # blue   — in motion, no action needed
INERT = "#3A3F46"        # grey   — parked, declined, disabled

# The quiet field — used by an idle session tile AND by every action-bar key.
#
# These used to differ (#2C323B against #0E0F12) and on an all-idle board the
# top row read as a visibly lighter grey than the bottom, for no reason anyone
# could name: better than three times the luminance, carrying no meaning. The
# board and the controls are already told apart by structure — left-aligned
# project-over-task versus a centred value over a small-caps caption — and
# brightness does not need to say it a second time, badly.
#
# Near-black rather than black, so a tile still reads as an object against the
# bezel when it has nothing to say. The whole point is that a quiet deck looks
# like ONE quiet surface, which is what makes a single coloured tile shout.
QUIET = "#1A1E24"
FIELD = QUIET
FURNITURE = QUIET

INK = "#F2F4F7"          # primary text
INK_DIM = "#7A828C"      # captions, secondary text
INK_OFF = "#4A5058"      # disabled text

# "You are here." A frame, never a tint: lightening a status colour desaturates
# it — the old FOCUS_LIFT of 0.30 turned blocked's field into a muted mauve and
# working's into a grey-green — so the tile you are looking at was the one whose
# status was hardest to read. Focus is a different question from urgency and it
# gets its own channel.
FOCUS = "#FFFFFF"
# A frame alone loses. Two pixels was invisible, four was still "a thin white
# line" on real glass — the bezel and the viewing angle eat any hairline, and a
# saturated field crowds it further. So focus is MASS: a solid white bar, thick
# enough to read as a block rather than a line.
#
# It sits at the BOTTOM, not the top. A top cap worked visually but pushed the
# project name down by its own height, so the title sat at one of two heights
# depending on focus and the row read as disjointed (Grant, 2026-07-22). The
# label is anchored to the top of the tile, so a bottom band costs it nothing —
# the same amount of white, and the type never moves.
#
# No frame either, for the same reason: an inset perimeter shifts the text
# horizontally by its own width. Focus must be addable and removable without
# anything else on the tile twitching.
FOCUS_FOOT_H = 20
# The band's top corners are rounded the WRONG way — concave fillets that flare
# the white up the left and right edges instead of shaving it off them. Two
# reasons, both about the 96 px key. A normal radius removes mass exactly where
# a bottom band has least of it, at the ends, and mass is the whole argument for
# the band. And a straight full-width edge reads as a second horizon on a tile
# that already has a meter above it — the flare makes the white look like it
# belongs to this tile rather than sitting on top of it.
FOCUS_FOOT_R = 8


@dataclass(frozen=True)
class StateStyle:
    """How one session state looks. The field IS the signal; brightness ranks it."""

    color: str          # the pure hue — info-bar chips, and nothing else
    field: str          # the flooded tile background: the state, full strength
    ink: str            # label colour, chosen to stay legible on `field`
    ink_dim: str        # subtitle colour on `field`
    word: str           # the state in words, for the info bar
    badge: str          # a corner glyph: redundancy that costs no caption space
    needs_you: bool     # drives ordering and the info-bar tally
    # Motion is per-state and opt-in, NOT implied by needs_you. Grant, on living
    # with it (2026-07-22): a breathing red tile is intolerable to sit beside.
    # Loud and still beats loud and moving once the field is already saturated —
    # the flood does the attention-getting the pulse was there for.
    breathes: bool = False    # slow sustain while the state persists
    flashes: bool = False     # decaying spike on entering the state


STATE = {
    # Loud. White on a saturated red is the highest-urgency thing the deck can
    # show, and blocked is the only state that earns it.
    # Deliberately motionless. A saturated red field is already the loudest
    # thing the deck can show; adding movement made it nag rather than inform.
    # Flip breathes/flashes here to bring it back — the machinery is untouched.
    "blocked": StateStyle(WARNING, "#F03A34", "#FFFFFF", "#FFE2E1",
                          "BLOCKED", "!", True,
                          breathes=False, flashes=False),
    # Amber is light, so this one flips to dark ink — which also makes it read
    # as a caution sign rather than a second alarm.
    # Amber still breathes: it is the quieter of the two warm states, and a
    # session that wants you without blocking is the one most easily missed.
    "waiting": StateStyle(CAUTION, "#F5AC1A", "#150F02", "#4A3506",
                          "WAITING", "?", True,
                          breathes=True, flashes=True),
    # Present but calm: unmistakably blue at a glance, without competing with
    # the warm pair for attention. A working session needs nothing from you.
    "working": StateStyle(ADVISORY, "#2A66C8", "#FFFFFF", "#D6E6FF",
                          "WORKING", "", False),   # cool states never move
    # Recedes to exactly the same field the action bar sits on, so a board with
    # nothing happening is one uniform surface rather than two shades of grey.
    "idle": StateStyle(INERT, QUIET, "#EDF1F5", "#97A1AD",
                       "IDLE", "", False),
}

# --- the answer bar --------------------------------------------------------
#
# Colour here describes *your option*, not a session's motion, which is exactly
# why it must not borrow the board's palette.

ANSWER_AFFIRM = GO           # "Yes"
ANSWER_GRANT = CAUTION       # "Yes, and don't ask again" — approval that widens
ANSWER_DECLINE = WARNING     # "No"
ANSWER_CANCEL = INERT        # Escape

# Answer keys are a QUIET field with the hue in the icon and the frame, not a
# flooded one (Grant, 2026-07-22). Three flooded rows of saturated colour did
# not sit with the rest of the surface, and the flood was doing work the icon
# can do better now that icons render cleanly.
#
# **This is what lets "No" be red again.** The earlier objection stands — red
# means "a session needs you" and must not be spent elsewhere — but that was an
# argument about a red *field*. A red glyph on a dark key is a different object
# from a flooded red tile: different row, different form, and the answer bar
# only exists while a prompt is on screen. Green/yes and red/no are the most
# over-learned pair in computing and are worth having where the stakes are
# highest, so long as they cannot be mistaken for the board.
ANSWER_BG = FURNITURE
ANSWER_INK = INK
ANSWER_INK_DIM = INK_DIM
ANSWER_FRAME_W = 7

# The one place two flooded keys can wear the same hue at once: a `waiting`
# session tile and a permission-widening answer key are both amber. That is
# consistent by the rule above — both mean caution — and they differ in row,
# alignment and shape. Worth watching on hardware; if it ever misreads, the
# answer key is the one that should move, because the board is the surface you
# read without intent.

# --- meters ----------------------------------------------------------------
#
# A meter is not a status, so it gets its own ramp rather than reusing a state
# hue. It turns amber then red because "approaching a limit" is exactly what
# caution means and "out of room" is exactly what warning means — the one
# cross-cutting reuse this palette allows.

METER = "#2F5D7C"
METER_TRACK = "#1A1D21"

# Three bands, not two, and both thresholds sit lower than the single 80% one
# they replace. A context window is not a fuel gauge you watch drain to empty:
# half full is already the moment worth knowing about, because that is when the
# decision — compact now, or start fresh — is still cheap to act on. By the time
# a session is at 80% the choice has largely been made for you.
#
#     < 50%   cool     plenty of room; the gauge is furniture
#   50-75%    amber    caution — think about where this session ends
#     >= 75%  red      warning — running out
CONTEXT_CAUTION_PCT = 50.0
CONTEXT_WARN_PCT = 75.0

def context_color(pct: float, base: str = METER, field: str = QUIET) -> str:
    """Ramp colour for a context-window gauge at `pct` (0..100).

    `base` is the colour below the first threshold — the caller's own idea of a
    quiet gauge. `field` is what the meter is drawn on. Both are passed, and
    both are passed to `distinct`, because two things on this board can swallow
    the ramp and they are the same colour. The FIELD: a tile is flooded with its
    state hue, so an amber meter on `waiting` and a red one on `blocked` are the
    colour of the thing behind them. And the gauge's own QUIET colour: that is
    the tile's ink over its field, which on `blocked` is a pale pink that a
    lightened red lands squarely on. A meter that reaches its warning colour
    without visibly changing has warned nobody.
    """
    if pct >= CONTEXT_WARN_PCT:
        return distinct(WARNING, field, base)
    if pct >= CONTEXT_CAUTION_PCT:
        return distinct(CAUTION, field, base)
    return base


__all__ = [
    "WARNING", "CAUTION", "GO", "ADVISORY", "INERT",
    "QUIET", "FIELD", "FURNITURE", "INK", "INK_DIM", "INK_OFF",
    "FOCUS", "FOCUS_FOOT_H", "FOCUS_FOOT_R",
    "StateStyle", "STATE",
    "ANSWER_AFFIRM", "ANSWER_GRANT", "ANSWER_DECLINE", "ANSWER_CANCEL",
    "ANSWER_INK", "ANSWER_INK_DIM", "ANSWER_FRAME_W",
    "METER", "METER_TRACK", "CONTEXT_CAUTION_PCT", "CONTEXT_WARN_PCT",
    "context_color",
]
