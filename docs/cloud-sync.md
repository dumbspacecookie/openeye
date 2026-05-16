# Cloud Sync — Endpoint Contract

OpenEye does not run a hosted cloud. Cloud sync is **opt-in**: each deployment
points the sidecar at an HTTP endpoint *you* operate, and the sidecar streams
rows there in the background.

This document specifies the contract your endpoint must implement.

## Enabling sync

Set two env vars on the sidecar process:

```bash
export OPENEYE_CLOUD_URL="https://cloud.your-org.example/openeye"
export OPENEYE_CLOUD_KEY="<your-bearer-token>"
```

Optional tuning:

| env var | default | description |
|---|---|---|
| `OPENEYE_SYNC_INTERVAL` | `60` | seconds between sync passes |
| `OPENEYE_SYNC_BATCH` | `50` | max rows per request |
| `OPENEYE_SYNC_MAX_RETRIES` | `4` | retry attempts per batch on transient failure |
| `OPENEYE_SYNC_BACKOFF_BASE` | `1.0` | base seconds for exponential backoff |
| `OPENEYE_SYNC_SESSIONS` | `1` | sync `visual_sessions` table |
| `OPENEYE_SYNC_VERIFICATIONS` | `1` | sync `step_verifications` table |
| `OPENEYE_SYNC_TRAJECTORIES` | `1` | sync `trajectories` table |
| `OPENEYE_SYNC_SKILLS` | `0` | sync `skills` table |

Setting any `OPENEYE_SYNC_*` flag to `0` disables sync for that table.

## Request

```
POST {OPENEYE_CLOUD_URL}/ingest/{table}
Authorization: Bearer {OPENEYE_CLOUD_KEY}
Content-Type: application/json
X-OpenEye-Client: sidecar/1.0
X-OpenEye-Batch-Id: <uuid4>     ← idempotency key

[
  { "id": "...", ...row fields... },
  ...
]
```

`{table}` is one of: `visual_sessions`, `step_verifications`, `trajectories`,
`skills`. (`frames` is not enabled by default — frame descriptions can be
PII-bearing and should be opted into explicitly.)

The body is a JSON **array** of row dicts. Internal fields (`sync_pending`,
`embedding_ref` for frames) are stripped before sending.

## Responses

| status | meaning | sidecar behavior |
|---|---|---|
| `200`, `201` | accepted | rows marked `sync_pending=0` |
| `400`, `401`, `403`, `422` | terminal error | logged, **no retry**, rows stay pending |
| `408`, `425`, `429`, `5xx` | transient | retry with exponential backoff + jitter |
| network error / timeout | transient | retry with exponential backoff + jitter |

After `OPENEYE_SYNC_MAX_RETRIES` exhausted, the batch is abandoned for this
pass. Rows remain `sync_pending=1` and are retried on the next interval.

## Idempotency

Every batch carries a fresh `X-OpenEye-Batch-Id` (UUIDv4). Your endpoint
**must** be idempotent on this header — the sidecar may retry the same batch
after a timeout even if the server actually persisted the rows.

Recommended: store `(batch_id, table)` in your warehouse and reject duplicates
with `200 OK` (no-op).

## Row shapes

The sidecar sends raw SQLite rows. Schemas:

### `visual_sessions`

```json
{
  "id": "uuid",
  "session_id": "uuid|null",
  "tenant_id": "string|null",
  "device_type": "hololens|webxr|ios|...",
  "device_id": "string|null",
  "procedure_id": "string|null",
  "procedure_name": "string|null",
  "user_id": "string|null",
  "started_at": 1715000000.0,
  "ended_at": 1715000300.0,
  "frame_count": 12,
  "step_count": 5,
  "steps_verified": 4,
  "outcome": "completed|abandoned|error",
  "metadata": "json string|null"
}
```

### `step_verifications`

```json
{
  "id": 123,
  "frame_id": 456,
  "visual_session_id": "uuid",
  "tenant_id": "string|null",
  "step_id": "step-1-assembly",
  "step_name": "string|null",
  "result": "pass|fail|uncertain",
  "confidence": 0.91,
  "reasoning": "string|null",
  "model_used": "claude-opus-4-7",
  "latency_ms": 412,
  "verified_at": 1715000123.4
}
```

### `trajectories`

```json
{
  "id": "uuid",
  "session_id": "uuid|null",
  "visual_session_id": "uuid|null",
  "tenant_id": "string|null",
  "model": "claude-opus-4-7",
  "completed": 1,
  "conversations": "json string (ShareGPT)",
  "reward_signal": 0.92,
  "tags": "json string|null",
  "created_at": 1715000400.0,
  "exported_at": null
}
```

### `skills`

```json
{
  "id": "uuid",
  "name": "hand-hygiene-check",
  "description": "string|null",
  "content": "markdown body",
  "domain": "medical",
  "use_count": 3,
  "last_used": 1715000000.0,
  "created_at": 1714000000.0,
  "source": "generated|community"
}
```

## Privacy

- Raw frame pixels never leave the device.
- Frame **descriptions** can contain PII (faces described, names spoken,
  document text read). `frames` is not synced by default.
- Step verifications carry `reasoning` strings — review your retention
  policy before enabling.
- Multi-tenant deployments: every row carries `tenant_id`. Your endpoint
  should partition by it.

## Reference implementation

A minimal FastAPI receiver:

```python
from fastapi import FastAPI, Header, HTTPException
import sqlite3

app = FastAPI()
db = sqlite3.connect("cloud.db", check_same_thread=False)
db.execute("CREATE TABLE IF NOT EXISTS seen_batches (batch_id TEXT PRIMARY KEY)")

@app.post("/ingest/{table}")
def ingest(table: str, rows: list, authorization: str = Header(...),
           x_openeye_batch_id: str = Header(...)):
    if authorization != "Bearer " + EXPECTED_KEY:
        raise HTTPException(401)
    if table not in {"visual_sessions", "step_verifications", "trajectories", "skills"}:
        raise HTTPException(400, "unknown table")
    # Idempotency check
    try:
        db.execute("INSERT INTO seen_batches VALUES (?)", (x_openeye_batch_id,))
        db.commit()
    except sqlite3.IntegrityError:
        return {"status": "duplicate", "rows": 0}
    # Persist rows to your warehouse here...
    return {"status": "ok", "rows": len(rows)}
```

## Triggering a sync manually

For testing, the sidecar exposes:

```
POST /sync/now
```

Returns a per-table count of rows pushed:

```json
{"synced": {"visual_sessions": 3, "step_verifications": 17, "trajectories": 1, "skills": 0}}
```
