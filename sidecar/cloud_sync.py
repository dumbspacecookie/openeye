"""
OpenEye Cloud Sync
Opt-in data streaming to the OpenEye cloud platform.
Users enable this by setting OPENEYE_CLOUD_URL and OPENEYE_CLOUD_KEY.

The receiving endpoint MUST implement:
    POST {OPENEYE_CLOUD_URL}/ingest/{table}
    Headers: Authorization: Bearer <key>, X-OpenEye-Batch-Id: <uuid>
    Body:    JSON array of row dicts
    Success: HTTP 200 or 201 (idempotent — same batch id may be retried)
    Retry:   HTTP 408, 429, 5xx, or network errors trigger exponential backoff
    Reject:  HTTP 400/401/403/422 are terminal (no retry)
"""

import json
import logging
import os
import random
import threading
import time
import urllib.error
import urllib.request
import uuid
from typing import Dict, List, Optional

from state import get_db

logger = logging.getLogger(__name__)

CLOUD_URL = os.getenv("OPENEYE_CLOUD_URL", "")
CLOUD_KEY = os.getenv("OPENEYE_CLOUD_KEY", "")
SYNC_INTERVAL = int(os.getenv("OPENEYE_SYNC_INTERVAL", "60"))
SYNC_BATCH = int(os.getenv("OPENEYE_SYNC_BATCH", "50"))
SYNC_MAX_RETRIES = int(os.getenv("OPENEYE_SYNC_MAX_RETRIES", "4"))
SYNC_BACKOFF_BASE = float(os.getenv("OPENEYE_SYNC_BACKOFF_BASE", "1.0"))

SYNC_TABLES = {
    "visual_sessions": os.getenv("OPENEYE_SYNC_SESSIONS", "1") == "1",
    "step_verifications": os.getenv("OPENEYE_SYNC_VERIFICATIONS", "1") == "1",
    "trajectories": os.getenv("OPENEYE_SYNC_TRAJECTORIES", "1") == "1",
    "skills": os.getenv("OPENEYE_SYNC_SKILLS", "0") == "1",
}

RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}


class CloudSyncError(Exception):
    """Raised when a sync attempt fails."""
    def __init__(self, message: str, retryable: bool = False, status: Optional[int] = None):
        super().__init__(message)
        self.retryable = retryable
        self.status = status


def _post_once(endpoint: str, payload, batch_id: str) -> bool:
    """One POST attempt. Returns True on 2xx, raises CloudSyncError otherwise."""
    if not CLOUD_URL or not CLOUD_KEY:
        return False
    url = f"{CLOUD_URL.rstrip('/')}/{endpoint}"
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {CLOUD_KEY}",
        "X-OpenEye-Client": "sidecar/1.0",
        "X-OpenEye-Batch-Id": batch_id,
    }, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            if 200 <= resp.status < 300:
                return True
            raise CloudSyncError(
                f"Unexpected status {resp.status}",
                retryable=resp.status in RETRYABLE_STATUS,
                status=resp.status)
    except urllib.error.HTTPError as e:
        raise CloudSyncError(
            f"HTTP {e.code} from {endpoint}",
            retryable=e.code in RETRYABLE_STATUS,
            status=e.code)
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        raise CloudSyncError(f"Network error: {e}", retryable=True)


def _post_with_retry(endpoint: str, payload, batch_id: str,
                     max_retries: int = SYNC_MAX_RETRIES) -> bool:
    """POST with exponential backoff + jitter on retryable failures."""
    if not CLOUD_URL or not CLOUD_KEY:
        return False
    last_err: Optional[CloudSyncError] = None
    for attempt in range(max_retries + 1):
        try:
            return _post_once(endpoint, payload, batch_id)
        except CloudSyncError as e:
            last_err = e
            if not e.retryable or attempt == max_retries:
                logger.warning("Cloud sync %s failed (status=%s, retryable=%s): %s",
                               endpoint, e.status, e.retryable, e)
                return False
            delay = SYNC_BACKOFF_BASE * (2 ** attempt) + random.uniform(0, 0.5)
            logger.info("Cloud sync %s attempt %d/%d failed (%s); retrying in %.1fs",
                        endpoint, attempt + 1, max_retries + 1, e, delay)
            time.sleep(delay)
    if last_err:
        logger.warning("Cloud sync %s exhausted retries: %s", endpoint, last_err)
    return False


def _strip_internal_fields(rows: List[Dict], table: str) -> List[Dict]:
    """Remove fields that shouldn't leave the device."""
    cleaned = []
    for r in rows:
        d = dict(r)
        d.pop("sync_pending", None)
        if table == "frames":
            d.pop("embedding_ref", None)
        cleaned.append(d)
    return cleaned


def _sync_table(table: str) -> int:
    if not SYNC_TABLES.get(table, False):
        return 0
    db = get_db()
    rows = db.get_pending_sync(table, limit=SYNC_BATCH)
    if not rows:
        return 0

    payload = _strip_internal_fields(rows, table)
    batch_id = str(uuid.uuid4())
    success = _post_with_retry(f"ingest/{table}", payload, batch_id)
    if not success:
        return 0

    ids = [r.get("id") for r in rows if r.get("id") is not None]
    if ids:
        db.mark_synced(table, ids)
    logger.info("Synced %d rows from %s (batch=%s)", len(rows), table, batch_id)
    return len(rows)


def sync_once() -> Dict[str, int]:
    if not CLOUD_URL or not CLOUD_KEY:
        return {}
    results: Dict[str, int] = {}
    for table in SYNC_TABLES:
        try:
            results[table] = _sync_table(table)
        except Exception as e:
            logger.warning("Sync error for table %s: %s", table, e)
            results[table] = 0
    return results


class SyncWorker(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True, name="openeye-sync")
        self._stop_event = threading.Event()

    def run(self):
        if not CLOUD_URL or not CLOUD_KEY:
            logger.debug("Cloud sync disabled (OPENEYE_CLOUD_URL not set)")
            return
        logger.info("Cloud sync worker started (interval=%ds, max_retries=%d)",
                    SYNC_INTERVAL, SYNC_MAX_RETRIES)
        while not self._stop_event.wait(SYNC_INTERVAL):
            try:
                results = sync_once()
                total = sum(results.values())
                if total:
                    logger.info("Sync pass: %s", results)
            except Exception as e:
                logger.warning("Sync worker error: %s", e)

    def stop(self):
        self._stop_event.set()


_worker: Optional[SyncWorker] = None


def start_sync_worker():
    global _worker
    if _worker and _worker.is_alive():
        return
    _worker = SyncWorker()
    _worker.start()


def stop_sync_worker():
    global _worker
    if _worker:
        _worker.stop()
        _worker = None
