# Context Receiver — Reference Implementation

This is the starting point for Context's backend team to implement the
ingest endpoint OpenEye expects. It is **wire-compatible** with the
contract in [`../../docs/context-data.md`](../../docs/context-data.md)
and ships with 19 conformance tests.

## What this is

A minimal FastAPI server that:

- Accepts batched trajectory POSTs from OpenEye sidecars
- Authenticates via static bearer tokens (replace with real auth)
- Stores trajectories to SQLite (replace with your warehouse)
- Idempotent on `X-OpenEye-Batch-Id` (replays return 200, zero inserts)
- Multi-tenant — every operation is scoped to the token's tenant
- Supports soft-delete for right-to-be-forgotten requests
- Returns structured error bodies

## What this is NOT

This is a **reference**, not a production server. Before deploying:

| Concern | Reference does | Production should |
|---|---|---|
| Auth | Static dict from env var | OIDC / API key DB with revocation |
| Storage | Local SQLite | Postgres / S3 / Snowflake / Iceberg |
| Rate limiting | None | Per-token quotas (e.g. 1k batches/hour) |
| Async ingest | Synchronous write | Write to queue, return 202, process async |
| Audit log | stderr | Structured logs → SIEM |
| Bad payload | Return 422 | Send to DLQ with reason |
| Hard delete | Soft only (deleted_at) | Periodic vacuum job after retention window |
| Schema migrations | Single SQL block | Alembic / Liquibase / Sqitch |
| Backpressure | None | Response with Retry-After when overloaded |
| Tenant isolation | Query filter | Separate DBs / row-level security |

## Run it

```bash
cd examples/context-receiver
pip install -r requirements.txt

# Reference tokens map "token:tenant_id". Multiple tenants comma-separated.
export CONTEXT_RECEIVER_TOKENS="ctx-test-key:tenant-a,ctx-prod-key:tenant-b"
export CONTEXT_RECEIVER_DB="./context.db"

python server.py
# or
uvicorn server:app --port 8080
```

Health check:
```bash
curl http://localhost:8080/health
```

## Run the conformance tests

```bash
python -m pytest test_receiver.py -v
```

These tests are the **acceptance criteria** for any production
implementation. If a new backend passes all 19 tests, the OpenEye sidecar
will work against it unchanged.

## End-to-end smoke test against OpenEye

Spin up both servers and ship one real trajectory:

**Terminal 1 — start receiver:**
```bash
cd examples/context-receiver
CONTEXT_RECEIVER_TOKENS="ctx-test-key:dev-tenant" \
CONTEXT_RECEIVER_DB=./ctx.db \
uvicorn server:app --port 8080
```

**Terminal 2 — point OpenEye at it:**
```bash
cd ../..
export OPENEYE_CONTEXT_OPTIN=true
export OPENEYE_CONTEXT_API_KEY=ctx-test-key
export OPENEYE_CONTEXT_URL=http://localhost:8080/v1/openeye
export OPENEYE_CONTEXT_SYNC_INTERVAL=5    # speed up for testing

python sidecar/server.py
```

**Terminal 3 — generate a trajectory:**
```bash
curl -X POST http://localhost:7770/sessions/create \
  -H "Content-Type: application/json" -d '{}' \
  | tee /tmp/session.json
SID=$(python -c "import json;print(json.load(open('/tmp/session.json'))['session_id'])")

curl -X POST http://localhost:7770/sessions/$SID/messages \
  -H "Content-Type: application/json" \
  -d '{"role":"user","content":"frame test"}'

curl -X POST http://localhost:7770/visual-sessions/create \
  -H "Content-Type: application/json" \
  -d '{"device_type":"test","procedure_id":"bolt-assembly"}' \
  | tee /tmp/vs.json
VSID=$(python -c "import json;print(json.load(open('/tmp/vs.json'))['visual_session_id'])")

curl -X POST http://localhost:7770/steps/log \
  -H "Content-Type: application/json" \
  -d "{\"visual_session_id\":\"$VSID\",\"step_id\":\"s1\",\"result\":\"pass\"}"

curl -X POST http://localhost:7770/trajectories/capture \
  -H "Content-Type: application/json" \
  -d "{\"session_id\":\"$SID\",\"completed\":true,\"model\":\"test\",
       \"visual_session_id\":\"$VSID\",\"tags\":[\"bolt-assembly\"]}"

# Force immediate sync
curl -X POST http://localhost:7770/context/sync-now
```

**Verify on the receiver:**
```bash
curl http://localhost:8080/v1/openeye/trajectories \
  -H "Authorization: Bearer ctx-test-key"
```

You should see your trajectory with `tenant_id="dev-tenant"`, the
procedure_tag, the reward signal, and the conversation — **without** any
tenant_id, user_id, system_prompt, or visual_session_id from the OpenEye
side. That's the PII boundary working.

## API surface

### `POST /v1/openeye`
Ingest one batch of trajectories.

**Headers:**
- `Authorization: Bearer <token>` (required)
- `X-OpenEye-Batch-Id: <uuid>` (optional but recommended for idempotency)
- `X-OpenEye-Schema: 1.0` (optional)
- `X-OpenEye-Client: sidecar/1.0` (informational)

**Body:** See `docs/context-data.md` for full schema.

**Responses:**
- `200 {"received": N, "duplicate": false, "batch_id": "..."}` — new batch persisted
- `200 {"received": 0, "duplicate": true, "batch_id": "..."}` — idempotent replay
- `400` — schema mismatch or header/body batch_id disagreement
- `401` — missing or invalid auth
- `422` — malformed body

### `GET /v1/openeye/trajectories?procedure_tag=...&limit=...`
List the calling tenant's trajectories.

### `GET /v1/openeye/trajectories/{trajectory_id}`
Fetch one. Returns 404 if not yours.

### `DELETE /v1/openeye/trajectories/{trajectory_id}`
Soft-delete. Right-to-be-forgotten endpoint. Idempotent.

### `GET /v1/openeye/batches?limit=...`
Audit trail of received batches.

### `GET /health`
Liveness probe.

## What lands on disk

Each trajectory row contains:

| column | source |
|---|---|
| `trajectory_id` | OpenEye-generated UUID (not linkable to a user) |
| `batch_id` | OpenEye's `X-OpenEye-Batch-Id` header |
| `tenant_id` | Derived from your auth token, NOT from the request body |
| `model` | which LLM ran the session |
| `completed` | always 1 (incomplete trajectories are filtered upstream) |
| `reward_signal` | (passes + 0.5 × uncertain) / total |
| `procedure_tag` | the single procedure tag (meta tags stripped upstream) |
| `conversations` | ShareGPT JSON, system messages stripped upstream |
| `created_at` | when the OpenEye agent finished the session |
| `received_at` | when this receiver got the row |
| `deleted_at` | populated by DELETE for soft-delete |

Notice what's missing: no end-user identifiers, no customer org IDs, no
system prompts. That's by design — the OpenEye sidecar strips them before
the data ever leaves the device. If you find any of those fields appearing
here, OpenEye has a bug — open an issue.
