"""The cockpit daemon — the always-on process that owns the deck.

Stage 1: the deck shows the **read-only session dashboard**. One key per Claude
Code session, colored by state, press to jump to that window. The dashboard
itself lives in `dashboard.py`; this module is the process around it — the part
that makes it survivable rather than a script you babysit.

What it composes, all four lifecycle primitives at once:

  - single-instance guard   — refuses to start if another copy holds the lock
  - structured logging       — everything goes to a timestamped logfile
  - graceful shutdown        — SIGTERM blanks the deck and releases the device
  - per-component isolation   — a component that raises shows an error tile,
                               rather than taking the daemon down (App/View)

Stage 0.5's placeholder heartbeat view is still here as `--heartbeat`: it needs
no Terminal automation permission, so it stays the way to answer "is the device
path itself healthy?" without the dashboard in the picture.

Nothing device-specific is proven by tests — this earns "done" by uptime (see
../../docs/operations.md).

    PYTHONPATH=. ./.venv/bin/python -m cockpit
"""

from __future__ import annotations

import argparse
import logging
import os
import time

from deck import (
    App,
    DeckUnavailable,
    Live,
    SingleInstance,
    Slot,
    Static,
    Surface,
    View,
    configure_logging,
)
from deck.surface import TOUCH_LEFT, TOUCH_RIGHT, DeviceManager

from .actions import default_bar
from .claude_code import ClaudeCodeAdapter
from .dashboard import Dashboard
from .listener import DEFAULT_PORT, ChannelListener
from .registry import Registry

log = logging.getLogger("deck.cockpit")

INSTANCE_NAME = "cockpit"
DEFAULT_LOG = os.path.expanduser("~/Library/Logs/cockpit.log")
HEARTBEAT_FILE = os.path.expanduser("~/Library/Logs/cockpit.heartbeat")
HEARTBEAT_EVERY_S = 15.0

PAGE_GLOW = (0, 90, 140)
NO_GLOW = (0, 0, 0)


def build_view(started_at: float) -> View:
    """The Stage 0.5 heartbeat surface — kept as the device-only fallback.

    A ticking clock and a live uptime prove the loop is alive; a pulsing dot
    proves the render path keeps firing. It touches no other app, so when the
    dashboard looks wrong this answers "is it the deck, or is it Terminal?"
    """
    def uptime() -> Slot:
        secs = int(time.monotonic() - started_at)
        h, rem = divmod(secs, 3600)
        m, s = divmod(rem, 60)
        text = f"{h}h{m:02d}m" if h else (f"{m}m{s:02d}s" if m else f"{s}s")
        return Slot(label="up", sub=text, bg="#101820", accent="#4CD964")

    def clock() -> Slot:
        return Slot(label=time.strftime("%H:%M"), sub=time.strftime("%S"),
                    bg="#12263A", accent="#3FA7D6")

    def pulse() -> Slot:
        on = int(time.monotonic()) % 2 == 0
        return Slot(label="cockpit", sub="heartbeat",
                    bg="#141414", accent="#3FA7D6" if on else "#1E3A4A")

    return View([
        Live(clock),
        Live(uptime),
        Live(pulse),
        Static(Slot(label="lifecycle", sub="skeleton", bg="#141414", fg="#888")),
    ])


class CockpitDaemon(App):
    """App plus an out-of-band heartbeat, so liveness is checkable externally."""

    def __init__(self, started_at: float, surface: Surface,
                 dashboard: Dashboard | None = None,
                 heartbeat_file: str = HEARTBEAT_FILE):
        view = dashboard.view if dashboard else build_view(started_at)
        # Paint twice a second: an unchanged board costs a signature compare
        # and zero USB writes (Surface diffs), so the only thing this buys is
        # a fresh poll reaching the keys sooner.
        super().__init__(surface=surface, view=view, interval=0.5)
        self._started_at = started_at
        self._dashboard = dashboard
        self._heartbeat_file = heartbeat_file
        self._last_heartbeat = 0.0
        self._glow: tuple | None = None
        if dashboard:
            self.info(dashboard.info)

    def _paint(self) -> None:
        if self._dashboard:
            # Cheap: compares a signature and only rebuilds tiles when the
            # session set actually changed. The polling itself is on its own
            # thread — nothing here does I/O.
            self._dashboard.refresh()
            self._sync_touch_glow()
        super()._paint()
        self._maybe_heartbeat()

    def _sync_touch_glow(self) -> None:
        """Light the touch points only while there is somewhere to page to.

        App.run() sets this once at startup; the session count moves all day,
        so the dashboard has to keep it honest.
        """
        want = PAGE_GLOW if self._dashboard.pages > 1 else NO_GLOW
        if want == self._glow:
            return
        self._glow = want
        self.surface.set_touch(TOUCH_LEFT, want)
        self.surface.set_touch(TOUCH_RIGHT, want)

    def _maybe_heartbeat(self) -> None:
        now = time.monotonic()
        if now - self._last_heartbeat < HEARTBEAT_EVERY_S:
            return
        self._last_heartbeat = now
        up = int(now - self._started_at)
        log.info("heartbeat — up %ds", up)
        try:
            with open(self._heartbeat_file, "w") as f:
                f.write(f"{int(time.time())} up={up}\n")
        except OSError as e:
            log.warning("could not write heartbeat file: %s", e)

    def _route(self, index: int, long: bool) -> None:
        log.info("press key=%d long=%s", index, long)
        super()._route(index, long)


