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
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

sys.path.insert(0, os.path.dirname(__file__))

from state import get_db
from skills import write_skill, get_skill, list_skills, recall_relevant_skills, build_skills_context
from trajectories import capture_trajectory, export_for_training
from cloud_sync import start_sync_worker, stop_sync_worker, sync_once
from dpo_export import export_dpo_pairs

logging.basicConfig(level=logging.INFO, format="%(asctime)s [openeye] %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

PORT = int(os.getenv("OPENEYE_PORT", "7770"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    db = get_db()
    logger.info("OpenEye sidecar starting (db: %s, port: %d)", db.db_path, PORT)
    start_sync_worker()
    yield
    stop_sync_worker()
    logger.info("OpenEye sidecar stopped")


app = FastAPI(title="OpenEye Sidecar", version="1.0.0", lifespan=lifespan)


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
    return {"ok": True}

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


if __name__ == "__main__":
    uvicorn.run("server:app", host="127.0.0.1", port=PORT, log_level="info", workers=1)
