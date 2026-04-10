"""
OpenEye Cloud Sync
Opt-in data streaming to the OpenEye cloud platform.
Users enable this by setting OPENEYE_CLOUD_URL and OPENEYE_CLOUD_KEY.
"""

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from typing import Dict, List, Optional

from state import get_db

logger = logging.getLogger(__name__)

CLOUD_URL = os.getenv("OPENEYE_CLOUD_URL", "")
CLOUD_KEY = os.getenv("OPENEYE_CLOUD_KEY", "")
SYNC_INTERVAL = int(os.getenv("OPENEYE_SYNC_INTERVAL", "60"))
SYNC_BATCH = int(os.getenv("OPENEYE_SYNC_BATCH", "50"))

SYNC_TABLES = {
    "visual_sessions": os.getenv("OPENEYE_SYNC_SESSIONS", "1") == "1",
    "step_verifications": os.getenv("OPENEYE_SYNC_VERIFICATIONS", "1") == "1",
    "trajectories": os.getenv("OPENEYE_SYNC_TRAJECTORIES", "1") == "1",
    "skills": os.getenv("OPENEYE_SYNC_SKILLS", "0") == "1",
}


def _post(endpoint, payload):
    if not CLOUD_URL or not CLOUD_KEY:
        return False
    url = f"{CLOUD_URL.rstrip('/')}/{endpoint}"
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {CLOUD_KEY}",
        "X-OpenEye-Client": "sidecar/1.0",
    }, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError) as e:
        logger.debug("Cloud sync failed for %s: %s", endpoint, e)
        return False


def _sync_table(table):
    if not SYNC_TABLES.get(table, False):
        return 0
    db = get_db()
    rows = db.get_pending_sync(table, limit=SYNC_BATCH)
    if not rows:
        return 0
    if table == "frames":
        rows = [{k: v for k, v in dict(r).items() if k != "embedding_ref"} for r in rows]
    success = _post(f"ingest/{table}", rows)
    if success:
        ids = [r.get("id") for r in rows if r.get("id")]
        if ids:
            db.mark_synced(table, ids)
        logger.info("Synced %d rows from %s", len(rows), table)
        return len(rows)
    return 0


def sync_once():
    if not CLOUD_URL or not CLOUD_KEY:
        return {}
    results = {}
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
        logger.info("Cloud sync worker started (interval=%ds)", SYNC_INTERVAL)
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
