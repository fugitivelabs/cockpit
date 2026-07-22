"""The daemon's local HTTP endpoint — where hooks and the statusline report in.

Claude Code's `http` hook type POSTs the hook payload straight here, with no
shell script in between. The statusline can't do that itself (it's a command),
so a tiny script posts on its behalf — see `statusline.py`.

**Intent lives in the URL, not the payload.** `/hook/blocked` means blocked
because *we configured that matcher to call that path*, rather than because we
parsed a `notification_type` field. Payload field names are Claude Code's to
change; our own URLs are not, and a rename would otherwise silently downgrade
the board to Stage 1 with nothing in the log.

**Safety, and it is the important part of this file.** A hook's HTTP response
can carry a permission *decision* — it can allow or deny a tool call. This
daemon must never do that. Every response here is a bare `200 {}`: enough for
Claude Code to count the hook as succeeded, carrying no decision whatsoever. A
dashboard that could accidentally approve a tool call would be a far worse bug
than a dashboard that shows the wrong color, so the endpoint is built so it
cannot express approval at all.

If the daemon is down, Claude Code treats the connection failure as a
non-blocking error and carries on — so a dead deck never blocks your work.
"""

from __future__ import annotations

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional

from .registry import BLOCKED, NEEDS_INPUT, Registry
from .sessions import Telemetry

log = logging.getLogger("deck.cockpit.listener")

DEFAULT_PORT = 8787
BIND_HOST = "127.0.0.1"          # loopback only; never a listening socket on a LAN
MAX_BODY = 1 << 20               # a hook payload is small; refuse anything wild

# Tools whose "permission request" is really *a question for you*, not a request
# to run something. AskUserQuestion is a tool like any other, so PermissionRequest
# fires for it exactly as it does for Bash — which made every question turn the
# tile red. The tool name is the only thing in the payload that tells them apart.
QUESTION_TOOLS = {"askuserquestion", "asku", "question"}

# path -> flag. `None` clears the flag: the session stopped needing a human.
HOOK_PATHS = {
    "/hook/blocked": BLOCKED,        # Notification: permission_prompt
    "/hook/needs-input": NEEDS_INPUT,  # Notification: agent_needs_input, elicitation
    "/hook/idle": None,              # Notification: idle_prompt — nothing pending
    "/hook/stop": None,              # Stop — turn over
    "/hook/active": None,            # UserPromptSubmit / PostToolUse / PermissionDenied
                                     # — the prompt was answered, one way or another
}


def _telemetry(payload: dict) -> Optional[Telemetry]:
    """Pull the statusline's context/cost block, tolerating absence and nulls.

    Claude Code documents `current_usage` and the percentages as nullable early
    in a session and right after `/compact`, so every field here is optional by
    contract, not defensively.
    """
    ctx = payload.get("context_window") or {}
    cost = payload.get("cost") or {}
    tokens = None
    if isinstance(ctx.get("total_input_tokens"), (int, float)):
        tokens = int(ctx.get("total_input_tokens") or 0) + \
                 int(ctx.get("total_output_tokens") or 0)
    pct = ctx.get("used_percentage")
    usd = cost.get("total_cost_usd")
    if tokens is None and pct is None and usd is None:
        return None
    return Telemetry(
        tokens=tokens,
        cost_usd=float(usd) if isinstance(usd, (int, float)) else None,
        context_pct=float(pct) if isinstance(pct, (int, float)) else None,
    )


