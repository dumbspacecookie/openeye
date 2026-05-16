"""
OpenEye Sidecar — FastAPI server
Replaces the stdlib BaseHTTPRequestHandler with async FastAPI.
Handles concurrent frame ingestion from multiple AR devices without request queuing.

Start:
    python server.py               # development (auto-reload off)
    uvicorn server:app --port 7770 # production

The TypeScript sidecar-client.ts spawns this automatically.
"""

import json
import logging
import os
import sys
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel

sys.path.insert(0, os.path.dirname(__file__))

from state import get_db
from skills import write_skill, get_skill, list_skills, recall_relevant_skills, build_skills_context
from trajectories import capture_trajectory, export_for_training
from cloud_sync import start_sync_worker, stop_sync_worker, sync_once
from dpo_export import export_dpo_pairs
import context_sync
import retention
from event_bus import get_bus, format_sse
from fastapi.responses import StreamingResponse
import asyncio

logging.basicConfig(
    level=os.getenv("OPENEYE_LOG_LEVEL", "INFO").upper(),
    format='%(asctime)s [openeye] %(levelname)s %(name)s %(message)s')
logger = logging.getLogger(__name__)

PORT = int(os.getenv("OPENEYE_PORT", "7770"))
HOST = os.getenv("OPENEYE_BIND_HOST", "127.0.0.1")
SIDECAR_TOKEN = os.getenv("OPENEYE_SIDECAR_TOKEN", "")

# Paths exempt from auth even when SIDECAR_TOKEN is set — needed so health
# probes (from the TS spawner) and the openapi spec don't fail.
UNAUTHENTICATED_PATHS = {"/health", "/openapi.json", "/docs", "/redoc"}


def _print_context_banner():
    """Print the loud opt-in banner to stderr on sidecar boot.
    Surfaces the four states clearly:
      - undecided: full banner with enable instructions
      - opt-in pending consent: prompt to record attestation
      - fully enabled: one-line confirmation
      - explicit opt-out: silent
    """
    raw = os.getenv("OPENEYE_CONTEXT_OPTIN", "")
    optin = raw.strip().lower() in ("true", "1", "yes", "on")
    optout = raw.strip().lower() in ("false", "0", "no", "off")

    if optin:
        if not context_sync.CONTEXT_KEY:
            sys.stderr.write(
                "[openeye] OPENEYE_CONTEXT_OPTIN=true but "
                "OPENEYE_CONTEXT_API_KEY is unset — Context sharing disabled.\n")
            return
        if not context_sync.has_consent_attestation():
            sys.stderr.write(
                "\n"
                "  ┌─ Context sharing: AWAITING CONSENT ATTESTATION ─────────────────┐\n"
                "  │ OPENEYE_CONTEXT_OPTIN=true and key set, but you haven't yet     │\n"
                "  │ affirmed that you have consent from people in frame to share    │\n"
                "  │ their procedure data with Context.                              │\n"
                "  │                                                                 │\n"
                "  │ To attest:                                                      │\n"
                "  │   curl -X POST http://127.0.0.1:7770/context/consent \\          │\n"
                "  │        -H 'Content-Type: application/json' \\                   │\n"
                "  │        -d '{\"confirm\": true, \"note\": \"signed DPA YYYY-MM-DD\"}'  │\n"
                "  │                                                                 │\n"
                "  │ Or for CI: export OPENEYE_CONTEXT_CONSENT_CONFIRMED=true        │\n"
                "  └─────────────────────────────────────────────────────────────────┘\n"
                "\n")
            return
        sys.stderr.write(
            "[openeye] Context data sharing: ON. Per-tenant opt-in required "
            "for each contributing tenant — see docs/context-data.md.\n")
        return
    if optout:
        return
    # Undecided
    sys.stderr.write(
        "\n"
        "  ┌─ OpenEye is built by Context ───────────────────────────────────┐\n"
        "  │ Help us train better procedure-verification models. Opt in to   │\n"
        "  │ share completed trajectories + reward signals (no tenant IDs,   │\n"
        "  │ no user IDs, no system prompts — see docs/context-data.md).     │\n"
        "  │                                                                 │\n"
        "  │   1. export OPENEYE_CONTEXT_OPTIN=true                          │\n"
        "  │   2. export OPENEYE_CONTEXT_API_KEY=ctx-...                     │\n"
        "  │   3. Attest consent: POST /context/consent                      │\n"
        "  │   4. Opt-in each tenant:  POST /context/tenants/optin           │\n"
        "  │                                                                 │\n"
        "  │   To silence: export OPENEYE_CONTEXT_OPTIN=false                │\n"
        "  └─────────────────────────────────────────────────────────────────┘\n"
        "\n")


