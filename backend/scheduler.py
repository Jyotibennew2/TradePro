"""
TradePro Backend - Background Scheduler
Refresh quotes, option chain, run scanners, cleanup logs.
Compatible with Python 3.11+, Termux, Linux.

── Backoff pass ────────────────────────────────────────────────────────
A task's func may now optionally return True/False to report whether its
work actually succeeded (e.g. the underlying API call returned success).
Returning None (or nothing) is treated as success, so existing tasks that
don't report a result (like cache_cleanup) are unaffected.

After FAILURE_THRESHOLD consecutive False results, the scheduler stops
retrying that task at its normal interval and instead pauses it for a
cooldown period that doubles on each further consecutive failure (capped
at MAX_BACKOFF_SECONDS). A single success immediately clears the backoff
and resumes the task's normal interval.

This exists specifically to stop a "retry storm": previously, if Fyers
returned a rate-limit/auth error, the scheduler kept calling that task
again at its normal (often just a few seconds) interval indefinitely,
which both wastes calls and can prolong an active rate-limit window
instead of letting it clear.
"""

import time
import threading
import logging
from typing import Callable, Optional

logger = logging.getLogger(__name__)

FAILURE_THRESHOLD   = 3     # consecutive failures before backoff kicks in
BASE_BACKOFF_SECONDS  = 60    # first cooldown once threshold is crossed
MAX_BACKOFF_SECONDS   = 600   # cooldown never grows past this (10 min)


# ---------------------------------------------------------------------------
# Task container
# ---------------------------------------------------------------------------

class ScheduledTask:
    def __init__(
        self,
        name    : str,
        func    : Callable,
        interval: int,
        enabled : bool = True,
    ) -> None:
        self.name      = name
        self.func      = func
        self.interval  = interval
        self.enabled   = enabled
        self.last_run  = 0.0
        self.run_count = 0
        self.errors    = 0
        self.consecutive_failures = 0
        self.backoff_until        = 0.0   # monotonic timestamp; skip runs until past this


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------

class Scheduler:
    """
    Simple background task scheduler.
    Runs tasks in a single daemon thread at specified intervals.
    """

    def __init__(self) -> None:
        self._tasks  : list[ScheduledTask] = []
        self._running: bool                = False
        self._thread : Optional[threading.Thread] = None

    # ------------------------------------------------------------------
    # Register tasks
    # ------------------------------------------------------------------

    def add_task(
        self,
        name    : str,
        func    : Callable,
        interval: int,
        enabled : bool = True,
    ) -> None:
        """Add a task to the scheduler."""
        task = ScheduledTask(name=name, func=func, interval=interval, enabled=enabled)
        self._tasks.append(task)
        logger.info(f"Scheduler: task registered '{name}' every {interval}s")

    # ------------------------------------------------------------------
    # Start / Stop
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the scheduler in a background daemon thread."""
        if self._running:
            logger.warning("Scheduler already running")
            return
        self._running = True
        self._thread  = threading.Thread(
            target=self._loop,
            name="TradePro-Scheduler",
            daemon=True,
        )
        self._thread.start()
        logger.info("Scheduler started")

    def stop(self) -> None:
        """Stop the scheduler."""
        self._running = False
        logger.info("Scheduler stopped")

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def _loop(self) -> None:
        while self._running:
            now = time.monotonic()
            for task in self._tasks:
                if not task.enabled:
                    continue
                if task.backoff_until and now < task.backoff_until:
                    continue
                if now - task.last_run >= task.interval:
                    try:
                        result = task.func()
                        task.last_run  = now
                        task.run_count += 1
                        logger.debug(f"Scheduler: '{task.name}' ran (#{task.run_count})")
                        self._record_outcome(task, now, failed=(result is False))
                    except Exception as e:
                        task.errors += 1
                        logger.error(f"Scheduler: '{task.name}' error: {e}")
                        self._record_outcome(task, now, failed=True)
            time.sleep(1)

    def _record_outcome(self, task: ScheduledTask, now: float, failed: bool) -> None:
        if failed:
            task.consecutive_failures += 1
            if task.consecutive_failures >= FAILURE_THRESHOLD:
                backoff = min(
                    MAX_BACKOFF_SECONDS,
                    BASE_BACKOFF_SECONDS * (2 ** (task.consecutive_failures - FAILURE_THRESHOLD)),
                )
                task.backoff_until = now + backoff
                logger.warning(
                    f"Scheduler: '{task.name}' failed {task.consecutive_failures}x in a row — "
                    f"backing off for {backoff:.0f}s"
                )
        else:
            if task.consecutive_failures:
                logger.info(f"Scheduler: '{task.name}' recovered after {task.consecutive_failures} failure(s)")
            task.consecutive_failures = 0
            task.backoff_until        = 0.0

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def status(self) -> list[dict]:
        """Return status of all tasks."""
        now = time.monotonic()
        return [
            {
                "name"      : t.name,
                "interval"  : t.interval,
                "enabled"   : t.enabled,
                "run_count" : t.run_count,
                "errors"    : t.errors,
                "last_run"  : round(now - t.last_run, 1) if t.last_run else None,
                "consecutive_failures": t.consecutive_failures,
                "backoff_remaining"   : round(t.backoff_until - now, 1) if t.backoff_until and t.backoff_until > now else 0,
            }
            for t in self._tasks
        ]

    def enable(self, name: str) -> bool:
        for t in self._tasks:
            if t.name == name:
                t.enabled = True
                logger.info(f"Scheduler: '{name}' enabled")
                return True
        return False

    def disable(self, name: str) -> bool:
        for t in self._tasks:
            if t.name == name:
                t.enabled = False
                logger.info(f"Scheduler: '{name}' disabled")
                return True
        return False


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

scheduler = Scheduler()
