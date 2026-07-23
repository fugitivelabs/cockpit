"""Stage 2 channel tests — registry, fusion, the HTTP endpoint, the statusline.

Headless, and the HTTP half is a real server on a real loopback port — the
endpoint's contract with Claude Code is the whole point, so stubbing it would
test nothing. No device, no Claude Code, no hooks installed.

The assertion that matters most is the safety one: **the endpoint must never
return a permission decision.** A hook's HTTP response can allow or deny a tool
call, and a session dashboard that could accidentally approve one would be a
far worse defect than any wrong color.
"""
import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from fleet.adapters.claude_code import ClaudeCodeAdapter, parse_listing
from fleet.listener import HOOK_PATHS, ChannelListener
from fleet.registry import BLOCKED, NEEDS_INPUT, Registry, fuse_state
from fleet.sessions import Telemetry
from fleet.statusline import render

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


print("\n[fuse_state] hooks for edges, polling for truth")

check("a spinning session is working regardless of a stale flag",
      fuse_state("working", BLOCKED) == "working")
check("…which is how an answered prompt clears with no 'answered' event",
      fuse_state("working", NEEDS_INPUT) == "working")
check("a quiet session with a blocked flag is blocked",
      fuse_state("idle", BLOCKED) == "blocked")
check("a quiet session needing input is waiting",
      fuse_state("idle", NEEDS_INPUT) == "waiting")
check("no flag leaves the polled state alone", fuse_state("idle", None) == "idle")
check("an unknown flag is ignored, not trusted",
      fuse_state("idle", "nonsense") == "idle")


print("\n[Registry]")

clock = [100.0]
r = Registry(clock=lambda: clock[0], ttl=60.0)
check("empty to start", len(r) == 0)

r.note_statusline("sess-a", tty="/dev/ttys010", cwd="/x/peregrine",
                  telemetry=Telemetry(tokens=1500, context_pct=8.0), model="Opus")
check("statusline registers a session", len(r) == 1)
check("…and it is joinable by tty", "/dev/ttys010" in r.by_tty())
check("…carrying its telemetry",
      r.by_tty()["/dev/ttys010"].telemetry.context_pct == 8.0)

r.set_flag("sess-a", BLOCKED, cwd="/x/peregrine")
check("a hook sets the flag", r.by_tty()["/dev/ttys010"].flag == BLOCKED)
r.set_flag("sess-a", None)
check("a clearing hook clears it", r.by_tty()["/dev/ttys010"].flag is None)

r.set_flag("sess-a", NEEDS_INPUT)
clock[0] = 130.0
check("a flag survives well past a turn", r.by_tty()["/dev/ttys010"].flag == NEEDS_INPUT)
clock[0] = 200.0
check("…but a missed clear eventually expires",
      r.by_tty()["/dev/ttys010"].flag is None)

# Precedence: Claude Code fires BOTH events for a tool approval, in no
# guaranteed order, and only PermissionRequest proves it's a tool.
r.set_flag("sess-a", None)
r.set_flag("sess-a", NEEDS_INPUT)
r.set_flag("sess-a", BLOCKED)
check("a tool approval upgrades a bare notification",
      r.by_tty()["/dev/ttys010"].flag == BLOCKED)
r.set_flag("sess-a", NEEDS_INPUT)
check("…and the notification cannot downgrade it back",
      r.by_tty()["/dev/ttys010"].flag == BLOCKED)
r.set_flag("sess-a", None)
check("…but an explicit clear always wins",
      r.by_tty()["/dev/ttys010"].flag is None)

# A closed session must stop joining: ttys get recycled, and a stale flag
# landing on an unrelated new window is the wrong-session failure mode.
r.note_statusline("sess-z", tty="/dev/ttys099")
r.set_flag("sess-z", BLOCKED)
check("a live record joins", "/dev/ttys099" in r.by_tty())
clock[0] += 200.0
check("a record gone quiet stops joining", "/dev/ttys099" not in r.by_tty())
clock[0] -= 200.0
r.set_flag("sess-z", None)

