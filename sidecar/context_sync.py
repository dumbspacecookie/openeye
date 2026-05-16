"""
OpenEye → Context training-data sync.

Loud-opt-in by default: this module does nothing unless the operator sets
OPENEYE_CONTEXT_OPTIN=true. When enabled, completed trajectories with a
reward signal are shipped to Context's ingest endpoint as training data.

Data hygiene (enforced in `_clean_for_context`):
  - tenant_id, user_id, visual_session_id, session_id are STRIPPED
  - system_prompt is STRIPPED (may contain customer IP)
  - tags are filtered to procedure tags only (no meta tags)
  - only completed trajectories with a non-null reward are sent

What IS sent per trajectory:
  - trajectory_id (random uuid, not linkable to a user)
  - model used
  - completed flag
  - reward_signal
  - procedure_tag (e.g. "bolt-assembly")
  - conversations (ShareGPT format — see note on system-prompt stripping above)
  - created_at timestamp

Endpoint contract: see docs/context-data.md
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
from typing import Any, Dict, List, Optional

from state import get_db
from pii_scrub import scrub_conversations

logger = logging.getLogger(__name__)


def _truthy(v: str) -> bool:
    return v.strip().lower() in ("true", "1", "yes", "on")


CONTEXT_OPTIN = _truthy(os.getenv("OPENEYE_CONTEXT_OPTIN", ""))
CONTEXT_URL = os.getenv("OPENEYE_CONTEXT_URL", "https://api.getcontext.info/v1/openeye")
CONTEXT_KEY = os.getenv("OPENEYE_CONTEXT_API_KEY", "")
SYNC_INTERVAL = int(os.getenv("OPENEYE_CONTEXT_SYNC_INTERVAL", "300"))   # 5 min
SYNC_BATCH = int(os.getenv("OPENEYE_CONTEXT_SYNC_BATCH", "20"))
MAX_RETRIES = int(os.getenv("OPENEYE_CONTEXT_MAX_RETRIES", "3"))
BACKOFF_BASE = float(os.getenv("OPENEYE_CONTEXT_BACKOFF_BASE", "2.0"))

# Consent attestation: the developer must affirm they have consent from
# the people whose procedure data they're capturing. Either persist the
# attestation in OPENEYE_HOME/.context-consent or set the env var.
OPENEYE_HOME = os.getenv("OPENEYE_HOME", os.path.expanduser("~/.openeye"))
CONSENT_MARKER = os.path.join(OPENEYE_HOME, ".context-consent")
CONSENT_ENV = _truthy(os.getenv("OPENEYE_CONTEXT_CONSENT_CONFIRMED", ""))

SCHEMA_VERSION = "1.0"
RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}

# These fields never leave the device. Hard-coded, not configurable.
STRIPPED_FIELDS = (
    "tenant_id", "user_id", "visual_session_id", "session_id",
    "exported_at", "sync_pending", "tags",  # raw tags JSON; we re-derive procedure_tag
)

META_TAGS = {"openeye", "completed", "abandoned", "error"}


class ContextSyncError(Exception):
    def __init__(self, message: str, retryable: bool = False, status: Optional[int] = None):
        super().__init__(message)
        self.retryable = retryable
        self.status = status


def has_consent_attestation() -> bool:
    """True if the developer has affirmed they have consent from people in frame.
    Set via env var (CI) or by calling record_consent_attestation()."""
    if CONSENT_ENV:
        return True
    return os.path.exists(CONSENT_MARKER)


def record_consent_attestation(note: Optional[str] = None) -> str:
    """Persist the attestation. Called from POST /context/consent.
    Returns the marker path so the caller can show it to the user."""
    os.makedirs(OPENEYE_HOME, exist_ok=True)
    with open(CONSENT_MARKER, "w", encoding="utf-8") as f:
        f.write(
            f"# OpenEye → Context consent attestation\n"
            f"# Recorded at: {time.time()}\n"
            f"# Note: {note or '(none)'}\n"
            f"#\n"
            f"# By creating this file the operator affirms:\n"
            f"# 1. They have informed consent from people captured in procedure\n"
            f"#    footage to share derived trajectory data with Context.\n"
            f"# 2. They understand what is shared (see docs/context-data.md).\n"
            f"# 3. They will keep their per-tenant opt-in roster current.\n"
            f"# Delete this file to revoke.\n")
    return CONSENT_MARKER


def revoke_consent_attestation() -> bool:
    """Remove the attestation marker. Sync halts on next pass."""
    try:
        os.remove(CONSENT_MARKER)
        return True
    except FileNotFoundError:
        return False


def is_enabled() -> bool:
    """True only when (a) operator opted in globally, (b) API key set,
    and (c) consent attestation has been recorded. All three required —
    any missing piece keeps sync silent."""
    return CONTEXT_OPTIN and bool(CONTEXT_KEY) and has_consent_attestation()


def _procedure_tag(tags_list: List[str]) -> Optional[str]:
    for t in tags_list:
        if t and t not in META_TAGS:
            return t
    return None


def _strip_system_prompts(conversations: List[Dict]) -> List[Dict]:
    """Drop system messages from ShareGPT conversations.
    System prompts often contain customer-specific IP we should not ingest."""
    return [m for m in conversations if m.get("from") != "system"]


def _clean_for_context(traj: Dict) -> Optional[Dict]:
    """Convert a raw trajectory row into a Context-bound payload.
    Returns None if the trajectory fails hygiene checks (no reward, no procedure)."""
    if traj.get("reward_signal") is None:
        return None
    procedure = _procedure_tag(traj.get("tags_list") or [])
    if not procedure:
        return None

    conversations = _strip_system_prompts(traj.get("conversations") or [])
    # PII scrub runs after system-message stripping so we don't waste cycles
    # on text that's about to be removed anyway. Returns a new list.
    conversations = scrub_conversations(conversations)

    cleaned = {
        "trajectory_id": traj["id"],
        "schema_version": SCHEMA_VERSION,
        "model": traj.get("model"),
        "completed": bool(traj.get("completed")),
        "reward_signal": traj["reward_signal"],
        "procedure_tag": procedure,
        "conversations": conversations,
        "created_at": traj.get("created_at"),
    }

    for field in STRIPPED_FIELDS:
        cleaned.pop(field, None)

    if not cleaned["conversations"]:
        return None

    return cleaned


def _post_once(payload: Dict, batch_id: str) -> bool:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(CONTEXT_URL, data=body, headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {CONTEXT_KEY}",
        "X-OpenEye-Client": "sidecar/1.0",
        "X-OpenEye-Batch-Id": batch_id,
        "X-OpenEye-Schema": SCHEMA_VERSION,
    }, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            if 200 <= resp.status < 300:
                return True
            raise ContextSyncError(
                f"Unexpected status {resp.status}",
                retryable=resp.status in RETRYABLE_STATUS,
                status=resp.status)
    except urllib.error.HTTPError as e:
        raise ContextSyncError(
            f"HTTP {e.code} from Context",
            retryable=e.code in RETRYABLE_STATUS,
            status=e.code)
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        raise ContextSyncError(f"Network error: {e}", retryable=True)


def _post_with_retry(payload: Dict, batch_id: str, max_retries: int = MAX_RETRIES) -> bool:
    last_err: Optional[ContextSyncError] = None
    for attempt in range(max_retries + 1):
        try:
            return _post_once(payload, batch_id)
        except ContextSyncError as e:
            last_err = e
            if not e.retryable or attempt == max_retries:
                logger.warning(
                    "Context sync failed (status=%s, retryable=%s): %s",
                    e.status, e.retryable, e)
                return False
            delay = BACKOFF_BASE * (2 ** attempt) + random.uniform(0, 0.5)
            logger.info(
                "Context sync attempt %d/%d failed (%s); retrying in %.1fs",
                attempt + 1, max_retries + 1, e, delay)
            time.sleep(delay)
    if last_err:
        logger.warning("Context sync exhausted retries: %s", last_err)
    return False


def sync_once() -> Dict[str, Any]:
    """One sync pass. Returns {sent, skipped, batch_id} for observability."""
    if not is_enabled():
        return {"sent": 0, "skipped": 0, "enabled": False}

    db = get_db()
    unsent = db.get_unsent_to_context(limit=SYNC_BATCH, completed_only=True)
    if not unsent:
        return {"sent": 0, "skipped": 0, "enabled": True}

    cleaned: List[Dict] = []
    sent_ids: List[str] = []
    skipped_no_tenant_optin = 0
    skipped_hygiene = 0
    skipped_ids: List[str] = []
    for t in unsent:
        # Default-deny per tenant: even with global opt-in, only
        # tenants who set opted_in=1 contribute data.
        if not db.is_tenant_opted_in(t.get("tenant_id")):
            skipped_no_tenant_optin += 1
            skipped_ids.append(t["id"])
            continue
        c = _clean_for_context(t)
        if c is None:
            skipped_hygiene += 1
            skipped_ids.append(t["id"])
            continue
        cleaned.append(c)
        sent_ids.append(t["id"])

    if not cleaned:
        # Nothing eligible — mark the rejected ones as "sent" anyway so we
        # don't reprocess them every pass. Use a sentinel batch_id so the
        # audit trail shows WHY they didn't ship.
        if skipped_ids:
            db.mark_sent_to_context(skipped_ids, batch_id="skipped-not-eligible")
        return {
            "sent": 0,
            "skipped_no_tenant_optin": skipped_no_tenant_optin,
            "skipped_hygiene": skipped_hygiene,
            "enabled": True,
        }

    batch_id = str(uuid.uuid4())
    payload = {
        "schema_version": SCHEMA_VERSION,
        "batch_id": batch_id,
        "trajectory_count": len(cleaned),
        "trajectories": cleaned,
    }

    ok = _post_with_retry(payload, batch_id)
    if not ok:
        _record_outcome(False, error=f"POST {CONTEXT_URL} failed after retries")
        return {
            "sent": 0,
            "skipped_no_tenant_optin": skipped_no_tenant_optin,
            "skipped_hygiene": skipped_hygiene,
            "enabled": True,
            "batch_id": batch_id,
            "failed": True,
        }

    _record_outcome(True)
    db.mark_sent_to_context(sent_ids, batch_id=batch_id)
    if skipped_ids:
        db.mark_sent_to_context(skipped_ids, batch_id="skipped-not-eligible")
    logger.info(
        "Context sync OK: sent=%d skipped_no_optin=%d skipped_hygiene=%d batch=%s",
        len(sent_ids), skipped_no_tenant_optin, skipped_hygiene, batch_id)
    return {
        "sent": len(sent_ids),
        "skipped_no_tenant_optin": skipped_no_tenant_optin,
        "skipped_hygiene": skipped_hygiene,
        "enabled": True,
        "batch_id": batch_id,
    }


class ContextSyncWorker(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True, name="openeye-context-sync")
        self._stop_event = threading.Event()

    def run(self):
        if not is_enabled():
            logger.debug("Context sync disabled (OPENEYE_CONTEXT_OPTIN not true)")
            return
        logger.info(
            "Context sync worker started (interval=%ds, batch=%d, url=%s)",
            SYNC_INTERVAL, SYNC_BATCH, CONTEXT_URL)
        while not self._stop_event.wait(SYNC_INTERVAL):
            try:
                result = sync_once()
                if result.get("sent"):
                    logger.info("Context sync pass: %s", result)
            except Exception as e:
                logger.warning("Context sync worker error: %s", e)

    def stop(self):
        self._stop_event.set()


# Failure tracking. The sync worker logs every retry, but on a
# repeatedly-broken endpoint that produces hundreds of "Context sync failed"
# lines a day. Track consecutive failures and emit ONE prominent warning
# to stderr after the third consecutive miss, then go silent.
_consecutive_failures = 0
_last_error: Optional[str] = None
_warning_emitted = False
_failure_lock = threading.Lock()
LOUD_FAILURE_THRESHOLD = int(os.getenv("OPENEYE_CONTEXT_LOUD_AFTER", "3"))


def _record_outcome(ok: bool, error: Optional[str] = None) -> None:
    """Record a sync outcome. Emits a one-time loud warning after
    LOUD_FAILURE_THRESHOLD consecutive failures."""
    global _consecutive_failures, _last_error, _warning_emitted
    with _failure_lock:
        if ok:
            _consecutive_failures = 0
            _last_error = None
            _warning_emitted = False
            return
        _consecutive_failures += 1
        _last_error = error
        if _consecutive_failures >= LOUD_FAILURE_THRESHOLD and not _warning_emitted:
            _warning_emitted = True
            import sys
            sys.stderr.write(
                f"\n"
                f"  ┌─ Context sync: REPEATED FAILURES ───────────────────────────────┐\n"
                f"  │ {_consecutive_failures} consecutive sync batches have failed. Last error:    │\n"
                f"  │   {(error or 'unknown')[:60]:60s}│\n"
                f"  │                                                                 │\n"
                f"  │ Check OPENEYE_CONTEXT_URL / OPENEYE_CONTEXT_API_KEY.            │\n"
                f"  │ Inspect via:  curl http://127.0.0.1:7770/context/status         │\n"
                f"  │ Trajectories stay queued locally until sync succeeds.           │\n"
                f"  └─────────────────────────────────────────────────────────────────┘\n"
                f"\n")


def get_failure_state() -> Dict[str, Any]:
    """Snapshot of the failure tracker, for /context/status."""
    with _failure_lock:
        return {
            "consecutive_failures": _consecutive_failures,
            "last_error": _last_error,
            "loud_warning_emitted": _warning_emitted,
        }


_worker: Optional[ContextSyncWorker] = None


def start_context_worker():
    global _worker
    if _worker and _worker.is_alive():
        return
    _worker = ContextSyncWorker()
    _worker.start()


def stop_context_worker():
    global _worker
    if _worker:
        _worker.stop()
        _worker = None
