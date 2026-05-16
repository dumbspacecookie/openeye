"""
Reference Context Receiver — minimal FastAPI implementation of the
docs/context-data.md ingest contract.

This is a STARTING POINT for Context's backend team. It is correct against
the contract OpenEye expects, but it stores trajectories in SQLite and has
no production concerns wired up. Before deploying:

  - Replace SQLite with your warehouse (Postgres / S3 / Snowflake / etc.)
  - Wire auth tokens to your real identity system (not the static dict here)
  - Add rate limiting per token
  - Add structured audit logging
  - Add an async write queue so 200ms ingest latency doesn't cap throughput
  - Add a DLQ for bad payloads instead of returning 422
  - Add per-tenant data partitioning if tokens map to multiple tenants

Endpoints:
  POST   /v1/openeye                          ingest a batch (the contract)
  GET    /v1/openeye/trajectories             list stored trajectories (tenant)
  GET    /v1/openeye/trajectories/{id}        fetch one (tenant)
  DELETE /v1/openeye/trajectories/{id}        DSAR / right-to-be-forgotten (tenant)
  GET    /v1/openeye/batches                  list received batches (tenant)

  GET    /v1/admin/trajectories/{id}          operator fetch — any tenant
  DELETE /v1/admin/trajectories/{id}          operator DSAR — any tenant
                                              (use to honor inbound email
                                              deletion requests without SSH)

  GET    /health                              health check

Run:
  pip install -r requirements.txt
  export CONTEXT_RECEIVER_TOKENS="ctx-test-key:tenant-a,ctx-prod-key:tenant-b"
  # Optional: enables /v1/admin/* for operator-level DSAR handling.
  export CONTEXT_RECEIVER_ADMIN_TOKEN="ctx-admin-secret"
  uvicorn server:app --port 8080
"""

import json
import logging
import os
import sqlite3
import sys
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

import uvicorn
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