# The bug this rule exists for: a session blocked at a permission prompt emits
# NOTHING — the statusline pauses while the prompt is up and hooks are what
# being blocked means the absence of. Expiring the join on silence dropped
# exactly the sessions worth showing, two minutes into a prompt still on screen.
#
# Note the TTL here is the real one's shape — comfortably longer than the stale
# window — because that ordering IS the rule: the flag TTL is what governs how
# long a blocked session may sit, and it only gets to govern if it outlasts the
# silence timer it overrides.
q = Registry(clock=lambda: clock[0], ttl=600.0)
q.note_statusline("sess-q", tty="/dev/ttys098")
q.set_flag("sess-q", BLOCKED)
clock[0] += 200.0
check("a blocked record survives going quiet — the prompt is still up",
      q.by_tty()["/dev/ttys098"].flag == BLOCKED)
# Both of these sit well past STALE_JOIN_S, so they straddle the TTL and
# nothing else — the point being that FLAG_TTL_S is what governs now. Checking
# only far past both timers would pass under the old rule too, and prove
# nothing about which one is doing the work.
clock[0] += 390.0
check("…right up to the TTL, which is the timer that now governs it",
      q.by_tty()["/dev/ttys098"].flag == BLOCKED)
clock[0] += 20.0
check("…and no further, so a ghost cannot live forever",
      "/dev/ttys098" not in q.by_tty())
clock[0] -= 610.0

# Quit claude and start it again in the same window: same tty, new session, and
# the dead record can now outlive its session — so the tty collision this fix
# makes reachable must resolve to the live one, not the ghost.
recycled = Registry(clock=lambda: clock[0], ttl=600.0)
recycled.note_statusline("old-session", tty="/dev/ttys007")
recycled.set_flag("old-session", BLOCKED)
clock[0] += 1.0
recycled.note_statusline("new-session", tty="/dev/ttys007")
check("a recycled tty resolves to the newer session",
      recycled.by_tty()["/dev/ttys007"].session_id == "new-session")
check("…so the dead session's flag cannot paint the new one",
      recycled.by_tty()["/dev/ttys007"].flag is None)

# Same again with the records inserted the other way round. The old behaviour
# was last-write-wins by dict insertion order — right, but by accident, and only
# while a record could not outlive its session.
reverse = Registry(clock=lambda: clock[0], ttl=600.0)
clock[0] += 1.0
reverse.note_statusline("newer", tty="/dev/ttys007")
clock[0] -= 1.0
reverse.note_statusline("older", tty="/dev/ttys007")
reverse.set_flag("older", BLOCKED)
check("…and insertion order does not decide it",
      reverse.by_tty()["/dev/ttys007"].session_id == "newer")
clock[0] += 1.0

# The two above are not redundant: the forward case fails if the comparison is
# ever inverted, the reverse case fails if it is dropped altogether. Neither
# catches both, and dropping it is the likelier regression.

# A tie has no recency left to read, so it must not fall back to insertion
# order — that is the accident this rule replaced. It breaks toward the record
# with no flag, because a false red on a live window is the worse mistake.
for order in ("ghost first", "live first"):
    tie = Registry(clock=lambda: clock[0], ttl=600.0)
    if order == "ghost first":
        tie.note_statusline("ghost", tty="/dev/ttys006")
        tie.set_flag("ghost", BLOCKED)
        tie.note_statusline("live", tty="/dev/ttys006")
    else:
        tie.note_statusline("live", tty="/dev/ttys006")
        tie.note_statusline("ghost", tty="/dev/ttys006")
        tie.set_flag("ghost", BLOCKED)
    won = tie.by_tty()["/dev/ttys006"]
    check(f"a same-tick tie breaks toward the unflagged record ({order})",
          won.session_id == "live" and won.flag is None,
          f"{won.session_id} flag={won.flag}")

r.set_flag("sess-b", BLOCKED)
check("a hook before any statusline is kept", len(r) == 3)
check("…but is not joinable until a tty arrives",
      "sess-b" not in {v.session_id for v in r.by_tty().values()})
r.note_statusline("sess-b", tty="/dev/ttys011")
check("…and becomes joinable once the statusline reports",
      r.by_tty()["/dev/ttys011"].flag == BLOCKED)

check("reads are copies, not internals",
      r.by_tty()["/dev/ttys011"] is not r.by_tty()["/dev/ttys011"])
r.note_statusline("", tty="/dev/ttysX")
check("a missing session_id is ignored", len(r) == 3)
r.forget("sess-b")
check("forget drops it", len(r) == 2)