def wait_for_device(stop_after_s: float = 30.0, poll_s: float = 2.0) -> bool:
    """Poll for the Neo so a brief USB-enumeration delay at login isn't a crash.

    Returns True as soon as a device appears. If none shows within the window we
    return False and let the caller exit; launchd's KeepAlive+ThrottleInterval
    then retries, which is the right supervisor for a device that stays absent.
    """
    deadline = time.monotonic() + stop_after_s
    while time.monotonic() < deadline:
        try:
            if DeviceManager().enumerate():
                return True
        except Exception as e:
            log.debug("enumerate() failed while waiting: %s", e)
        time.sleep(poll_s)
    return False


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="cockpit", description=__doc__)
    ap.add_argument("--logfile", default=os.environ.get("COCKPIT_LOG", DEFAULT_LOG),
                    help="where to append structured logs")
    ap.add_argument("--debug", action="store_true", help="verbose logging")
    ap.add_argument("--brightness", type=int, default=70)
    ap.add_argument("--no-single-instance", action="store_true",
                    help="skip the single-instance guard (testing only)")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT,
                    help="local port for hook/statusline channels (0 disables)")
    ap.add_argument("--heartbeat", action="store_true",
                    help="run the Stage 0.5 heartbeat view instead of the "
                         "dashboard (device-only; no Terminal automation)")
    args = ap.parse_args(argv)

    configure_logging(level=logging.DEBUG if args.debug else logging.INFO,
                      logfile=args.logfile, stream=True)
    log.info("cockpit starting (pid %d)", os.getpid())

    lock = SingleInstance(INSTANCE_NAME)
    if not args.no_single_instance and not lock.acquire():
        log.error("another cockpit already holds the lock (pid %s) — exiting",
                  lock.holder_pid())
        return 1

    if not wait_for_device():
        log.error("no Stream Deck found — exiting for launchd to retry")
        return 1

    started_at = time.monotonic()
    surface = Surface(brightness=args.brightness)
    try:
        surface.open()
    except DeckUnavailable as e:
        log.error("could not claim the device: %s", e)
        return 1

    dashboard = None
    listener = None
    if not args.heartbeat:
        # The channels registry is shared: the listener writes what hooks and
        # the statusline report, the adapter joins it onto windows by tty.
        registry = Registry()
        # Adapter #1. When there is a second, this becomes a small registry —
        # not before (see the config note in ../../docs/architecture.md).
        adapter = ClaudeCodeAdapter(registry=registry)
        dashboard = Dashboard(
            adapter, prompt_reader=adapter.read_prompt,
            # The screen is the only evidence a *denial* ever produces: no hook
            # fires when the tool never runs. Session-scoped because the prompt
            # is gone regardless of which tool raised it.
            on_prompt_gone=lambda s: registry.set_flag(
                s.session_id, None, scope="session") if s.session_id else None)
        if args.port:
            # A hook firing wakes the poller immediately instead of the board
            # waiting out the interval.
            listener = ChannelListener(registry, port=args.port,
                                       on_change=dashboard.poller.request_poll)
            if not listener.start():
                # Losing the port costs live state, not the board — Stage 1
                # behaviour is the floor, and the log already said why.
                listener = None
        dashboard.set_actions(default_bar(dashboard, surface))
        dashboard.start()
        log.info("dashboard up — %d session(s) on first poll",
                 len(dashboard.sessions))

    daemon = CockpitDaemon(started_at, surface, dashboard)
    log.info("cockpit up — deck claimed, entering run loop")
    try:
        # App.run() installs the SIGTERM/SIGINT handlers (main thread) and
        # blanks + releases the deck on the way out.
        daemon.run()
        log.info("cockpit stopped cleanly")
        return 0
    except Exception:
        log.exception("cockpit crashed in the run loop")
        return 1
    finally:
        if listener:
            listener.stop()
        if dashboard:
            dashboard.stop()
        # Belt and suspenders: run() already releases on a signalled stop, but
        # guarantee the device is freed on every exit path (close is idempotent).
        try:
            surface.close()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