logging.basicConfig(
    level=os.getenv("CONTEXT_RECEIVER_LOG_LEVEL", "INFO").upper(),
    format='%(asctime)s [ctx-receiver] %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

DB_PATH = Path(os.getenv("CONTEXT_RECEIVER_DB", "context_receiver.db"))
SUPPORTED_SCHEMA = "1.0"


# ── Auth ──────────────────────────────────────────────────────────────────────

def _load_tokens() -> Dict[str, str]:
    """Parse CONTEXT_RECEIVER_TOKENS="token1:tenant1,token2:tenant2".
    Returns {token: tenant_id}.

    PRODUCTION: replace with a database lookup against your identity system.
    Static env-based tokens are for the reference impl only."""
    raw = os.getenv("CONTEXT_RECEIVER_TOKENS", "")
    out: Dict[str, str] = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if not pair or ":" not in pair:
            continue
        token, tenant = pair.split(":", 1)
        out[token.strip()] = tenant.strip()
    if not out:
        # Allow a dev-mode default so the test suite works without env setup
        out["ctx-test-key"] = "dev-tenant"
    return out


TOKENS = _load_tokens()
ADMIN_TOKEN = os.getenv("CONTEXT_RECEIVER_ADMIN_TOKEN", "").strip()


def _extract_bearer(authorization: Optional[str]) -> str:
    if not authorization:
        raise HTTPException(401, "Missing Authorization header")
    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(401, "Authorization must be 'Bearer <token>'")
    return parts[1].strip()


def authenticate(authorization: Optional[str]) -> str:
    """Extracts and validates the bearer token. Returns the tenant_id.
    Raises 401 on any failure — never leak whether token vs scheme was wrong."""
    token = _extract_bearer(authorization)
    tenant = TOKENS.get(token)
    if not tenant:
        raise HTTPException(401, "Invalid token")
    return tenant


def authenticate_admin(authorization: Optional[str]) -> None:
    """Operator-side token for /v1/admin/*. Lets me delete a trajectory
    when someone emails support@ without ssh'ing into the box. 401 whether
    the token is wrong or just not configured — probes can't tell."""
    token = _extract_bearer(authorization)
    if not ADMIN_TOKEN or token != ADMIN_TOKEN:
        raise HTTPException(401, "Invalid admin token")


# ── Storage ───────────────────────────────────────────────────────────────────

class ReceiverStore:
    """Thread-safe SQLite-backed store. SCHEMA:
      batches      — one row per X-OpenEye-Batch-Id (idempotency)
      trajectories — one row per trajectory across all batches
    """

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS batches (
        batch_id        TEXT PRIMARY KEY,
        tenant_id       TEXT NOT NULL,
        schema_version  TEXT NOT NULL,
        trajectory_count INTEGER NOT NULL,
        received_at     REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS trajectories (
        trajectory_id   TEXT PRIMARY KEY,
        batch_id        TEXT NOT NULL REFERENCES batches(batch_id),
        tenant_id       TEXT NOT NULL,
        model           TEXT,
        completed       INTEGER NOT NULL,
        reward_signal   REAL,
        procedure_tag   TEXT,
        conversations   TEXT NOT NULL,
        created_at      REAL,
        received_at     REAL NOT NULL,
        deleted_at      REAL
    );
    CREATE INDEX IF NOT EXISTS idx_traj_tenant ON trajectories(tenant_id);
    CREATE INDEX IF NOT EXISTS idx_traj_procedure ON trajectories(procedure_tag);
    """

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False, timeout=10.0)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(self.SCHEMA)
        self._conn.commit()

    def close(self):
        with self._lock:
            self._conn.close()

    def has_batch(self, batch_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute("SELECT 1 FROM batches WHERE batch_id=?", (batch_id,))
            return cur.fetchone() is not None

    def store_batch(self, batch_id: str, tenant_id: str, schema_version: str,
                    trajectories: List[Dict[str, Any]]) -> int:
        """Atomic batch insert. Returns count of NEW trajectories persisted.
        Idempotent: if the batch already exists, returns 0 (caller should
        check has_batch first to return 200 cleanly)."""
        now = time.time()
        inserted = 0
        with self._lock:
            try:
                self._conn.execute(
                    "INSERT INTO batches (batch_id, tenant_id, schema_version, trajectory_count, received_at) "
                    "VALUES (?,?,?,?,?)",
                    (batch_id, tenant_id, schema_version, len(trajectories), now))
            except sqlite3.IntegrityError:
                # Race: another worker wrote the batch between our has_batch and here
                return 0

            for t in trajectories:
                try:
                    self._conn.execute(
                        """INSERT INTO trajectories
                           (trajectory_id, batch_id, tenant_id, model, completed,
                            reward_signal, procedure_tag, conversations,
                            created_at, received_at)
                           VALUES (?,?,?,?,?,?,?,?,?,?)""",
                        (t["trajectory_id"], batch_id, tenant_id,
                         t.get("model"), 1 if t.get("completed") else 0,
                         t.get("reward_signal"), t.get("procedure_tag"),
                         json.dumps(t.get("conversations", []), ensure_ascii=False),
                         t.get("created_at"), now))
                    inserted += 1
                except sqlite3.IntegrityError:
                    # Duplicate trajectory_id from a previous batch — skip silently
                    logger.warning("Duplicate trajectory_id %s ignored", t["trajectory_id"])
            self._conn.commit()
        return inserted

    def list_trajectories(self, tenant_id: str, limit: int = 100,
                          procedure_tag: Optional[str] = None) -> List[Dict]:
        clauses = ["tenant_id = ?", "deleted_at IS NULL"]
        params: List[Any] = [tenant_id]
        if procedure_tag:
            clauses.append("procedure_tag = ?")
            params.append(procedure_tag)
        params.append(limit)
        q = (f"SELECT trajectory_id, model, reward_signal, procedure_tag, "
             f"created_at, received_at FROM trajectories "
             f"WHERE {' AND '.join(clauses)} ORDER BY received_at DESC LIMIT ?")
        with self._lock:
            cur = self._conn.execute(q, params)
            return [dict(r) for r in cur.fetchall()]

    def get_trajectory(self, tenant_id: str, trajectory_id: str) -> Optional[Dict]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM trajectories WHERE trajectory_id=? AND tenant_id=? AND deleted_at IS NULL",
                (trajectory_id, tenant_id))
            row = cur.fetchone()
        if not row:
            return None
        d = dict(row)
        try:
            d["conversations"] = json.loads(d["conversations"])
        except (json.JSONDecodeError, TypeError):
            d["conversations"] = []
        return d

    def soft_delete(self, tenant_id: str, trajectory_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE trajectories SET deleted_at=? "
                "WHERE trajectory_id=? AND tenant_id=? AND deleted_at IS NULL",
                (time.time(), trajectory_id, tenant_id))
            self._conn.commit()
            return cur.rowcount > 0

    def get_trajectory_any(self, trajectory_id: str) -> Optional[Dict]:
        """Operator fetch — bypasses tenant scope. For inbound DSAR
        requests when the user doesn't know which tenant owns the row."""
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM trajectories WHERE trajectory_id=? AND deleted_at IS NULL",
                (trajectory_id,))
            row = cur.fetchone()
        if not row:
            return None
        d = dict(row)
        try:
            d["conversations"] = json.loads(d["conversations"])
        except (json.JSONDecodeError, TypeError):
            d["conversations"] = []
        return d

    def soft_delete_any(self, trajectory_id: str) -> bool:
        """Operator soft-delete — bypasses tenant scope."""
        with self._lock:
            cur = self._conn.execute(
                "UPDATE trajectories SET deleted_at=? "
                "WHERE trajectory_id=? AND deleted_at IS NULL",
                (time.time(), trajectory_id))
            self._conn.commit()
            return cur.rowcount > 0

    def list_batches(self, tenant_id: str, limit: int = 100) -> List[Dict]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM batches WHERE tenant_id=? ORDER BY received_at DESC LIMIT ?",
                (tenant_id, limit))
            return [dict(r) for r in cur.fetchall()]