print("\n[ChannelListener] the real HTTP contract")

reg = Registry()
changes = []
listener = ChannelListener(reg, port=0, on_change=lambda: changes.append(1))
started = listener.start()
port = listener._server.server_address[1] if started else 0
check("listener binds", started and port > 0, f"port={port}")


def post(path, payload, timeout=3.0):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, json.loads(resp.read() or b"{}")


status, body = post("/statusline", {
    "session_id": "s1", "cwd": "/p/peregrine", "tty": "/dev/ttys020",
    "model": {"display_name": "Opus"},
    "cost": {"total_cost_usd": 0.42},
    "context_window": {"total_input_tokens": 9000, "total_output_tokens": 1000,
                       "used_percentage": 12},
})
check("statusline POST returns 200", status == 200)
check("…with an empty body — never a permission decision", body == {})
check("…and registers the tty join", "/dev/ttys020" in reg.by_tty())
rec = reg.by_tty()["/dev/ttys020"]
check("…with tokens summed", rec.telemetry.tokens == 10000, str(rec.telemetry))
check("…and cost", rec.telemetry.cost_usd == 0.42)
check("…and context percent", rec.telemetry.context_pct == 12.0)
check("on_change fired", len(changes) >= 1)

status, body = post("/hook/blocked", {"session_id": "s1", "cwd": "/p/peregrine"})
check("blocked hook returns 200 and an empty body",
      status == 200 and body == {})
check("…and flags the session", reg.by_tty()["/dev/ttys020"].flag == BLOCKED)

post("/hook/stop", {"session_id": "s1"})
check("a stop hook clears the flag", reg.by_tty()["/dev/ttys020"].flag is None)

post("/hook/needs-input", {"session_id": "s1"})
check("needs-input flags waiting", reg.by_tty()["/dev/ttys020"].flag == NEEDS_INPUT)
post("/hook/idle", {"session_id": "s1"})
check("idle_prompt clears it", reg.by_tty()["/dev/ttys020"].flag is None)

# AskUserQuestion is a tool, so PermissionRequest fires for it exactly as it
# does for Bash. Only tool_name separates "answer this question" (blue) from
# "approve this command" (red).
post("/hook/blocked", {"session_id": "s1", "tool_name": "AskUserQuestion"})
check("a question routed to /hook/blocked becomes waiting, not blocked",
      reg.by_tty()["/dev/ttys020"].flag == NEEDS_INPUT,
      str(reg.by_tty()["/dev/ttys020"].flag))
post("/hook/stop", {"session_id": "s1"})
post("/hook/blocked", {"session_id": "s1", "tool_name": "Bash"})
check("…while a real tool stays blocked",
      reg.by_tty()["/dev/ttys020"].flag == BLOCKED)
post("/hook/stop", {"session_id": "s1"})
post("/hook/blocked", {"session_id": "s1"})
check("…and no tool_name at all is treated as blocked (the safer read)",
      reg.by_tty()["/dev/ttys020"].flag == BLOCKED)
post("/hook/stop", {"session_id": "s1"})

check("every configured hook path is mapped",
      set(HOOK_PATHS) == {"/hook/blocked", "/hook/needs-input", "/hook/idle",
                          "/hook/stop", "/hook/active", "/hook/tool-done"})

print("\n[question vs block] a named tool is a new prompt, not a downgrade")
# From a real capture (2026-07-22): a turn ended with a Bash approval one event
# before an AskUserQuestion, so a stale `blocked` was standing when the question
# arrived. The remap made the question `waiting`, but the no-downgrade rule kept
# it red. A question is "needs you", not "blocked on a tool", so it must win.
rq = Registry(); rq.note_statusline("q", tty="/dev/ttys060")
rq.set_flag("q", BLOCKED, tool="Bash")
rq.set_flag("q", NEEDS_INPUT, tool="AskUserQuestion")
check("a question naming its tool overrides a stale block from another tool",
      rq.by_tty()["/dev/ttys060"].flag == NEEDS_INPUT,
      rq.by_tty()["/dev/ttys060"].flag)

