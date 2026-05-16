"""
OpenEye data retention.

Default: OFF. Local SQLite grows forever unless OPENEYE_RETAIN_DAYS is set
to a positive integer. When enabled, a background worker prunes sessions,
messages, frames, step_verifications, and unsynced trajectories older
than the cutoff once a day.

Protected from pruning:
  - Skills (procedural memory you curated)
  - Tenant opt-in roster (consent state)
  - Trajectories already shipped to Context (the marker proves history)

Manual prune via:
    POST /retention/prune-now
"""

import logging
import os
import threading
import time
from typing import Dict, Optional

from state import get_db

logger = logging.getLogger(__name__)

RETAIN_DAYS = int(os.getenv("OPENEYE_RETAIN_DAYS", "0"))   # 0 = disabled
RUN_INTERVAL_SECONDS = int(os.getenv("OPENEYE_RETAIN_INTERVAL", "86400"))  # 24h


def is_enabled() -> bool:
    return RETAIN_DAYS > 0


def prune_now() -> Dict:
    """Prune all data older than RETAIN_DAYS. Returns count summary."""
    if not is_enabled():
        return {"enabled": False, "deleted": {}}
    cutoff = time.time() - (RETAIN_DAYS * 86400)
    deleted = get_db().prune_older_than(cutoff)
    total = sum(deleted.values())
    if total:
        logger.info("Retention pruned %d rows older than %d days: %s",
                    total, RETAIN_DAYS, deleted)
    return {"enabled": True, "cutoff_epoch": cutoff, "deleted": deleted}


class RetentionWorker(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True, name="openeye-retention")
        self._stop_event = threading.Event()

    def run(self):
        if not is_enabled():
            logger.debug("Retention disabled (OPENEYE_RETAIN_DAYS=0)")
            return
        logger.info("Retention worker started (retain=%d days, interval=%ds)",
                    RETAIN_DAYS, RUN_INTERVAL_SECONDS)
        # First pass after a short delay so we don't slow startup
        if not self._stop_event.wait(60):
            try:
                prune_now()
            except Exception as e:
                logger.warning("Retention initial pass error: %s", e)
        # Then run on the configured interval
        while not self._stop_event.wait(RUN_INTERVAL_SECONDS):
            try:
                prune_now()
            except Exception as e:
                logger.warning("Retention worker error: %s", e)

    def stop(self):
        self._stop_event.set()


_worker: Optional[RetentionWorker] = None


def start_retention_worker():
    global _worker
    if _worker and _worker.is_alive():
        return
    _worker = RetentionWorker()
    _worker.start()


def stop_retention_worker():
    global _worker
    if _worker:
        _worker.stop()
        _worker = None
