"""Auto-sync scheduler for live connectors.

Manages periodic re-ingestion of sources that have live API credentials.
Each source can be scheduled independently with a configurable interval.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import datetime

logger = logging.getLogger(__name__)


class SyncStatus:
    """Status of a single source sync."""

    def __init__(self, source_name: str):
        self.source_name = source_name
        self.last_sync: datetime | None = None
        self.last_error: str | None = None
        self.item_count: int = 0
        self.syncing: bool = False
        self.interval_minutes: int = 60

    def to_dict(self) -> dict:
        return {
            "source_name": self.source_name,
            "last_sync": self.last_sync.isoformat() if self.last_sync else None,
            "last_error": self.last_error,
            "item_count": self.item_count,
            "syncing": self.syncing,
            "interval_minutes": self.interval_minutes,
        }


class SyncScheduler:
    """Schedules periodic sync for connected sources."""

    def __init__(self):
        self._schedules: dict[str, SyncStatus] = {}
        self._timers: dict[str, threading.Timer] = {}
        self._sync_fn: Callable[[str], int] | None = None
        self._lock = threading.Lock()

    def set_sync_function(self, fn: Callable[[str], int]) -> None:
        """Set the function called to sync a source. Returns item count."""
        self._sync_fn = fn

    def schedule(self, source_name: str, interval_minutes: int = 60) -> SyncStatus:
        """Schedule periodic sync for a source."""
        with self._lock:
            if source_name in self._schedules:
                status = self._schedules[source_name]
                status.interval_minutes = interval_minutes
            else:
                status = SyncStatus(source_name)
                status.interval_minutes = interval_minutes
                self._schedules[source_name] = status

            # Cancel existing timer
            self._cancel_timer(source_name)

            # Start new timer
            self._start_timer(source_name, interval_minutes)

            return status

    def unschedule(self, source_name: str) -> None:
        """Stop scheduled sync for a source."""
        with self._lock:
            self._cancel_timer(source_name)
            self._schedules.pop(source_name, None)

    def sync_now(self, source_name: str) -> SyncStatus:
        """Trigger an immediate sync for a source."""
        status = self._schedules.get(source_name)
        if not status:
            status = SyncStatus(source_name)
            self._schedules[source_name] = status

        self._do_sync(source_name)
        return status

    def get_status(self, source_name: str | None = None) -> dict | list[dict]:
        """Get sync status for one or all sources."""
        with self._lock:
            if source_name:
                status = self._schedules.get(source_name)
                return status.to_dict() if status else {"source_name": source_name, "last_sync": None}
            return [s.to_dict() for s in self._schedules.values()]

    def stop_all(self) -> None:
        """Cancel all scheduled syncs."""
        with self._lock:
            for name in list(self._timers.keys()):
                self._cancel_timer(name)
            self._schedules.clear()

    def _start_timer(self, source_name: str, interval_minutes: int) -> None:
        """Start a repeating timer for a source."""
        interval_seconds = interval_minutes * 60

        def run():
            self._do_sync(source_name)
            # Reschedule
            with self._lock:
                if source_name in self._schedules:
                    self._start_timer(source_name, interval_minutes)

        timer = threading.Timer(interval_seconds, run)
        timer.daemon = True
        timer.start()
        self._timers[source_name] = timer

    def _cancel_timer(self, source_name: str) -> None:
        """Cancel timer for a source."""
        timer = self._timers.pop(source_name, None)
        if timer:
            timer.cancel()

    def _do_sync(self, source_name: str) -> None:
        """Execute sync for a source."""
        status = self._schedules.get(source_name)
        if not status:
            return

        if status.syncing:
            logger.debug("Sync already in progress for %s, skipping", source_name)
            return

        if not self._sync_fn:
            logger.warning("No sync function configured")
            return

        status.syncing = True
        try:
            item_count = self._sync_fn(source_name)
            status.last_sync = datetime.now()
            status.item_count = item_count
            status.last_error = None
            logger.info("Sync complete for %s: %d items", source_name, item_count)
        except Exception as e:
            status.last_error = str(e)
            logger.warning("Sync failed for %s: %s", source_name, e, exc_info=True)
        finally:
            status.syncing = False


# Global scheduler instance
scheduler = SyncScheduler()