# The two things the ranking still has to protect, or it was the wrong fix:
rp = Registry(); rp.note_statusline("p", tty="/dev/ttys061")
rp.set_flag("p", BLOCKED, tool="Bash")
rp.set_flag("p", NEEDS_INPUT, tool="")          # the bare notification, no tool
check("a NO-tool notification still cannot downgrade a real block",
      rp.by_tty()["/dev/ttys061"].flag == BLOCKED, rp.by_tty()["/dev/ttys061"].flag)
rp2 = Registry(); rp2.note_statusline("p2", tty="/dev/ttys062")
rp2.set_flag("p2", NEEDS_INPUT, tool="")        # …in either arrival order
rp2.set_flag("p2", BLOCKED, tool="Bash")
check("…in either order", rp2.by_tty()["/dev/ttys062"].flag == BLOCKED)

# And a real tool block after a question must still go red.
rb = Registry(); rb.note_statusline("b", tty="/dev/ttys063")
rb.set_flag("b", NEEDS_INPUT, tool="AskUserQuestion")
rb.set_flag("b", BLOCKED, tool="Bash")
check("a real tool approval after a question shows red",
      rb.by_tty()["/dev/ttys063"].flag == BLOCKED)

print("\n[concurrency] a sibling tool finishing must not clear a live prompt")
# Replayed from a real capture (2026-07-22): a session held a WebFetch approval
# while WebSearch / ToolSearch / StructuredOutput kept completing under the SAME
# session_id. Each sibling used to wipe the flag a moment after it was raised,
# and the Notification re-nag put it back — the idle/red/amber flicker.
reg2 = Registry()
reg2.note_statusline("c1", tty="/dev/ttys050")
reg2.set_flag("c1", BLOCKED, tool="WebFetch")
check("a tool approval raises blocked", reg2.by_tty()["/dev/ttys050"].flag == BLOCKED)
for sibling in ("WebSearch", "ToolSearch", "StructuredOutput", "WebSearch"):
    reg2.set_flag("c1", None, tool=sibling, scope="tool")
check("…and four sibling completions leave it standing",
      reg2.by_tty()["/dev/ttys050"].flag == BLOCKED,
      str(reg2.by_tty()["/dev/ttys050"].flag))
reg2.set_flag("c1", NEEDS_INPUT, tool="")     # the Notification re-nag
check("…a re-nag cannot downgrade it either",
      reg2.by_tty()["/dev/ttys050"].flag == BLOCKED)
reg2.set_flag("c1", None, tool="WebFetch", scope="tool")
check("the tool that RAISED it does clear it",
      reg2.by_tty()["/dev/ttys050"].flag is None)

reg2.set_flag("c1", BLOCKED, tool="Bash")
reg2.set_flag("c1", None, scope="session")
check("a session-wide edge (Stop/idle) clears whatever the tool was",
      reg2.by_tty()["/dev/ttys050"].flag is None)

reg2.set_flag("c1", BLOCKED, tool="")          # raised with no tool named
reg2.set_flag("c1", None, tool="Whatever", scope="tool")
check("a flag raised without a tool name is still clearable",
      reg2.by_tty()["/dev/ttys050"].flag is None,
      "otherwise an unnamed flag could never be cleared")

# Robustness: none of these may take the daemon down or emit a decision.
raw = urllib.request.Request(f"http://127.0.0.1:{port}/hook/blocked",
                             data=b"not json at all",
                             headers={"Content-Type": "application/json"},
                             method="POST")
with urllib.request.urlopen(raw, timeout=3.0) as resp:
    check("malformed JSON still returns 200 with no decision",
          resp.status == 200 and json.loads(resp.read() or b"{}") == {})

status, body = post("/hook/unknown-path", {"session_id": "s1"})
check("an unknown hook path is a harmless 200", status == 200 and body == {})

status, body = post("/statusline", {})
check("an empty payload is survivable", status == 200)

with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=3.0) as resp:
    health = json.loads(resp.read())
check("health endpoint reports sessions", health.get("ok") is True)

with urllib.request.urlopen(f"http://127.0.0.1:{port}/sessions", timeout=3.0) as resp:
    dump = json.loads(resp.read())
rows = {r["tty"]: r for r in dump["sessions"]}
check("/sessions exposes the join table", "/dev/ttys020" in rows, str(list(rows)))
check("…with the session id", rows["/dev/ttys020"]["session_id"] == "s1")
check("…and telemetry for eyeballing", rows["/dev/ttys020"]["context_pct"] == 12.0)