@asynccontextmanager
async def lifespan(app: FastAPI):
    db = get_db()
    logger.info("OpenEye sidecar starting (db: %s, port: %d)", db.db_path, PORT)
    _print_context_banner()
    start_sync_worker()
    context_sync.start_context_worker()
    retention.start_retention_worker()
    yield
    retention.stop_retention_worker()
    context_sync.stop_context_worker()
    stop_sync_worker()
    logger.info("OpenEye sidecar stopped")


app = FastAPI(title="OpenEye Sidecar", version="1.0.0", lifespan=lifespan)


# ── Middleware + error handlers ────────────────────────────────────────────

@app.middleware("http")
async def sidecar_auth(request: Request, call_next):
    """Optional shared-secret auth. When OPENEYE_SIDECAR_TOKEN is set, all
    requests except /health (needed by the TS spawner) must carry a
    matching Authorization: Bearer <token> header. When the env var is
    unset (default), the sidecar accepts unauthenticated requests — fine
    for the standard localhost-only deployment."""
    if not SIDECAR_TOKEN:
        return await call_next(request)
    if request.url.path in UNAUTHENTICATED_PATHS:
        return await call_next(request)
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer ") or auth[7:].strip() != SIDECAR_TOKEN:
        return JSONResponse(
            status_code=401,
            content={"error": "unauthorized",
                     "detail": "Missing or invalid sidecar token"})
    return await call_next(request)


@app.middleware("http")
async def request_logging(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
    start = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception as exc:
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        logger.exception(
            "request_failed id=%s method=%s path=%s elapsed_ms=%d error=%s",
            request_id, request.method, request.url.path, elapsed_ms, exc)
        return JSONResponse(
            status_code=500,
            content={"error": "internal_error", "request_id": request_id,
                     "detail": "An unexpected error occurred."},
            headers={"x-request-id": request_id})
    elapsed_ms = int((time.perf_counter() - start) * 1000)
    response.headers["x-request-id"] = request_id
    logger.info("request id=%s method=%s path=%s status=%d elapsed_ms=%d",
                request_id, request.method, request.url.path,
                response.status_code, elapsed_ms)
    return response


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    request_id = request.headers.get("x-request-id") or ""
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": _error_code(exc.status_code), "detail": exc.detail,
                 "request_id": request_id},
        headers={"x-request-id": request_id} if request_id else {})


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    request_id = request.headers.get("x-request-id") or ""
    return JSONResponse(
        status_code=422,
        content={"error": "validation_error", "detail": exc.errors(),
                 "request_id": request_id},
        headers={"x-request-id": request_id} if request_id else {})


@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError):
    request_id = request.headers.get("x-request-id") or ""
    return JSONResponse(
        status_code=400,
        content={"error": "bad_request", "detail": str(exc),
                 "request_id": request_id},
        headers={"x-request-id": request_id} if request_id else {})


def _error_code(status: int) -> str:
    return {
        400: "bad_request", 401: "unauthorized", 403: "forbidden",
        404: "not_found", 409: "conflict", 422: "validation_error",
        429: "rate_limited", 500: "internal_error", 503: "service_unavailable",
    }.get(status, "error")


# ── Request models ─────────────────────────────────────────────────────────

