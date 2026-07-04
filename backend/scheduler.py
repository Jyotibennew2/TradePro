"""
TradePro Backend - Background Scheduler
Refresh quotes, option chain, run scanners, cleanup logs.
Compatible with Python 3.11+, Termux, Linux.
"""

import time
import threading
import logging
from typing import Callable, Optional

logger = logging.getLogger(__name__)


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
                if now - task.last_run >= task.interval:
                    try:
                        task.func()
                        task.last_run  = now
                        task.run_count += 1
                        logger.debug(f"Scheduler: '{task.name}' ran (#{task.run_count})")
                    except Exception as e:
                        task.errors += 1
                        logger.error(f"Scheduler: '{task.name}' error: {e}")
            time.sleep(1)

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def status(self) -> list[dict]:
        """Return status of all tasks."""
        return [
            {
                "name"      : t.name,
                "interval"  : t.interval,
                "enabled"   : t.enabled,
                "run_count" : t.run_count,
                "errors"    : t.errors,
                "last_run"  : round(time.monotonic() - t.last_run, 1) if t.last_run else None,
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
