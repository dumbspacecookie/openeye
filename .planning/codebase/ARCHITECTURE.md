# Architecture

**Analysis Date:** 2026-04-09

## Pattern Overview

**Overall:** Hybrid TypeScript + Python (Node IPC) with async sidecar subprocess pattern.

**Key Characteristics:**
- Layered agent-centric architecture: pi-agent core (reasoning layer) wraps Python subprocess (persistence/inference layer)
- Bi-directional HTTP communication between TypeScript client and Python FastAPI server
- Full-text search (FTS5) on session messages and frame descriptions for memory retrieval
- Opt-in cloud sync for training data flywheel with configurable per-tenant isolation
- RL trajectory capture with structured (pass/fail/uncertain) reward signal from procedure verification

## Layers

**TypeScript Agent Layer (Node.js):**
- Purpose: Runs the pi-agent decision loop, manages tool invocations, streams responses from LLM
- Location: `src/index.ts`, `src/tools.ts`, `src/models.ts`
- Contains: OpenEyeAgent class, tool definitions, model configurations for 10+ LLM providers
- Depends on: `@mariozechner/pi-agent-core` (Agent runtime), `@mariozechner/pi-ai` (Model abstraction), sidecar HTTP API
- Used by: Consumer applications importing `@dumbspacecookie/openeye` package

**Sidecar Client Layer (TypeScript):**
- Purpose: Spawns Python subprocess, manages lifecycle, translates agent calls to HTTP requests
- Location: `src/sidecar-client.ts`
- Contains: SidecarClient class, HTTP request/response marshaling, health checks, non-throwing error handling
- Depends on: Node.js child_process, http modules, filesystem for sidecar location resolution
- Used by: OpenEyeAgent and Memory classes for all persistence/search operations

**Python FastAPI Server (Sidecar):**
- Purpose: Core persistence layer, FTS5 search, skill management, trajectory export, cloud sync coordination
- Location: `sidecar/server.py`
- Contains: FastAPI app with 20+ endpoints, request/response Pydantic models, lifespan context manager
- Depends on: `sidecar/state.py` (SQLite state engine), skills, trajectories, cloud_sync modules
- Used by: SidecarClient HTTP calls from TypeScript

**Python State Engine (SQLite):**
- Purpose: Manages all persistent data: sessions, messages, visual sessions, frames, step verifications, trajectories
- Location: `sidecar/state.py`
- Contains: SQLite schema (12 tables), connection pooling, FTS5 indexing, thread-safe accessors, migration logic
- Depends on: sqlite3, threading for concurrent frame ingestion
- Used by: server.py endpoints for all read/write operations

**Skills Layer (Markdown):**
- Purpose: Domain-specific procedural protocols that get injected into agent system prompt
- Location: `skills/[domain]/[skill].md` (medical, manufacturing, field-service)
- Contains: Skill front-matter (YAML) with name/domain/version, step verification criteria (pass/fail/uncertain)
- Depends on: State engine for persistence, skills.py for retrieval via semantic search
- Used by: Agent for context-aware step verification on domain-specific procedures

**Memory Compatibility Layer (TypeScript):**
- Purpose: Drop-in mem0/Zep replacement for simpler applications
- Location: `src/memory.ts`
- Contains: Memory class with add/search/update/delete/getAll interface mirroring mem0
- Depends on: SidecarClient (internally calls createSession → appendMessage → endSession)
- Used by: Applications migrating from mem0 to OpenEye

## Data Flow

**Agent Session Flow (Primary):**

1. Application calls `OpenEyeAgent.create()` with model, system prompt, tenant ID
2. SidecarClient spawns Python sidecar (or attaches to existing one)
3. Server boots FastAPI app, initializes SQLite state DB in `~/.openeye/openeye.db`
4. Agent injects relevant skills into system prompt via `/skills/context` endpoint
5. Agent creates session record in DB via `/sessions/create` → stored with source='pi'
6. Agent subscribes to message_end events and mirrors to DB via `/sessions/{id}/messages`
7. Application calls `agent.prompt(userMessage)` → Agent runs loop:
   - Routes through pi-agent-core to LLM
   - Tool calls (search_memory, verify_step, etc.) invoke TypeScript tool handlers
   - Handlers call SidecarClient methods → HTTP POST to Python endpoints
   - Results returned as tool results to agent
   - Agent continues reasoning
8. Application calls `agent.captureAndClose()` → creates training trajectory via `/trajectories/capture`
9. Trajectory packaged in ShareGPT format, optionally synced to cloud if `cloudSync=true`

**Visual Session + Step Verification Flow (Secondary):**

1. Application calls `agent.client.createVisualSession()` → creates visual_sessions record
2. Device sends frame → application calls `agent.client.logFrame()` → inserts frame record
3. Agent analyzes frame → calls `verify_step()` tool → inserts step_verifications record
4. Step result (pass/fail/uncertain) becomes reward signal for RL training
5. Application calls `agent.client.endVisualSession()` → marks visual_session.outcome
6. Session closes, RL trajectory created including all frames + step verifications

**Memory Search Flow (Tertiary):**

1. Agent tool `search_memory` called with query string
2. SidecarClient calls POST `/search/messages` with query, tenant_id, limit
3. Server routes to `state.search_fts5_messages()` → FTS5 query on messages table
4. Results filtered by tenant_id for multi-tenant isolation
5. Returns array of {snippet, content, session_id, model, source, timestamp}
6. Agent tool returns formatted JSON to agent context

**State Management:**