class SessionCreate(BaseModel):
    source: str = "pi"
    user_id: Optional[str] = None
    tenant_id: Optional[str] = None
    model: Optional[str] = None
    system_prompt: Optional[str] = None
    title: Optional[str] = None

class SessionEnd(BaseModel):
    reason: str = "normal"

class MessageAppend(BaseModel):
    role: str
    content: Optional[str] = None
    tool_calls: Optional[Any] = None
    tool_name: Optional[str] = None
    token_count: Optional[int] = None
    finish_reason: Optional[str] = None

class SearchQuery(BaseModel):
    query: str
    tenant_id: Optional[str] = None
    procedure_id: Optional[str] = None
    limit: int = 20

class VisualSessionCreate(BaseModel):
    device_type: str
    device_id: Optional[str] = None
    procedure_id: Optional[str] = None
    procedure_name: Optional[str] = None
    user_id: Optional[str] = None
    tenant_id: Optional[str] = None
    session_id: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

class VisualSessionEnd(BaseModel):
    outcome: str = "completed"

class FrameLog(BaseModel):
    visual_session_id: str
    sequence_num: int
    scene_description: str
    tenant_id: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    objects_detected: Optional[List[str]] = None
    step_context: Optional[str] = None
    embedding_ref: Optional[str] = None
    confidence: Optional[float] = None
    cloud_sync: bool = False

class StepVerificationLog(BaseModel):
    visual_session_id: str
    step_id: str
    result: str
    frame_id: Optional[int] = None
    step_name: Optional[str] = None
    confidence: Optional[float] = None
    reasoning: Optional[str] = None
    model_used: Optional[str] = None
    latency_ms: Optional[int] = None
    tenant_id: Optional[str] = None
    cloud_sync: bool = False

class SkillWrite(BaseModel):
    name: str
    content: str
    description: Optional[str] = None
    domain: str = "general"
    source: str = "generated"

class SkillRecall(BaseModel):
    task: str
    domain: Optional[str] = None
    top_k: int = 5

class TrajectoryCapture(BaseModel):
    session_id: str
    completed: bool = True
    model: str = "unknown"
    system_prompt: Optional[str] = None
    visual_session_id: Optional[str] = None
    tenant_id: Optional[str] = None
    tags: Optional[List[str]] = None
    cloud_sync: bool = False

class TrajectoryExport(BaseModel):
    output_path: str = "trajectories.jsonl"
    completed_only: bool = True

class DPOExport(BaseModel):
    output_path: str = "dpo_pairs.jsonl"
    chosen_threshold: float = 0.8
    rejected_threshold: float = 0.4
    completed_only: bool = True

class HubPushRequest(BaseModel):
    repo_id: str
    hf_token: str
    private: bool = False
    tags: Optional[List[str]] = None
    dry_run: bool = False
    completed_only: bool = True


# ── Routes ─────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    db = get_db()
    return {"ok": True, "db": str(db.db_path)}


# Sessions
@app.post("/sessions/create")
async def session_create(body: SessionCreate):
    sid = get_db().create_session(
        source=body.source, user_id=body.user_id, tenant_id=body.tenant_id,
        model=body.model, system_prompt=body.system_prompt, title=body.title)
    return {"session_id": sid}

@app.post("/sessions/{session_id}/end")
async def session_end(session_id: str, body: SessionEnd):
    get_db().end_session(session_id, reason=body.reason)
    get_bus().publish(session_id, "session_ended", {"reason": body.reason})
    return {"ok": True}


@app.get("/sessions/{session_id}/events")
async def session_events(session_id: str):
    """Server-Sent Events stream for a session. Pass session_id='*' to
    subscribe to all sessions. Use for real-time AR overlay verdicts."""
    bus = get_bus()

    async def event_generator():
        q = await bus.subscribe(session_id)
        # Initial hello frame so the client knows the stream is alive
        yield format_sse({
            "type": "subscribed",
            "session_id": session_id,
            "ts": __import__("time").time(),
            "data": {},
        })
        try:
            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=15.0)
                    yield format_sse(event)
                except asyncio.TimeoutError:
                    # Heartbeat — proxies and load balancers will close
                    # idle streams without this.
                    yield ": heartbeat\n\n"
        finally:
            await bus.unsubscribe(session_id, q)

    return StreamingResponse(event_generator(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})