try:
    urllib.request.urlopen(f"http://127.0.0.1:{port}/nope", timeout=3.0)
    check("unknown GET is 404", False)
except urllib.error.HTTPError as e:
    check("unknown GET is 404", e.code == 404)

# /doctor is the library's one host-supplied seam. The probes it reports are
# the *daemon's* TCC grants, which only the app can run — so the listener takes
# a callable instead of importing the app's doctor module, which was the single
# edge stopping this file from being library code. Three things to pin: absent
# means the route still answers, present means the host's checks are served
# verbatim, and a raising probe must not take the endpoint down with it.
with urllib.request.urlopen(f"http://127.0.0.1:{port}/doctor", timeout=3.0) as resp:
    doc = json.loads(resp.read())
check("/doctor answers with no self_check injected", doc.get("checks") == [])

listener.stop()

probed = []


def _fake_checks():
    probed.append(1)
    return [{"name": "accessibility", "status": "ok"}]


injected = ChannelListener(Registry(), port=0, self_check=_fake_checks)
injected.start()
iport = injected._server.server_address[1]
with urllib.request.urlopen(f"http://127.0.0.1:{iport}/doctor", timeout=3.0) as resp:
    doc = json.loads(resp.read())
check("an injected self_check is served verbatim",
      doc.get("checks") == [{"name": "accessibility", "status": "ok"}])
check("…and it really ran in the handler", probed == [1])
injected.stop()


def _exploding_checks():
    raise RuntimeError("TCC read blew up")


boom = ChannelListener(Registry(), port=0, self_check=_exploding_checks)
boom.start()
bport = boom._server.server_address[1]
with urllib.request.urlopen(f"http://127.0.0.1:{bport}/doctor", timeout=3.0) as resp:
    doc = json.loads(resp.read())
check("a raising self_check degrades to no checks, not a 500",
      doc.get("checks") == [])
boom.stop()

second = ChannelListener(Registry(), port=port)
# Port is free again after stop(), so this proves shutdown actually released it.
check("stop() releases the port", second.start() is True)
second.stop()

busy_a = ChannelListener(Registry(), port=0)
busy_a.start()
taken = busy_a._server.server_address[1]
busy_b = ChannelListener(Registry(), port=taken)
check("a taken port disables channels rather than crashing",
      busy_b.start() is False)
busy_a.stop()


print("\n[adapter fusion] joining hook state onto windows by tty")

reg2 = Registry()
adapter = ClaudeCodeAdapter(registry=reg2)
listing = ("100\t/dev/ttys001\tpereg — ✳ a task — claude — 1×1\n"
           "200\t/dev/ttys002\tdoc — ⠐ b task — claude — 1×1\n")
sessions = parse_listing(listing)
check("parse keeps the tty", sessions[0].tty == "/dev/ttys001")

fused = {s.handle: s for s in adapter._fuse(sessions)}
check("no registry data leaves sessions untouched", fused["100"].state == "idle")

reg2.note_statusline("s-A", tty="/dev/ttys001", cwd="/p",
                     telemetry=Telemetry(context_pct=55.0))
reg2.set_flag("s-A", BLOCKED)
fused = {s.handle: s for s in adapter._fuse(sessions)}
check("a blocked hook reaches the right window", fused["100"].state == "blocked")
check("…and only that window", fused["200"].state == "working")
check("…carrying the session id through the join",
      fused["100"].session_id == "s-A")
check("…and the telemetry", fused["100"].telemetry.context_pct == 55.0)

reg2.note_statusline("s-B", tty="/dev/ttys002")
reg2.set_flag("s-B", BLOCKED)
fused = {s.handle: s for s in adapter._fuse(sessions)}
check("a spinning window ignores a blocked flag (polling wins)",
      fused["200"].state == "working")

check("an adapter with no registry is Stage 1",
      ClaudeCodeAdapter()._fuse(sessions) == sessions)

untracked = parse_listing("300\t\tx — ✳ t — claude — 1×1\n")
check("a window with no tty still renders", len(untracked) == 1)
check("…and survives fusion", adapter._fuse(untracked)[0].state == "idle")


print("\n[statusline] must never break a session")