- **Session-scoped**: Each agent session creates one sessions record (source='pi'), with 1..N messages. Session lifecycle tracked via started_at/ended_at/end_reason
- **Visual session-scoped**: Each visual session creates one visual_sessions record with 1..N frames and 1..N step_verifications. Tracks device type, procedure ID, user ID for RL signal
- **Tenant-scoped**: All records include tenant_id for multi-tenant data isolation. Searches filter by tenant_id
- **Trajectory-scoped**: At session end, whole conversation captured as trajectory record with completion status, reward signals from step verifications, ready for DPO/SFT training
- **Cloud-scoped**: Rows with sync_pending=1 picked up by async sync_worker, sent to cloud endpoint if configured

## Key Abstractions

**OpenEyeAgent:**
- Purpose: High-level facade for agent creation and lifecycle
- Examples: `src/index.ts` class definition
- Pattern: Factory pattern (static `create()`) wrapping Agent + SidecarClient composition. Manages subscriptions to mirror messages to DB, handles skill injection, coordinates session creation/capture

**SidecarClient:**
- Purpose: HTTP bridge between TypeScript and Python
- Examples: `src/sidecar-client.ts` class with ~20 public methods
- Pattern: Subprocess lifecycle management (spawn → health check → reuse) + non-throwing wrapper pattern (all calls return null on failure, never throw). Enables graceful degradation if sidecar unavailable

**AgentTool:**
- Purpose: Registered tool on pi-agent that executes OpenEye operations
- Examples: `search_memory`, `verify_step`, `write_skill` in `src/tools.ts`
- Pattern: Pi-agent-compatible tool definition with parameters schema (Type objects) and async execute handler

**VerifyResult:**
- Purpose: Reward signal type for RL training
- Examples: "pass" | "fail" | "uncertain"
- Pattern: Literal union type ensuring only three outcomes, each with specific semantics for DPO pair construction

**Skill:**
- Purpose: Domain-specific procedural memory
- Examples: `skills/medical/hand-hygiene.md` with YAML front-matter + markdown body
- Pattern: Markdown with YAML metadata (name, description, domain, device_types, reward_threshold) + pass/fail/uncertain criteria per step

**Trajectory:**
- Purpose: Training data package (ShareGPT format)
- Examples: Created by `captureTrajectory()`, exported via `exportTrajectories()`
- Pattern: Conversation + metadata (model, systemPrompt, completedFlag, steps with pass/fail/uncertain results) ready for batch_runner or DPO trainer

## Entry Points

**Application Entry (TypeScript):**
- Location: `src/index.ts` (package main export)
- Triggers: `import { OpenEyeAgent, ... } from "@dumbspacecookie/openeye"`
- Responsibilities: Export OpenEyeAgent class, model constants, tool creator, Memory class, makeStreamFn() helper

**Sidecar Entry (Python):**
- Location: `sidecar/server.py`
- Triggers: TypeScript SidecarClient spawns via `python -m uvicorn server:app --port 7770`
- Responsibilities: Start FastAPI app, initialize state DB, register endpoint handlers, start cloud sync worker

**Skill Entry (Markdown):**
- Location: `skills/[domain]/[skill].md`
- Triggers: Skill discovery during `buildSkillsContext()` → Python loads all .md files in skills/ directories
- Responsibilities: Provide domain + procedure verification criteria to be injected into agent system prompt

**Test Entry (TypeScript):**
- Location: `tests/*.test.ts`
- Triggers: `npm test` → vitest runner
- Responsibilities: Integration tests for SidecarClient, preflight checks

**Test Entry (Python):**
- Location: `tests/test_sidecar.py`
- Triggers: `python -m pytest tests/test_sidecar.py -v`
- Responsibilities: Unit/integration tests for state.py, skills.py, trajectory export

## Error Handling

**Strategy:** Non-throwing error handling in SidecarClient, graceful degradation if sidecar unavailable.

**Patterns:**

- **HTTP Failures**: `SidecarClient.call()` returns `null` on any HTTP error (connection refused, timeout, 500). Callers check `if (!isReady()) return null` before making calls
- **Sidecar Death**: If subprocess exits, `_ready` flag set to false, subsequent calls return null without trying to retry
- **Schema Validation**: Pydantic models in server.py validate all incoming JSON payloads, return 422 Unprocessable Entity on mismatch
- **Database Integrity**: state.py uses foreign key constraints and transactions for multi-step operations (e.g., create session + append message as atomic unit)
- **Tenant Isolation**: All searchMemory/searchFrames operations filter by tenant_id. If tenant_id not provided, defaults to empty string (effectively isolating untenanted records)

## Cross-Cutting Concerns

**Logging:**
- TypeScript: console.stderr for sidecar subprocess output, [openeye] prefix for clarity
- Python: logging module with [openeye] prefix in FastAPI lifespan context

**Validation:**
- TypeScript: TypeScript static typing, optional runtime Type validation in pi-ai
- Python: Pydantic BaseModel for request payloads, explicit type annotations in state.py

**Authentication:**
- LLM API keys: Loaded from env vars (ANTHROPIC_API_KEY, OPENAI_API_KEY, etc.) by makeStreamFn()
- Cloud sync: Optional API key via OPENEYE_CLOUD_KEY env var, sent in sync headers
- Tenant isolation: All endpoints accept optional tenant_id, enforced in search queries

**Concurrency:**
- Python: SQLite with WAL mode for concurrent frame ingestion from multiple devices, thread-safe DB connection pooling in state.py
- TypeScript: All SidecarClient calls are non-blocking async, but serialized via HTTP to single-worker uvicorn instance
- Agent loop: Synchronous message turns (agent.prompt() awaits response before returning)

---

*Architecture analysis: 2026-04-09*