class _Handler(BaseHTTPRequestHandler):
    registry: Registry = None        # injected by serve()
    on_change = None                 # optional callback: something changed

    # Silence BaseHTTPRequestHandler's stderr access log; we have real logging.
    def log_message(self, fmt, *args):
        log.debug("http %s", fmt % args)

    def _reply(self, code: int = 200, body: dict = None) -> None:
        raw = json.dumps(body if body is not None else {}).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path == "/health":
            self._reply(200, {"ok": True, "sessions": len(self.registry)})
            return
        if self.path == "/doctor":
            # The daemon's OWN permissions, which are not the same as the ones
            # your terminal has — macOS grants per responsible process, and the
            # LaunchAgent has no Terminal parent to inherit from.
            from .doctor import daemon_self_checks
            try:
                checks = daemon_self_checks()
            except Exception:
                log.exception("self-check failed")
                checks = []
            self._reply(200, {"sessions": len(self.registry), "checks": checks})
            return
        if self.path == "/sessions":
            # Debug view of the join table. The whole Stage 2 design hinges on
            # session_id -> tty -> window, and a join that silently fails looks
            # exactly like a dashboard that just never turns blue — so make it
            # inspectable from outside the process.
            out = []
            for rec in self.registry.snapshot().values():
                out.append({
                    "session_id": rec.session_id,
                    "tty": rec.tty,
                    "cwd": rec.cwd,
                    "flag": rec.flag,
                    "model": rec.model,
                    "context_pct": rec.telemetry.context_pct if rec.telemetry else None,
                    "cost_usd": rec.telemetry.cost_usd if rec.telemetry else None,
                })
            out.sort(key=lambda r: (r["tty"] or "", r["session_id"] or ""))
            self._reply(200, {"sessions": out})
            return
        self._reply(404, {"error": "not found"})

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length > MAX_BODY:
            self._reply(413, {"error": "too large"})
            return
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw or b"{}")
            if not isinstance(payload, dict):
                raise ValueError("payload is not an object")
        except (ValueError, UnicodeDecodeError) as e:
            log.warning("bad payload on %s: %s", self.path, e)
            # Still a 200: a malformed body is our problem to see in the log,
            # not a reason to make Claude Code report a hook failure at the user.
            self._reply(200, {})
            return

        try:
            self._dispatch(payload)
        except Exception:
            log.exception("handler failed for %s", self.path)
        # Always a bare 200 with an empty object — never a permission decision.
        self._reply(200, {})

    def _dispatch(self, payload: dict) -> None:
        session_id = str(payload.get("session_id") or "")
        cwd = str(payload.get("cwd") or "")
        path = self.path.split("?", 1)[0].rstrip("/") or "/"

        if path == "/statusline":
            self.registry.note_statusline(
                session_id,
                tty=str(payload.get("tty") or "") or None,
                cwd=cwd,
                telemetry=_telemetry(payload),
                model=((payload.get("model") or {}).get("display_name") or ""),
            )
        elif path == "/hook/capture":
            # Observation only — never touches state. Exists to answer "what
            # does Claude Code actually send for this event?" without guessing,
            # which is how the permission_prompt/question conflation was found.
            log.info("capture %s: %s",
                     payload.get("hook_event_name") or "?", json.dumps(payload)[:1200])
        elif path in HOOK_PATHS:
            flag = HOOK_PATHS[path]
            tool = str(payload.get("tool_name") or "")
            if tool:
                log.info("%s tool=%s", path, tool)
            # A question is "needs input" (blue), not "blocked on a tool" (red).
            if flag == BLOCKED and tool.lower() in QUESTION_TOOLS:
                flag = NEEDS_INPUT
            # The payload is the only way to learn how Claude Code actually
            # classifies an event — the matcher that routed it here is not
            # echoed back. Kept at DEBUG so it's available when a state looks
            # wrong without spamming a normal day's log.
            log.debug("payload %s: %s", path, json.dumps(payload)[:600])
            self.registry.set_flag(session_id, flag, cwd=cwd)
        else:
            log.warning("unknown endpoint %s", path)
            return

        if callable(self.on_change):
            try:
                self.on_change()
            except Exception:
                log.exception("on_change callback failed")


class ChannelListener:
    """Owns the HTTP server thread. Start it, forget it, stop it on shutdown."""

    def __init__(self, registry: Registry, port: int = DEFAULT_PORT,
                 on_change=None):
        self.registry = registry
        self.port = port
        self._on_change = on_change
        self._server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    def start(self) -> bool:
        """Bind and serve. Returns False if the port is taken — never raises.

        A daemon that dies because something else holds 8787 would be a bad
        trade: the dashboard works without this channel, just with less state.
        """
        handler = type("_BoundHandler", (_Handler,),
                       {"registry": self.registry, "on_change": staticmethod(self._on_change)
                        if callable(self._on_change) else None})
        try:
            self._server = ThreadingHTTPServer((BIND_HOST, self.port), handler)
        except OSError as e:
            log.error("could not bind %s:%d — channels disabled (%s)",
                      BIND_HOST, self.port, e)
            self._server = None
            return False
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever,
                                        name="channels", daemon=True)
        self._thread.start()
        log.info("channel listener up on http://%s:%d", BIND_HOST, self.port)
        return True

    def stop(self) -> None:
        if self._server is not None:
            try:
                self._server.shutdown()
                self._server.server_close()
            except Exception:
                log.debug("listener shutdown was not clean", exc_info=True)
            self._server = None
