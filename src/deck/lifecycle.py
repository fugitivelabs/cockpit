"""Lifecycle primitives — what "always-on" needs beyond drawing to keys.

Rendering and input are verified; this is the operational layer that turns a
foreground script into a supervisable daemon. Two things live here because they
are use-case-agnostic and shared by every consumer:

    configure_logging()   opt-in log setup for a headless process
    SingleInstance        refuse a second copy that would fight over the device

Per-component fault isolation lives in app.py/render.py (it is a rendering
concern) and SIGTERM handling lives in surface.py (it owns the device). See
../../docs/operations.md for the design and honest status of each.
"""

from __future__ import annotations

import atexit
import logging
import os
import tempfile
from typing import Optional

# The library follows the standard convention: modules only ever call
# logging.getLogger(__name__) and never configure handlers themselves. A
# consumer opts into output by calling configure_logging(); until it does, the
# NullHandler installed in __init__ keeps the stdlib from printing the
# "No handlers could be found" warning.

_LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def configure_logging(level: int = logging.INFO,
                      logfile: Optional[str] = None,
                      stream: bool = True,
                      name: str = "deck") -> logging.Logger:
    """Set up a top-level logger tree for a headless process.

    Levelled + timestamped, so a daemon under launchd is debuggable from its
    log file alone. Idempotent: repeated calls re-point handlers rather than
    stacking duplicates, so a restart in the same process never doubles lines.

    - `level` — threshold for the whole `<name>.*` tree.
    - `logfile` — append structured lines here (created if missing). This is the
      LaunchAgent's `StandardErrorPath` target when run headless.
    - `stream` — also emit to stderr (useful in the foreground; harmless under
      launchd, which captures stderr to the same log).
    - `name` — which tree to configure. Defaults to `deck`, but a host driving
      more than one library logs them the same way by calling this once per
      tree: handlers attach per-tree and each sets `propagate = False`, so
      configuring `deck` and `fleet` separately is the supported shape rather
      than a workaround. A tree left unconfigured keeps its NullHandler and
      goes silent — which is how a moved module quietly loses its log lines.
    """
    logger = logging.getLogger(name)
    logger.setLevel(level)

    # Drop only the handlers we own, so a second call is a clean reconfigure and
    # any handler a host app attached to "deck" is left alone.
    for h in list(logger.handlers):
        if getattr(h, "_deck_owned", False):
            logger.removeHandler(h)
            h.close()

    fmt = logging.Formatter(_LOG_FORMAT, _DATE_FORMAT)

    if stream:
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        sh._deck_owned = True  # type: ignore[attr-defined]
        logger.addHandler(sh)

    if logfile:
        fh = logging.FileHandler(logfile)
        fh.setFormatter(fmt)
        fh._deck_owned = True  # type: ignore[attr-defined]
        logger.addHandler(fh)

    # Handlers on "deck" carry output; stop it also bubbling to the root logger
    # (which a host app may have configured), avoiding doubled lines.
    logger.propagate = False
    return logger


class AlreadyRunning(RuntimeError):
    """Raised when another instance already holds the lock."""


class SingleInstance:
    """Advisory single-instance lock backed by ``flock``.

    Only one process may drive the USB device — a second writer just fights over
    the display (verified: macOS does not hard-lock the device). This guards
    against that by holding an exclusive advisory lock on a well-known file for
    the life of the process.

    ``flock`` is the right primitive here: the lock is tied to the open file
    description, so the kernel releases it automatically when the process exits
    — including on crash or SIGKILL, where an atexit/PID-file scheme would leave
    a stale lock behind and refuse every future start.

        with SingleInstance("cockpit"):
            run_the_daemon()

    or, to fail loudly with a helpful message:

        lock = SingleInstance("cockpit")
        if not lock.acquire():
            sys.exit(f"already running (pid {lock.holder_pid()})")
    """

    def __init__(self, name: str = "cockpit",
                 directory: Optional[str] = None):
        directory = directory or tempfile.gettempdir()
        self.path = os.path.join(directory, f"{name}.lock")
        self._fd: Optional[int] = None
        self._log = logging.getLogger(__name__)

    def acquire(self) -> bool:
        """Try to take the lock. Returns True on success, False if held elsewhere.

        Safe to call once; a second call while already held is a no-op that
        returns True.
        """
        if self._fd is not None:
            return True
        import fcntl

        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError):
            os.close(fd)
            self._log.warning("another instance already holds %s", self.path)
            return False

        # Record our pid for the next starter to read; truncate first so a
        # shorter pid never leaves trailing digits from a previous holder.
        os.ftruncate(fd, 0)
        os.write(fd, f"{os.getpid()}\n".encode())
        self._fd = fd
        atexit.register(self.release)
        self._log.debug("acquired single-instance lock %s", self.path)
        return True

    def acquire_or_raise(self) -> "SingleInstance":
        if not self.acquire():
            raise AlreadyRunning(
                f"another instance already holds {self.path} "
                f"(pid {self.holder_pid()})")
        return self

    def holder_pid(self) -> Optional[int]:
        """Best-effort read of the pid recorded in the lock file, or None."""
        try:
            with open(self.path) as f:
                return int(f.read().strip())
        except (OSError, ValueError):
            return None

    def release(self) -> None:
        if self._fd is None:
            return
        import fcntl

        try:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
        except OSError:
            pass
        try:
            os.close(self._fd)
        except OSError:
            pass
        self._fd = None

    def __enter__(self) -> "SingleInstance":
        return self.acquire_or_raise()

    def __exit__(self, *exc) -> bool:
        self.release()
        return False
