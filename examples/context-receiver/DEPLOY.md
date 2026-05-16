# Deploying the Context Receiver

This guide walks you from `git clone` to a working HTTPS endpoint that
OpenEye sidecars in the wild can POST trajectories to. ~30 minutes to
the first successful ingest.

The reference receiver is **not production**. See [`README.md`](README.md)
for the production-readiness gap list. This guide gets you to a
working alpha endpoint quickly so you can start collecting data while
the production version is being built.

## Option 1: Fly.io (recommended for alpha)

Free tier covers a single shared-cpu instance + 1GB volume — enough for
the first few months of opt-in data.

### Prerequisites

```bash
# Install the Fly CLI
curl -L https://fly.io/install.sh | sh
fly auth signup   # or `fly auth login` if you have an account
```

### Launch

```bash
cd examples/context-receiver

# Step 1: create the app (pick a unique name)
fly launch --copy-config --name context-receiver-yourname --no-deploy

# Step 2: create the persistent volume for SQLite
fly volumes create receiver_data --size 1 --region iad

# Step 3: set your bearer tokens
# Format: "token1:tenant1,token2:tenant2"
# Production: rotate quarterly; one token per customer
fly secrets set CONTEXT_RECEIVER_TOKENS="ctx-acme-2026q1:acme-corp,ctx-pilot-2026q1:pilot-user"

# Step 4: deploy
fly deploy
```

After `fly deploy` finishes, your endpoint is live at:
```
https://context-receiver-yourname.fly.dev/v1/openeye
```

### Verify

```bash
# Health check (no auth required)
curl https://context-receiver-yourname.fly.dev/health

# Smoke-test ingest
curl -X POST https://context-receiver-yourname.fly.dev/v1/openeye \
  -H "Authorization: Bearer ctx-acme-2026q1" \
  -H "Content-Type: application/json" \
  -d '{
    "schema_version": "1.0",
    "batch_id": "smoke-test-1",
    "trajectory_count": 1,
    "trajectories": [{
      "trajectory_id": "smoke-1",
      "model": "test",
      "completed": true,
      "reward_signal": 0.9,
      "procedure_tag": "smoke-test",
      "conversations": [{"from": "human", "value": "hello"}]
    }]
  }'
```

Expected response:
```json
{"received": 1, "duplicate": false, "batch_id": "smoke-test-1"}
```

### Point OpenEye at it

On the OpenEye side:
```bash
export OPENEYE_CONTEXT_OPTIN=true
export OPENEYE_CONTEXT_API_KEY=ctx-acme-2026q1
export OPENEYE_CONTEXT_URL=https://context-receiver-yourname.fly.dev/v1/openeye
```

Restart the OpenEye sidecar. Confirm with:
```bash
curl http://127.0.0.1:7770/context/status
```

You should see `"enabled": true` and the endpoint URL.

## Option 2: Render.com

Same idea, different provider. Render has a free tier that spins down
when idle — fine for testing but adds 30-second cold starts.

```bash
# Push the receiver to a GitHub repo, then in the Render dashboard:
# 1. New > Web Service
# 2. Connect your repo
# 3. Build command: pip install -r requirements.txt
# 4. Start command: uvicorn server:app --host 0.0.0.0 --port $PORT
# 5. Add disk: name=receiver-data, mount path=/data, size=1GB
# 6. Environment > add CONTEXT_RECEIVER_TOKENS and CONTEXT_RECEIVER_DB=/data/context_receiver.db
```

## Option 3: Your own server

```bash
# On any VPS with Docker:
git clone https://github.com/dumbspacecookie/openeye.git
cd openeye/examples/context-receiver
docker build -t context-receiver .

docker run -d --name context-receiver \
  -p 8080:8080 \
  -v context-data:/data \
  -e CONTEXT_RECEIVER_TOKENS="ctx-prod:tenant-a" \
  -e CONTEXT_RECEIVER_DB=/data/context_receiver.db \
  --restart unless-stopped \
  context-receiver
```

Put Caddy or nginx in front for TLS and HTTPS.

## Operational notes

### Backups

The SQLite database is at `/data/context_receiver.db` inside the
container. Back up with:

```bash
# Fly.io: SSH in and copy out
fly ssh console -C "sqlite3 /data/context_receiver.db .dump" > backup.sql

# Restore
fly ssh console
sqlite3 /data/context_receiver.db < backup.sql
```

For production, swap SQLite for Postgres (Fly has managed Postgres) and
use point-in-time recovery.

### Monitoring

Three things to watch:

1. **Ingest rate** — `SELECT COUNT(*), MAX(received_at) FROM trajectories` —
   if no inserts in 6 hours and your alpha has active devs, something is broken.
2. **Schema drift** — `SELECT DISTINCT schema_version FROM batches` — if you
   ever see anything other than `1.0`, an OpenEye sidecar is sending newer
   data than your receiver knows about.
3. **Auth failures** — Fly logs `401` responses with `fly logs`. Spikes mean
   token rotation issues or someone probing the endpoint.

### Scaling beyond the reference

The reference receiver maxes out at maybe 50 req/s on a shared-cpu Fly
instance. Real scaling needs:

- Postgres (or Snowflake / BigQuery / Iceberg if you're going straight to a lake)
- An async ingest queue (the HTTP handler returns 202 instantly, a worker
  consumes the queue)
- Per-token rate limiting
- A real auth system

The contract (`docs/context-data.md`) stays the same. Only the
implementation behind `/v1/openeye` needs to change.

### Rotating tokens

```bash
# Generate a new one
fly secrets set CONTEXT_RECEIVER_TOKENS="ctx-acme-2026q2:acme-corp,$(fly secrets list -j | jq -r ...)"

# Tell the customer to update OPENEYE_CONTEXT_API_KEY
# Wait until their next sidecar restart
# Then remove the old token from CONTEXT_RECEIVER_TOKENS
```

The receiver does not currently support overlapping tokens with grace
periods — production should add that.

### Right-to-be-forgotten

OpenEye supports DSAR via the `DELETE /v1/openeye/trajectories/{id}`
endpoint. To process a request:

```bash
TRAJ=abc-123-uuid
curl -X DELETE https://context-receiver-yourname.fly.dev/v1/openeye/trajectories/$TRAJ \
  -H "Authorization: Bearer ctx-acme-2026q1"
```

Then tell the customer to reset the OpenEye-side sync marker so the row
doesn't reappear:
```bash
curl -X POST http://127.0.0.1:7770/context/forget/$TRAJ
```

For hard delete (vs. the receiver's default soft-delete), add a cron job:
```sql
DELETE FROM trajectories
WHERE deleted_at IS NOT NULL
  AND deleted_at < (strftime('%s', 'now') - 30*24*3600);  -- 30-day grace
```

## When to outgrow this

Move off the reference receiver and onto the production Context backend
when ANY of these is true:

- You have >10 active OpenEye deployments contributing data
- Total trajectories exceeds 100k
- A customer asks about SOC2 / DPAs
- You need to actually train models on the data (this receiver doesn't
  ship data anywhere — you'd export the SQLite db manually)

Until then, this is enough.
