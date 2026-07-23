"""Phase B discovery tests — process scanning and transcript reading.

Headless and hermetic. `ps`/`lsof` output and transcript files are supplied as
fixtures, so what is under test is the logic that decides *which processes are
sessions*, *which transcript belongs to which session*, and *whether a turn is
in flight* — the three things that go wrong silently in the real thing.

Every fixture below is the real observed shape (2026-07-23, a seven-session
desk), including the two collisions that make cwd unusable as a key.
"""
import json
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from fleet import transcript
from fleet.adapters.claude_process import build_sessions, parse_tty_windows
from fleet.procscan import (
    Proc,
    attach_cwds,
    normalize_tty,
    parse_lsof_cwd,
    parse_ps,
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


print("\n[normalize_tty] ps and Terminal must agree or the join matches nothing")

check("short form gains /dev", normalize_tty("ttys003") == "/dev/ttys003")
check("long form passes through", normalize_tty("/dev/ttys003") == "/dev/ttys003")
check("whitespace is stripped", normalize_tty("  ttys003 ") == "/dev/ttys003")
check("no controlling terminal is empty", normalize_tty("??") == "")
check("a dash is empty", normalize_tty("-") == "")
check("empty stays empty", normalize_tty("") == "")


print("\n[parse_ps] which processes are agent sessions")

PS = """\
74869 74653 ttys000     claude
18909 14384 ttys001     claude
29498     1 ??          /opt/homebrew/.../Python -m cockpit.daemon
53072 74869 ??          /bin/bash -c source /Users/g/.claude/shell-snapshots/snap.sh
35372 35161 ttys002     claude
99999 12345 ttys004     claudette
88888 12345 ttys006     /usr/local/bin/claude
77777 12345 ttys008     /opt/claude-helper/foo
"""

procs = parse_ps(PS)
ttys = [p.tty for p in procs]
check("finds the real sessions", len(procs) == 4, str(ttys))
check("…keeping their ttys", ttys == ["/dev/ttys000", "/dev/ttys001",
                                      "/dev/ttys002", "/dev/ttys006"], str(ttys))
check("an absolute path to the binary still counts",
      any(p.pid == 88888 for p in procs))
check("`claudette` is not a session", all(p.pid != 99999 for p in procs))
check("a path merely containing 'claude' is not a session",
      all(p.pid != 77777 for p in procs))
check("the daemon itself is not a session", all(p.pid != 29498 for p in procs))
check("a child with no controlling terminal is skipped",
      all(p.pid != 53072 for p in procs),
      "these are the agent's own subprocesses")
check("ppid is carried", procs[0].ppid == 74653)
check("garbage lines are survivable", parse_ps("nonsense\n\n12 x\n") == [])
check("empty input is survivable", parse_ps("") == [])


print("\n[parse_lsof_cwd] the -F field format is stateful")

LSOF = """\
p18909
n/Users/g/Documents/Projects/docland
p35372
n/Users/g/Documents/Projects
p81396
n/Users/g/Documents/Projects/modeling/peregrine
"""

cwds = parse_lsof_cwd(LSOF)
check("pid -> cwd", cwds.get(18909) == "/Users/g/Documents/Projects/docland")
check("…for every record", len(cwds) == 3, str(sorted(cwds)))
check("a nested path survives intact",
      cwds.get(81396) == "/Users/g/Documents/Projects/modeling/peregrine")
check("a path with spaces survives",
      parse_lsof_cwd("p1\nn/Users/g/My Projects/a b\n").get(1)
      == "/Users/g/My Projects/a b",
      "why -F is parsed instead of the columnar output")
check("an n-line with no preceding p-line is ignored",
      parse_lsof_cwd("n/orphaned\n") == {})
check("malformed input is survivable", parse_lsof_cwd("garbage\n") == {})


print("\n[attach_cwds] a missing cwd costs a label, never the session")

merged = attach_cwds([Proc(1, 0, "/dev/ttys001", "claude"),
                      Proc(2, 0, "/dev/ttys002", "claude")],
                     {1: "/Users/g/docland"})
check("a resolved cwd is attached", merged[0].cwd == "/Users/g/docland")
check("an unresolved cwd is empty, not dropped",
      merged[1].cwd == "" and len(merged) == 2)


print("\n[project_dir_name] Claude Code's cwd encoding")

check("slashes become dashes",
      transcript.project_dir_name("/Users/g/Documents/Projects/docland")
      == "-Users-g-Documents-Projects-docland")
check("a dot-directory doubles the dash",
      transcript.project_dir_name("/Users/g/cockpit/.claude/worktrees/wt")
      == "-Users-g-cockpit--claude-worktrees-wt",
      "the / and the . each become a dash")
check("existing dashes are preserved",
      transcript.project_dir_name("/Users/g/session-library")
      == "-Users-g-session-library")
check("empty is empty", transcript.project_dir_name("") == "")


print("\n[classify] working vs idle — and never blocked")


def entry(kind, blocks=None, stop=None):
    return {"type": kind,
            "message": {"role": kind,
                        "content": [{"type": b} for b in (blocks or [])],
                        "stop_reason": stop}}


check("a tool call in flight is working",
      transcript.classify([entry("assistant", ["tool_use"], "tool_use")])
      == "working")
check("a finished turn is idle",
      transcript.classify([entry("assistant", ["text"], "end_turn")]) == "idle")
check("a tool result means the model is about to continue",
      transcript.classify([entry("user", ["tool_result"])]) == "working")
check("a bare user prompt with no reply yet is idle",
      transcript.classify([entry("user", ["text"])]) == "idle")

# The trap: metadata entries are emitted constantly and sit AFTER the real
# messages. Reading the literal last line makes every session look idle.
check("trailing metadata does not mask a running turn",
      transcript.classify([
          entry("assistant", ["tool_use"], "tool_use"),
          {"type": "ai-title", "aiTitle": "x"},
          {"type": "file-history-snapshot"},
          {"type": "mode", "mode": "acceptEdits"},
      ]) == "working",
      "metadata is skipped, not treated as the tail")

check("the most recent conversational entry wins",
      transcript.classify([
          entry("assistant", ["tool_use"], "tool_use"),
          entry("user", ["tool_result"]),
          entry("assistant", ["text"], "end_turn"),
      ]) == "idle")

check("a session that died mid-turn ages out of working",
      transcript.classify([entry("assistant", ["tool_use"], "tool_use")],
                          age_s=transcript.STALE_AFTER_S + 1) == "idle",
      "a dangling tool_use must not pin a lie to the top of the board")
check("…but a fresh one does not",
      transcript.classify([entry("assistant", ["tool_use"], "tool_use")],
                          age_s=1.0) == "working")
check("no entries at all is idle", transcript.classify([]) == "idle")
check("only metadata is idle",
      transcript.classify([{"type": "ai-title", "aiTitle": "x"}]) == "idle")


print("\n[read_tail / read] against real files on disk")

tmp = tempfile.mkdtemp(prefix="fleet-transcript-")
try:
    root = os.path.join(tmp, "projects")
    cwd_a = "/Users/g/Documents/Projects"
    dir_a = os.path.join(root, transcript.project_dir_name(cwd_a))
    os.makedirs(dir_a)

    def write(path, entries):
        with open(path, "w") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")

    # Two sessions sharing one cwd — the collision that makes cwd unusable.
    a = os.path.join(dir_a, "aaaa1111.jsonl")
    b = os.path.join(dir_a, "bbbb2222.jsonl")
    write(a, [{"type": "ai-title", "aiTitle": "Recover the estate",
               "sessionId": "aaaa1111"},
              {"type": "user", "sessionId": "aaaa1111", "gitBranch": "main",
               "permissionMode": "default",
               "message": {"role": "user",
                           "content": [{"type": "text"}]}}])
    write(b, [{"type": "ai-title", "aiTitle": "New codenames",
               "sessionId": "bbbb2222"},
              {"type": "assistant", "sessionId": "bbbb2222",
               "gitBranch": "feat/x",
               "message": {"role": "assistant", "stop_reason": "tool_use",
                           "content": [{"type": "tool_use"}]}}])
    os.utime(b, (time.time(), time.time()))

    ma = transcript.read(a)
    mb = transcript.read(b)
    check("reads the ai-title", ma.ai_title == "Recover the estate")
    check("…and the session id", ma.session_id == "aaaa1111")
    check("…and the git branch", mb.git_branch == "feat/x")
    check("…and the permission mode", ma.permission_mode == "default")
    check("state comes from the tail", mb.state == "working" and ma.state == "idle")

    check("two sessions in one cwd are both listed",
          len(transcript.transcripts_for(cwd_a, root=root)) == 2)

    # find() keyed on session id, which is the whole point
    transcript._PATH_CACHE.clear()
    check("find() resolves by session id, not by newest",
          transcript.find("aaaa1111", cwd_hint=cwd_a, root=root) == a,
          "the newest file in that dir is the other session's")
    transcript._PATH_CACHE.clear()
    check("…and the other one too",
          transcript.find("bbbb2222", cwd_hint=cwd_a, root=root) == b)

    # The renamed / cd'd case: the hint is simply wrong.
    transcript._PATH_CACHE.clear()
    check("a wrong cwd hint falls back to searching",
          transcript.find("aaaa1111", cwd_hint="/Users/g/moved/elsewhere",
                          root=root) == a,
          "the transcript dir is keyed on the cwd the session STARTED in")
    transcript._PATH_CACHE.clear()
    check("no hint at all still finds it",
          transcript.find("aaaa1111", root=root) == a)
    check("an unknown session id is None",
          transcript.find("nope", root=root) is None)
    check("an empty session id is None", transcript.find("", root=root) is None)

    # A half-written final line is the normal state of a file being appended to.
    torn = os.path.join(dir_a, "cccc3333.jsonl")
    with open(torn, "w") as f:
        f.write(json.dumps({"type": "ai-title", "aiTitle": "Good",
                            "sessionId": "cccc3333"}) + "\n")
        f.write('{"type": "assist')          # torn mid-write
    mt = transcript.read(torn)
    check("a torn final line is skipped, not fatal",
          mt is not None and mt.ai_title == "Good")

    check("an unreadable path is None",
          transcript.read(os.path.join(dir_a, "missing.jsonl")) is None)
    check("a cwd with no project dir lists nothing",
          transcript.transcripts_for("/nowhere", root=root) == [])
finally:
    shutil.rmtree(tmp, ignore_errors=True)


print("\n[parse_tty_windows] tty -> window, for navigation")

check("maps tty to window id",
      parse_tty_windows("54593\t/dev/ttys000\n49378\t/dev/ttys002\n")
      == {"/dev/ttys000": "54593", "/dev/ttys002": "49378"})
check("a window mid-close (no tty) is skipped, not mapped to ''",
      parse_tty_windows("54593\t\n49378\t/dev/ttys002\n")
      == {"/dev/ttys002": "49378"},
      "otherwise every such window collides onto one key")
check("a non-numeric window id is skipped",
      parse_tty_windows("notanid\t/dev/ttys002\n") == {})
check("empty input is survivable", parse_tty_windows("") == {})


print("\n[build_sessions] composing process + registry + transcript")


class FakeRec:
    def __init__(self, session_id=None, flag=None, telemetry=None, model=""):
        self.session_id = session_id
        self.flag = flag
        self.telemetry = telemetry
        self.model = model


class FakeMeta:
    def __init__(self, state="idle", ai_title=None, last_prompt=None):
        self.state = state
        self.ai_title = ai_title
        self.last_prompt = last_prompt


p1 = Proc(74869, 1, "/dev/ttys000", "claude",
          "/Users/g/Documents/Projects/cockpit/.claude/worktrees/session-library")
p2 = Proc(18909, 1, "/dev/ttys001", "claude", "/Users/g/Documents/Projects/docland")

built = build_sessions(
    [p1, p2],
    {"/dev/ttys000": FakeRec("sid-a", None, None, "Opus 4.8")},
    {"/dev/ttys000": FakeMeta("working", ai_title="Evaluate the abstraction")},
)
a, b = built

check("the id is keyed on pid", a.id == "claude:pid:74869",
      "available on the first poll, unlike session_id")
check("the handle is the tty", a.handle == "/dev/ttys000",
      "terminal-agnostic identity; resolved to a window at press time")
check("cwd is shortened to the basename for the label",
      a.cwd == "session-library")
check("the task comes from the transcript title",
      a.task == "Evaluate the abstraction")
check("the session id comes from the registry", a.session_id == "sid-a")
check("…and the model too", a.model == "Opus 4.8")
check("state comes from the transcript", a.state == "working")

check("a session with no registry record still appears", b.cwd == "docland")
check("…with no session id", b.session_id is None)
check("…and no model", b.model is None)
check("…and no transcript means idle, never invented activity",
      b.state == "idle")
check("…and an empty task rather than a crash", b.task == "")
check("nothing parses a title any more", a.title is None and b.title is None)

fused = build_sessions(
    [p1], {"/dev/ttys000": FakeRec("sid-a", "blocked")},
    {"/dev/ttys000": FakeMeta("idle")})
check("a hook flag raises a quiet session", fused[0].state == "blocked")

fused = build_sessions(
    [p1], {"/dev/ttys000": FakeRec("sid-a", "blocked")},
    {"/dev/ttys000": FakeMeta("working")})
check("…but a running turn beats a stale flag", fused[0].state == "working",
      "fuse_state: polled truth wins, same as adapter #1")

titled = build_sessions([p1], {}, {"/dev/ttys000": FakeMeta("idle",
                                   last_prompt="do the thing")})
check("last_prompt is the fallback when there is no ai-title",
      titled[0].task == "do the thing")

check("no processes means no sessions", build_sessions([], {}, {}) == [])


print(f"\n=== {ok} passed, {fail} failed ===")
sys.exit(1 if fail else 0)