@app.post("/sessions/{session_id}/messages")
async def message_append(session_id: str, body: MessageAppend):
    mid = get_db().append_message(
        session_id=session_id, role=body.role, content=body.content,
        tool_calls=body.tool_calls, tool_name=body.tool_name,
        token_count=body.token_count, finish_reason=body.finish_reason)
    return {"message_id": mid}

@app.get("/sessions/{session_id}/messages")
async def messages_get(session_id: str):
    return {"messages": get_db().get_messages(session_id)}

@app.get("/sessions")
async def sessions_list(user_id: Optional[str] = None, tenant_id: Optional[str] = None,
                        source: Optional[str] = None, limit: int = 100,
                        exclude_reason: Optional[str] = None):
    return {"sessions": get_db().list_sessions(
        user_id=user_id, tenant_id=tenant_id, source=source,
        limit=limit, exclude_reason=exclude_reason)}


# Search
@app.post("/search/messages")
async def search_messages(body: SearchQuery):
    return {"results": get_db().search_messages(query=body.query, tenant_id=body.tenant_id, limit=body.limit)}

@app.post("/search/frames")
async def search_frames(body: SearchQuery):
    return {"results": get_db().search_frames(
        query=body.query, tenant_id=body.tenant_id, procedure_id=body.procedure_id, limit=body.limit)}


# Visual sessions
@app.post("/visual-sessions/create")
async def visual_session_create(body: VisualSessionCreate):
    vsid = get_db().create_visual_session(
        device_type=body.device_type, device_id=body.device_id,
        procedure_id=body.procedure_id, procedure_name=body.procedure_name,
        user_id=body.user_id, tenant_id=body.tenant_id,
        session_id=body.session_id, metadata=body.metadata)
    return {"visual_session_id": vsid}

@app.post("/visual-sessions/{vsid}/end")
async def visual_session_end(vsid: str, body: VisualSessionEnd):
    get_db().end_visual_session(vsid, outcome=body.outcome)
    return {"ok": True}

@app.get("/visual-sessions/{vsid}")
async def visual_session_get(vsid: str):
    vs = get_db().get_visual_session(vsid)
    if not vs:
        raise HTTPException(status_code=404, detail="Visual session not found")
    return vs


# Frames
@app.post("/frames/log")
async def frame_log(body: FrameLog):
    fid = get_db().log_frame(
        visual_session_id=body.visual_session_id, sequence_num=body.sequence_num,
        scene_description=body.scene_description, tenant_id=body.tenant_id,
        width=body.width, height=body.height, objects_detected=body.objects_detected,
        step_context=body.step_context, embedding_ref=body.embedding_ref,
        confidence=body.confidence, mark_sync=body.cloud_sync)
    vs = get_db().get_visual_session(body.visual_session_id)
    session_id = vs.get("session_id") if vs else None
    get_bus().publish(session_id, "frame_logged", {
        "frame_id": fid,
        "visual_session_id": body.visual_session_id,
        "sequence_num": body.sequence_num,
        "scene_description": body.scene_description[:200],  # truncate for SSE bandwidth
        "step_context": body.step_context,
        "confidence": body.confidence,
    })
    return {"frame_id": fid}


# Step verifications
@app.post("/steps/log")
async def step_log(body: StepVerificationLog):
    if body.result not in ("pass", "fail", "uncertain"):
        raise HTTPException(status_code=422, detail="result must be pass, fail, or uncertain")
    vid = get_db().log_step_verification(
        visual_session_id=body.visual_session_id, step_id=body.step_id,
        result=body.result, frame_id=body.frame_id, step_name=body.step_name,
        confidence=body.confidence, reasoning=body.reasoning,
        model_used=body.model_used, latency_ms=body.latency_ms,
        tenant_id=body.tenant_id, mark_sync=body.cloud_sync)
    # Look up which session (text agent) this visual session belongs to so
    # we can route the event to the right SSE stream.
    vs = get_db().get_visual_session(body.visual_session_id)
    session_id = vs.get("session_id") if vs else None
    get_bus().publish(session_id, "step_verified", {
        "verification_id": vid,
        "visual_session_id": body.visual_session_id,
        "step_id": body.step_id,
        "step_name": body.step_name,
        "result": body.result,
        "confidence": body.confidence,
        "reasoning": body.reasoning,
        "frame_id": body.frame_id,
    })
    return {"verification_id": vid}