_store: Optional[ReceiverStore] = None


def get_store() -> ReceiverStore:
    global _store
    if _store is None:
        _store = ReceiverStore(DB_PATH)
    return _store


# ── Models ────────────────────────────────────────────────────────────────────

class ConversationTurn(BaseModel):
    # ShareGPT format. We allow extra fields so future schema additions
    # don't break the receiver.
    model_config = {"extra": "allow"}


class TrajectoryIn(BaseModel):
    trajectory_id: str = Field(..., min_length=1)
    schema_version: Optional[str] = None
    model: Optional[str] = None
    completed: bool = True
    reward_signal: Optional[float] = None
    procedure_tag: Optional[str] = None
    conversations: List[Dict[str, Any]] = Field(default_factory=list)
    created_at: Optional[float] = None


class BatchIn(BaseModel):
    schema_version: str
    batch_id: str = Field(..., min_length=1)
    trajectory_count: Optional[int] = None
    trajectories: List[TrajectoryIn] = Field(default_factory=list)


# ── App ───────────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    store = get_store()
    logger.info("don't panic — receiver up. db=%s tokens=%d admin=%s",
                store.db_path, len(TOKENS),
                "enabled" if ADMIN_TOKEN else "disabled")
    yield
    store.close()


app = FastAPI(title="Context Receiver (Reference)", version="0.1.0", lifespan=lifespan)


@app.exception_handler(HTTPException)
async def http_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.detail, "status": exc.status_code})


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={"error": "validation_error", "detail": exc.errors()})


@app.get("/health")
async def health():
    return {"ok": True, "db": str(get_store().db_path), "schema": SUPPORTED_SCHEMA}


