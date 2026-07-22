"""cockpit — the always-on consumer built on the `deck` library.

Stage 0.5 is the lifecycle skeleton: it composes the four library primitives
(single-instance guard, structured logging, graceful shutdown, per-component
fault isolation) into a supervisable daemon and gives the LaunchAgent something
real to run. Stage 1 grows the placeholder view into the session dashboard.

All Claude/homelab knowledge belongs here, never in `deck/`.
"""

from .daemon import CockpitDaemon, main

__all__ = ["CockpitDaemon", "main"]
