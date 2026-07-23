"""Reading a session's own transcript — what it is doing, and what it is called.

Claude Code writes every session to `~/.claude/projects/<encoded-cwd>/<id>.jsonl`
as it goes. That file is a better source for two things than the window title we
read today:

- **the task.** The title carries a truncated fragment ("Evaluate session"); the
  transcript carries Claude Code's own `aiTitle` ("Evaluate session management
  abstraction as potential library"). It also carries `gitBranch` and
  `permissionMode`, which a title cannot express at all.
- **working vs idle**, from the shape of the tail rather than from a spinner
  glyph that only Terminal.app renders where we can see it.

**What it CANNOT tell us, and this is the load-bearing limit.** A tool call that
is *executing* and one that is *waiting for you to approve it* look identical
here: both are an assistant message ending in `tool_use` with no result yet
(verified 2026-07-23). So the transcript can say "a turn is in flight" and never
"Claude is blocked on you" — which is precisely the distinction the deck exists
to show. `blocked` stays hook-driven, exactly as `registry.py` argues. This
module deliberately returns `working` for both and lets `fuse_state` decide.

**Format stability.** This file is Claude Code's internal format, documented as
unstable and versioned per entry. Every read here is therefore total: an
unparseable line is skipped, a missing key degrades that one field to None, and
nothing raises. Losing a title costs a tile its subtitle; it must never cost the
daemon its poll.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger("fleet.transcript")

PROJECTS_ROOT = os.path.expanduser("~/.claude/projects")

# Only the tail is read. These files reach thousands of entries and we poll, so
# reading whole files for seven sessions every few seconds is not affordable.
# 256 KiB comfortably spans the recent `ai-title` / `last-prompt` records (which
# Claude Code re-emits throughout a session) plus the messages that decide state.
TAIL_BYTES = 256 * 1024

# A dangling `tool_use` at the tail means "working" — unless nothing has been
# written for this long, in which case the session died mid-turn and calling it
# working forever would pin a lie to the top of the board.
STALE_AFTER_S = 900.0


@dataclass(frozen=True)
class TranscriptMeta:
    """What one transcript says about its session. Every field may be absent."""

    path: str
    session_id: Optional[str] = None
    state: str = "idle"                  # "working" | "idle" — never "blocked"
    ai_title: Optional[str] = None       # Claude Code's own session title
    last_prompt: Optional[str] = None
    git_branch: Optional[str] = None
    permission_mode: Optional[str] = None
    cwd: Optional[str] = None
    mtime: float = 0.0


def project_dir_name(cwd: str) -> str:
    """`/Users/g/Projects/cockpit/.claude/wt` -> `-Users-g-Projects-cockpit--claude-wt`.

    Claude Code encodes the working directory into a directory name by replacing
    every non-alphanumeric character with `-`. That is lossy and deliberately not
    inverted anywhere here: two different paths can encode to one name, so we
    only ever go cwd -> directory, never back.
    """
    return re.sub(r"[^a-zA-Z0-9]", "-", cwd or "")


def transcripts_for(cwd: str, root: str = PROJECTS_ROOT) -> list[str]:
    """Transcript paths for a working directory, newest first.

    Newest-first matters: a cwd accumulates a file per session over months, and
    the live one is the one being written. Callers that know a `session_id`
    should match on it rather than trusting this order — two sessions sharing a
    cwd is the normal case that broke labelling in the first place.
    """
    d = os.path.join(root, project_dir_name(cwd))
    try:
        names = [n for n in os.listdir(d) if n.endswith(".jsonl")]
    except OSError:
        return []
    paths = [os.path.join(d, n) for n in names]
    paths.sort(key=lambda p: _mtime(p), reverse=True)
    return paths


# session_id -> transcript path. A found transcript never moves, so this is a
# pure win; a miss is not cached, because the file appears as soon as the
# session writes its first entry.
_PATH_CACHE: dict[str, str] = {}


def find(session_id: str, cwd_hint: str = "",
         root: str = PROJECTS_ROOT) -> Optional[str]:
    """The transcript for a session id, wherever it actually lives.

    **The session id is the only safe key, and cwd is a hint at best.** Three
    ways deriving the path from the current working directory goes wrong, all
    observed live on 2026-07-23:

      - **two sessions share a cwd.** Picking the newest `.jsonl` in that
        directory hands both of them the same transcript, so one tile shows the
        other's task. This is the same collision that shaped the labeling rule.
      - **mtime does not identify the live one.** A directory accumulates a file
        per session; one desk had a 40-day-old process whose newest neighbouring
        transcript was 22 days old.
      - **the directory is keyed on the cwd the session STARTED in.** `cd`
        somewhere else, or rename the project directory, and the encoded name no
        longer matches — one live session's transcript was simply absent from
        the directory its process now reports.

    So: try the hint (one `stat`, right nearly always), then fall back to
    searching the project directories for the file named after this session.
    """
    if not session_id:
        return None
    hit = _PATH_CACHE.get(session_id)
    if hit and os.path.exists(hit):
        return hit

    name = f"{session_id}.jsonl"
    if cwd_hint:
        candidate = os.path.join(root, project_dir_name(cwd_hint), name)
        if os.path.exists(candidate):
            _PATH_CACHE[session_id] = candidate
            return candidate

    try:
        dirs = os.listdir(root)
    except OSError:
        return None
    for d in dirs:
        candidate = os.path.join(root, d, name)
        if os.path.exists(candidate):
            _PATH_CACHE[session_id] = candidate
            return candidate
    return None


def _mtime(path: str) -> float:
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def read_tail(path: str, limit: int = TAIL_BYTES) -> list[dict]:
    """The last `limit` bytes of a JSONL file, as parsed objects.

    The first line of the window is almost always a fragment of a longer line,
    so it is dropped whenever we did not start at byte 0. Every other
    unparseable line is skipped in silence: a half-written final line is the
    normal state of a file being appended to while we read it.
    """
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            start = max(0, size - limit)
            if start:
                f.seek(start)
            blob = f.read()
    except OSError as e:
        log.debug("could not read %s: %s", path, e)
        return []

    lines = blob.split(b"\n")
    if start and lines:
        lines = lines[1:]
    out = []
    for raw in lines:
        if not raw.strip():
            continue
        try:
            obj = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def classify(entries: list[dict], age_s: float = 0.0) -> str:
    """`working` or `idle` from the tail. Pure — the rule worth testing.

    Walks back to the last *conversational* entry and reads the shape:

      - assistant whose `stop_reason` is `tool_use`  -> a tool was requested and
        no result has landed. The turn is in flight.
      - a user entry carrying a `tool_result`        -> the model is about to
        continue. Also in flight.
      - anything else (an assistant that ended its turn, a bare user prompt
        awaiting a reply that has not started)       -> idle.

    Metadata entries (`ai-title`, `mode`, `file-history-*`) are skipped: they are
    emitted constantly and say nothing about whether a turn is running. Reading
    the literal last line instead is what would make every session look idle.
    """
    if age_s > STALE_AFTER_S:
        return "idle"
    for d in reversed(entries):
        kind = d.get("type")
        if kind not in ("assistant", "user"):
            continue
        msg = d.get("message")
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        blocks = [c.get("type") for c in content
                  if isinstance(c, dict)] if isinstance(content, list) else []
        if kind == "assistant":
            if msg.get("stop_reason") == "tool_use" or "tool_use" in blocks:
                return "working"
            return "idle"
        # A user entry is either a fresh prompt or the result of a tool call.
        if "tool_result" in blocks:
            return "working"
        return "idle"
    return "idle"


def read(path: str, limit: int = TAIL_BYTES) -> Optional[TranscriptMeta]:
    """Everything one transcript can tell us. None only if it is unreadable.

    Later records win for every field, because Claude Code re-emits `ai-title`
    and `last-prompt` as a session evolves and the newest is the true one.
    """
    entries = read_tail(path, limit)
    if not entries:
        return None
    mtime = _mtime(path)
    import time
    age = max(0.0, time.time() - mtime) if mtime else 0.0

    meta = {"session_id": None, "ai_title": None, "last_prompt": None,
            "git_branch": None, "permission_mode": None, "cwd": None}
    for d in entries:
        kind = d.get("type")
        if kind == "ai-title" and d.get("aiTitle"):
            meta["ai_title"] = str(d["aiTitle"])
        elif kind == "last-prompt" and d.get("lastPrompt"):
            meta["last_prompt"] = str(d["lastPrompt"])
        sid = d.get("sessionId") or d.get("session_id")
        if sid:
            meta["session_id"] = str(sid)
        if d.get("gitBranch"):
            meta["git_branch"] = str(d["gitBranch"])
        if d.get("permissionMode"):
            meta["permission_mode"] = str(d["permissionMode"])
        if d.get("cwd"):
            meta["cwd"] = str(d["cwd"])

    return TranscriptMeta(path=path, state=classify(entries, age),
                          mtime=mtime, **meta)
