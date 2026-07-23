"""Finding agent sessions by looking at processes, not at windows.

The discovery channel that does not care which terminal you use. A `claude`
process carries its own controlling terminal, so `ps` alone answers "what is
running and where" for Terminal.app, iTerm2, Ghostty, VS Code and anything else
that allocates a pty — where enumerating *windows* only ever answers it for the
one app we wrote AppleScript for.

Two calls, both cheap enough to poll (measured 2026-07-23 on a 7-session desk:
`ps` plus one batched `lsof`, ~0.08s total, against ~0.10s for the AppleScript
window enumeration it replaces):

    ps   -> pid, ppid, tty, command      which sessions exist, and their tty
    lsof -> pid -> cwd                   where each one is working

**Why cwd needs a second call at all.** `ps` cannot report another process's
working directory on macOS; only `lsof -d cwd` can. It is worth the call: the
window title carries just the *last path component* (`peregrine`), so today two
sessions in `~/Documents/Projects` and `~/Documents/Projects/modeling/peregrine`
both render from a fragment. Here we get the full path, which is what lets the
label rule tell colliding sessions apart on something real instead of on the
task text.

**What this deliberately does NOT provide: `session_id`.** A `claude` process
does not hold its transcript open (checked with `lsof`, 2026-07-23 — no `.jsonl`
in its file table), so there is nothing on the process to name its session. The
statusline remains the only channel that reports `session_id` *and* tty from
inside the session, which keeps it structural exactly as `registry.py` says. A
scan tells you a session exists and where; the registry tells you which one.

**And NOT the window.** A tty is not a window. Turning one into a place you can
navigate to is per-terminal-app work and lives with the adapter that knows that
app's scripting model.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger("fleet.procscan")

# argv[0] is `claude` for the CLI. Matched as a whole word against the command
# field for the same reason `claude_code.py` does it against the title: a path
# containing "claude" or a `claudette` binary is not a session.
AGENT_COMMANDS = ("claude",)

# A session with no controlling terminal is not one we can navigate to, and on
# this machine `??` is what Claude Code's own spawned children report. Filtering
# them here is what keeps a scan from inventing sessions out of subprocesses.
NO_TTY = ("??", "-", "")


@dataclass(frozen=True)
class Proc:
    """One agent process as the OS describes it. No session identity yet."""

    pid: int
    ppid: int
    tty: str            # normalized to a full device path, e.g. /dev/ttys003
    command: str        # argv[0] as ps reports it
    cwd: str = ""       # filled in by the lsof pass; "" when unreadable


def normalize_tty(raw: str) -> str:
    """`ttys003` -> `/dev/ttys003`; pass through anything already absolute.

    `ps` reports the short form and Terminal's `tty of selected tab` reports the
    long one. They have to agree or the join silently matches nothing, which
    looks exactly like "no sessions are running".
    """
    t = (raw or "").strip()
    if not t or t in NO_TTY:
        return ""
    return t if t.startswith("/dev/") else f"/dev/{t}"


def _is_agent(command: str) -> bool:
    """Is this argv[0] one of the agent CLIs we know how to discover?"""
    head = (command or "").strip().split()
    if not head:
        return False
    # Compare on the basename so an absolute path to the binary still matches,
    # but never on a substring — `/opt/claude-helper/foo` is not a session.
    name = head[0].rsplit("/", 1)[-1]
    return name in AGENT_COMMANDS


def parse_ps(raw: str) -> list[Proc]:
    """`ps -Ao pid=,ppid=,tty=,command=` output -> agent processes. Pure.

    Skips anything without a real tty: those are the agent's own children (they
    inherit no controlling terminal, which is the same fact `statusline.own_tty`
    has to walk *up* the tree to work around).
    """
    out: list[Proc] = []
    for line in (raw or "").splitlines():
        parts = line.split(None, 3)
        if len(parts) < 4:
            continue
        pid_s, ppid_s, tty_s, command = parts
        try:
            pid, ppid = int(pid_s), int(ppid_s)
        except ValueError:
            continue
        if not _is_agent(command):
            continue
        tty = normalize_tty(tty_s)
        if not tty:
            continue
        out.append(Proc(pid=pid, ppid=ppid, tty=tty, command=command.strip()))
    return out


def parse_lsof_cwd(raw: str) -> dict[int, str]:
    """`lsof -F pn` field output -> {pid: cwd}. Pure.

    The `-F` machine format emits one field per line, tagged by its first
    character, and is stateful: a `p<pid>` line sets the context for every `n`
    line after it. Parsed rather than the human format because a path with a
    space in it makes the columnar output ambiguous.
    """
    out: dict[int, str] = {}
    pid: Optional[int] = None
    for line in (raw or "").splitlines():
        if not line:
            continue
        tag, value = line[0], line[1:]
        if tag == "p":
            try:
                pid = int(value)
            except ValueError:
                pid = None
        elif tag == "n" and pid is not None and value:
            # First n-line for a pid wins; with `-d cwd` there is only one.
            out.setdefault(pid, value)
    return out


def attach_cwds(procs: list[Proc], cwds: dict[int, str]) -> list[Proc]:
    """Merge the two passes. A process with no readable cwd keeps `""`.

    Losing a cwd costs that tile its label, not its existence — a session you
    can still navigate to is worth showing with a poor name.
    """
    from dataclasses import replace
    return [replace(p, cwd=cwds.get(p.pid, "")) for p in procs]


# --- the impure half ---------------------------------------------------------

def _run(argv: list[str], timeout: float) -> Optional[str]:
    """Run and return stdout, or None. Never raises — a scan may fail a poll."""
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except (subprocess.SubprocessError, OSError) as e:
        log.warning("%s failed: %s", argv[0], e)
        return None
    if r.returncode != 0 and not r.stdout:
        # lsof exits non-zero when *some* pid is gone, while still printing the
        # rest. Only a non-zero exit with no output at all is a real failure.
        log.warning("%s returned %d: %s", argv[0], r.returncode,
                    (r.stderr or "").strip()[:200])
        return None
    return r.stdout


def scan(timeout: float = 5.0) -> list[Proc]:
    """Every agent session process on this machine, with its tty and cwd.

    Never raises: a failure returns [] and the caller renders "no sessions" for
    one poll rather than the daemon dying because `ps` hiccuped.
    """
    raw = _run(["ps", "-Ao", "pid=,ppid=,tty=,command="], timeout)
    if raw is None:
        return []
    procs = parse_ps(raw)
    if not procs:
        return []

    # One batched lsof for every pid rather than one per process: this runs on
    # a poll, and per-process subprocess cost is exactly what the statusline's
    # `own_tty` was rewritten to avoid.
    pids = ",".join(str(p.pid) for p in procs)
    raw_cwd = _run(["lsof", "-a", "-p", pids, "-d", "cwd", "-Fpn"], timeout)
    cwds = parse_lsof_cwd(raw_cwd) if raw_cwd else {}
    if not cwds:
        log.debug("no cwds resolved for %d process(es)", len(procs))
    return attach_cwds(procs, cwds)