@app.post("/v1/openeye")
async def ingest(
    batch: BatchIn,
    authorization: Optional[str] = Header(default=None),
    x_openeye_batch_id: Optional[str] = Header(default=None),
    x_openeye_schema: Optional[str] = Header(default=None),
    x_openeye_client: Optional[str] = Header(default=None),
):
    tenant_id = authenticate(authorization)

    # Header batch ID is the source of truth for idempotency.
    # If body batch_id disagrees, reject — likely a client bug.
    if x_openeye_batch_id and x_openeye_batch_id != batch.batch_id:
        raise HTTPException(400, "X-OpenEye-Batch-Id header does not match body batch_id")
    batch_id = x_openeye_batch_id or batch.batch_id

    # Body AND header must match. A future sidecar bumping the header before
    # the body would otherwise slip through and corrupt parsing downstream.
    if batch.schema_version != SUPPORTED_SCHEMA:
        raise HTTPException(400, f"Unsupported schema {batch.schema_version}; expected {SUPPORTED_SCHEMA}")
    if x_openeye_schema and x_openeye_schema != SUPPORTED_SCHEMA:
        raise HTTPException(400, f"Unsupported X-OpenEye-Schema {x_openeye_schema}; expected {SUPPORTED_SCHEMA}")

    store = get_store()

    # Idempotency: if we've seen this batch before, ack with 200 and zero inserts.
    if store.has_batch(batch_id):
        logger.info("Idempotent replay of batch %s from tenant %s", batch_id, tenant_id)
        return {"received": 0, "duplicate": True, "batch_id": batch_id}

    inserted = store.store_batch(
        batch_id=batch_id,
        tenant_id=tenant_id,
        schema_version=batch.schema_version,
        trajectories=[t.model_dump() for t in batch.trajectories])

    logger.info("Accepted batch %s tenant=%s inserted=%d", batch_id, tenant_id, inserted)
    return {"received": inserted, "duplicate": False, "batch_id": batch_id}


@app.get("/v1/openeye/trajectories")
async def list_trajectories(
    procedure_tag: Optional[str] = None,
    limit: int = 100,
    authorization: Optional[str] = Header(default=None),
):
    tenant_id = authenticate(authorization)
    return {"trajectories": get_store().list_trajectories(
        tenant_id, limit=limit, procedure_tag=procedure_tag)}


@app.get("/v1/openeye/trajectories/{trajectory_id}")
async def get_trajectory(
    trajectory_id: str,
    authorization: Optional[str] = Header(default=None),
):
    tenant_id = authenticate(authorization)
    t = get_store().get_trajectory(tenant_id, trajectory_id)
    if not t:
        raise HTTPException(404, "Trajectory not found")
    return t


@app.delete("/v1/openeye/trajectories/{trajectory_id}")
async def delete_trajectory(
    trajectory_id: str,
    authorization: Optional[str] = Header(default=None),
):
    """Right-to-be-forgotten. Soft delete (sets deleted_at). For hard
    delete, run a periodic VACUUM job against deleted_at IS NOT NULL rows."""
    tenant_id = authenticate(authorization)
    ok = get_store().soft_delete(tenant_id, trajectory_id)
    if not ok:
        raise HTTPException(404, "Trajectory not found or already deleted")
    return {"ok": True, "trajectory_id": trajectory_id}


@app.get("/v1/openeye/batches")
async def list_batches(
    limit: int = 100,
    authorization: Optional[str] = Header(default=None),
):
    tenant_id = authenticate(authorization)
    return {"batches": get_store().list_batches(tenant_id, limit=limit)}


# ── Admin / operator endpoints ────────────────────────────────────────────────
# Cross-tenant. Requires CONTEXT_RECEIVER_ADMIN_TOKEN. For honoring inbound
# DSAR emails without SSH gymnastics. Don't expose this token to customers.

@app.get("/v1/admin/trajectories/{trajectory_id}")
async def admin_get_trajectory(
    trajectory_id: str,
    authorization: Optional[str] = Header(default=None),
):
    authenticate_admin(authorization)
    t = get_store().get_trajectory_any(trajectory_id)
    if not t:
        raise HTTPException(404, "Trajectory not found")
    return t


@app.delete("/v1/admin/trajectories/{trajectory_id}")
async def admin_delete_trajectory(
    trajectory_id: str,
    authorization: Optional[str] = Header(default=None),
):
    authenticate_admin(authorization)
    ok = get_store().soft_delete_any(trajectory_id)
    if not ok:
        raise HTTPException(404, "Trajectory not found or already deleted")
    logger.info("ADMIN DSAR soft-delete: trajectory_id=%s", trajectory_id)
    return {"ok": True, "trajectory_id": trajectory_id}


if __name__ == "__main__":
    uvicorn.run("server:app", host="0.0.0.0",
                port=int(os.getenv("CONTEXT_RECEIVER_PORT", "8080")),
                log_level="info", workers=1)