# Skills
@app.post("/skills/write")
async def skill_write(body: SkillWrite):
    return write_skill(name=body.name, content=body.content, description=body.description,
                       domain=body.domain, source=body.source)

@app.get("/skills")
async def skills_list(domain: Optional[str] = None):
    return {"skills": list_skills(domain=domain)}

@app.post("/skills/recall")
async def skill_recall(body: SkillRecall):
    return {"skills": recall_relevant_skills(task_description=body.task, domain=body.domain, top_k=body.top_k)}

@app.post("/skills/context")
async def skill_context(body: SkillRecall):
    return {"context": build_skills_context(task_description=body.task, domain=body.domain)}


# Trajectories
@app.post("/trajectories/capture")
async def trajectory_capture(body: TrajectoryCapture):
    tid = capture_trajectory(
        session_id=body.session_id, completed=body.completed, model=body.model,
        system_prompt=body.system_prompt, visual_session_id=body.visual_session_id,
        tenant_id=body.tenant_id, tags=body.tags, cloud_sync=body.cloud_sync)
    return {"trajectory_id": tid}

@app.post("/trajectories/export")
async def trajectory_export(body: TrajectoryExport):
    count = export_for_training(body.output_path, completed_only=body.completed_only)
    return {"exported": count, "path": body.output_path}

@app.post("/trajectories/export-dpo")
async def dpo_export(body: DPOExport):
    count = export_dpo_pairs(body.output_path, chosen_threshold=body.chosen_threshold,
                             rejected_threshold=body.rejected_threshold,
                             completed_only=body.completed_only)
    return {"exported": count, "path": body.output_path}

@app.post("/trajectories/push-to-hub")
async def push_to_hub(body: HubPushRequest):
    from huggingface import push_trajectories_to_hub
    result = push_trajectories_to_hub(
        repo_id=body.repo_id, hf_token=body.hf_token, private=body.private,
        tags=body.tags or [], dry_run=body.dry_run, completed_only=body.completed_only)
    return result


# Cloud sync
@app.post("/sync/now")
async def sync_now():
    return {"synced": sync_once()}


# Data retention
@app.get("/retention/status")
async def retention_status():
    return {
        "enabled": retention.is_enabled(),
        "retain_days": retention.RETAIN_DAYS,
        "interval_seconds": retention.RUN_INTERVAL_SECONDS,
    }

@app.post("/retention/prune-now")
async def retention_prune_now():
    return retention.prune_now()


# Per-procedure reward calibration
class RewardConfig(BaseModel):
    procedure_tag: str
    pass_weight: float = 1.0
    uncertain_weight: float = 0.5
    fail_weight: float = 0.0
    note: Optional[str] = None

@app.post("/procedures/reward-config")
async def set_procedure_reward(body: RewardConfig):
    """Configure custom reward weights for a procedure. The reward formula
    becomes (pass_weight*P + uncertain_weight*U + fail_weight*F) / total."""
    return get_db().set_procedure_reward_weights(
        procedure_tag=body.procedure_tag,
        pass_weight=body.pass_weight,
        uncertain_weight=body.uncertain_weight,
        fail_weight=body.fail_weight,
        note=body.note)

@app.get("/procedures/reward-config")
async def list_procedure_rewards():
    return {"procedures": get_db().list_procedure_reward_configs()}

@app.get("/procedures/reward-config/{procedure_tag}")
async def get_procedure_reward(procedure_tag: str):
    return get_db().get_procedure_reward_weights(procedure_tag)