check("renders model, context and cost",
      render({"model": {"display_name": "Opus"},
              "context_window": {"used_percentage": 8},
              "cost": {"total_cost_usd": 1.5}}) == "Opus  ·  8% ctx  ·  $1.50")
check("empty payload renders empty, not a crash", render({}) == "")
check("null context is tolerated",
      render({"model": {"display_name": "Opus"}, "context_window": None}) == "Opus")
check("zero cost is omitted",
      "$" not in render({"model": {"display_name": "Opus"},
                         "cost": {"total_cost_usd": 0}}))
check("a null used_percentage is skipped",
      render({"context_window": {"used_percentage": None}}) == "")

print("\n[claude_config] wiring that survives a new machine")

import tempfile
from cockpit import claude_config as cc

check("desired() builds every endpoint from listener.HOOK_PATHS",
      all(any(h["url"].endswith(path)
              for entries in cc.desired()["hooks"].values()
              for e in entries for h in e["hooks"])
          for path in HOOK_PATHS),
      "every served path is wired")

d = cc.desired()
check("statusline carries a refreshInterval",
      d["statusLine"]["refreshInterval"] == cc.REFRESH_INTERVAL_S)
check("…and points at fleet.statusline",
      "fleet.statusline" in d["statusLine"]["command"])
check("clearing edges are wired", "PostToolUse" in d["hooks"]
      and "PermissionDenied" in d["hooks"])
check("capture hook is OFF by default", "PreToolUse" not in d["hooks"])
check("…and available on request", "PreToolUse" in cc.desired(capture=True)["hooks"])

# Paths must follow the checkout, which is the whole point.
other = cc.desired(python="/opt/py/bin/python", repo="/elsewhere/streamdeck")
check("paths are computed from the checkout, not hardcoded",
      other["statusLine"]["command"]
      == "PYTHONPATH=/elsewhere/streamdeck /opt/py/bin/python -m fleet.statusline",
      other["statusLine"]["command"])

foreign = {"theme": "light",
           "statusLine": {"type": "command", "command": "my-own-thing"},
           "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "mine"}]}]}}
merged = cc.merge(foreign)
check("unrelated settings survive merging", merged["theme"] == "light")
check("a foreign hook on the same event survives",
      any(h.get("command") == "mine"
          for e in merged["hooks"]["Stop"] for h in e["hooks"]))
check("…alongside ours",
      any(h.get("url", "").endswith("/hook/stop")
          for e in merged["hooks"]["Stop"] for h in e["hooks"]))

check("merging is idempotent", cc.merge(merged) == merged)
twice = cc.merge(cc.merge(foreign))
check("…even applied twice, no duplicate hooks",
      len(twice["hooks"]["Stop"]) == len(merged["hooks"]["Stop"]))

stripped = cc.strip(merged)
check("strip removes our hooks",
      not any(h.get("url", "").startswith("http://127.0.0.1:8787/hook/")
              for entries in stripped.get("hooks", {}).values()
              for e in entries for h in e["hooks"]))
check("…but keeps the foreign one",
      any(h.get("command") == "mine"
          for e in stripped["hooks"]["Stop"] for h in e["hooks"]))
# Claude Code allows only one statusLine, so ours necessarily replaces a
# foreign one — the contract is that this is REPORTED, not silent.
check("a foreign statusLine is reported as clobbered",
      cc.replaced_statusline(foreign) == "my-own-thing")
check("…and our own is not reported as a clobber",
      cc.replaced_statusline(cc.merge(foreign)) is None)
check("strip removes our statusLine entirely",
      "statusLine" not in stripped)

clean = cc.strip(cc.merge({}))
check("wire then unwire on an empty file leaves it empty", clean == {})

# Round-trip through a real file, atomically.
tmp = tempfile.mktemp(suffix=".json")
changed, bak, _ = cc.apply(path=tmp)
check("apply writes the file", changed and os.path.exists(tmp))
again, _, _ = cc.apply(path=tmp)
check("…and is a no-op the second time", again is False)
removed, _ = cc.remove(path=tmp)
check("remove undoes it", removed is True)
check("…leaving valid JSON", isinstance(cc.load(tmp), dict))
os.unlink(tmp)

print(f"\n=== {ok} passed, {fail} failed ===")
sys.exit(1 if fail else 0)