# Context training-data sync (loud opt-in)
@app.get("/context/status")
async def context_status():
    """Reports the current Context-sharing state. Safe to call without opt-in."""
    optin_truthy = context_sync.CONTEXT_OPTIN
    key_set = bool(context_sync.CONTEXT_KEY)
    consent = context_sync.has_consent_attestation()
    enabled = context_sync.is_enabled()

    blocker = None
    if not optin_truthy:
        blocker = "OPENEYE_CONTEXT_OPTIN is not true"
    elif not key_set:
        blocker = "OPENEYE_CONTEXT_API_KEY is not set"
    elif not consent:
        blocker = "Consent attestation not recorded — POST /context/consent or set OPENEYE_CONTEXT_CONSENT_CONFIRMED=true"

    return {
        "enabled": enabled,
        "blocker": blocker,
        "optin_env_set": optin_truthy,
        "api_key_set": key_set,
        "consent_attested": consent,
        "consent_marker_path": context_sync.CONSENT_MARKER,
        "endpoint": context_sync.CONTEXT_URL if enabled else None,
        "interval_seconds": context_sync.SYNC_INTERVAL,
        "schema_version": context_sync.SCHEMA_VERSION,
        "failure_state": context_sync.get_failure_state(),
    }


class ConsentAttestation(BaseModel):
    confirm: bool
    note: Optional[str] = None

@app.post("/context/consent")
async def context_consent(body: ConsentAttestation):
    """Record (or revoke) the developer's consent attestation.

    By POSTing confirm=true the developer affirms they have consent from
    people captured in procedure footage to share derived trajectory data
    with Context. The attestation is persisted to OPENEYE_HOME/.context-consent
    so it survives sidecar restarts."""
    if body.confirm:
        path = context_sync.record_consent_attestation(note=body.note)
        return {"ok": True, "attested": True, "marker": path}
    revoked = context_sync.revoke_consent_attestation()
    return {"ok": True, "attested": False, "revoked": revoked}

@app.post("/context/sync-now")
async def context_sync_now():
    """Trigger one Context sync pass immediately. No-op if opt-in is off."""
    return context_sync.sync_once()

@app.post("/context/forget/{trajectory_id}")
async def context_forget(trajectory_id: str):
    """Reset a trajectory's Context-sync marker so it's eligible to be re-sent.
    Use when Context confirms deletion or when a row got stuck."""
    ok = get_db().forget_context_sync(trajectory_id)
    if not ok:
        raise HTTPException(status_code=404, detail="No Context-sync record for that trajectory")
    return {"ok": True, "trajectory_id": trajectory_id}


# Per-tenant Context opt-in (default deny — only opted-in tenants contribute)
class TenantOptin(BaseModel):
    tenant_id: str
    opted_in: bool
    note: Optional[str] = None

@app.post("/context/tenants/optin")
async def context_tenant_optin(body: TenantOptin):
    """Opt a tenant in or out of Context data sharing. Default is OFF for
    every tenant — they must explicitly be opted in here, even when the
    global OPENEYE_CONTEXT_OPTIN flag is true."""
    return get_db().set_tenant_optin(body.tenant_id, body.opted_in, note=body.note)

@app.get("/context/tenants")
async def context_list_tenant_optins():
    return {"tenants": get_db().list_tenant_optins()}

@app.get("/context/tenants/{tenant_id}")
async def context_get_tenant_optin(tenant_id: str):
    row = get_db().get_tenant_optin(tenant_id)
    return row or {"tenant_id": tenant_id, "opted_in": False, "note": None}


if __name__ == "__main__":
    workers = int(os.getenv("OPENEYE_WORKERS", "1"))
    if workers != 1:
        logger.warning(
            "OPENEYE_WORKERS=%d but SQLite uses a single-process write lock. "
            "Run with 1 worker unless you've migrated to a multi-process backing store.",
            workers)
    uvicorn.run("server:app", host=HOST, port=PORT, log_level="info", workers=workers)
