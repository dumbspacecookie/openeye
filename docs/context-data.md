# Context Data Sharing

OpenEye is built by [Context](https://getcontext.info). To make procedure-
verification models better over time, OpenEye **can** ship opted-in training
data to Context. **It does not by default.** This document describes exactly
what happens when you turn it on, what we collect, what we don't, and how to
opt out or revoke.

## TL;DR

- **Default: OFF.** Nothing leaves your machine unless you set
  `OPENEYE_CONTEXT_OPTIN=true` AND `OPENEYE_CONTEXT_API_KEY=...`.
- **Only completed trajectories** with a non-null reward signal are sent.
- **Stripped before sending:** tenant IDs, user IDs, visual session IDs,
  session IDs, and system prompts.
- **Retain control:** every shipped trajectory can be re-marked as unsent
  via `POST /context/forget/{trajectory_id}` after Context confirms
  deletion, and you can disable at any time.

## How to enable

```bash
export OPENEYE_CONTEXT_OPTIN=true
export OPENEYE_CONTEXT_API_KEY=ctx-...   # request one at getcontext.info
```

Restart the sidecar. You should see:

```
[openeye] Context data sharing: ON. Trajectories with reward signals will
be shared with Context to improve future models. See docs/context-data.md
to revoke.
```

Verify status anytime:

```bash
curl http://127.0.0.1:7770/context/status
```

## How to disable

Either:

```bash
export OPENEYE_CONTEXT_OPTIN=false
```

…or unset the env var entirely. The next sidecar boot will not ship any
new trajectories. Already-sent data stays on Context unless you request
deletion (see "Right to be forgotten" below).

## What gets shipped

Per trajectory:

```json
{
  "trajectory_id": "uuid-v4",
  "schema_version": "1.0",
  "model": "claude-sonnet-4-6",
  "completed": true,
  "reward_signal": 0.92,
  "procedure_tag": "bolt-assembly",
  "conversations": [
    {"from": "human", "value": "Frame 1: operator placing bolt..."},
    {"from": "gpt", "value": "verify_step('s1', 'pass', ...)"}
  ],
  "created_at": 1715000000.0
}
```

Trajectories are batched (default 20 per call) and posted to:

```
POST https://api.getcontext.info/v1/openeye
Authorization: Bearer <your OPENEYE_CONTEXT_API_KEY>
X-OpenEye-Batch-Id: <uuid-v4 idempotency key>
X-OpenEye-Schema: 1.0
Content-Type: application/json
```

## What is NEVER shipped

OpenEye refuses to send any of the following, even if they exist in your
database. This is enforced by `_clean_for_context` in
[`sidecar/context_sync.py`](../sidecar/context_sync.py):

- `tenant_id` — your customer organization identifier
- `user_id` — end-user identifier
- `visual_session_id` / `session_id` — internal refs
- `system_prompt` — your prompt engineering / IP
- Frame descriptions (the per-frame scene text is **not** sent — only the
  final agent-trajectory conversations are, and only after system messages
  are stripped)
- Skill files
- Raw image bytes (these never enter OpenEye in the first place)

System messages are removed from `conversations` before sending. If your
agent's system prompt contains customer-specific instructions, none of
that text leaves the device.

Tags are filtered: only the procedure tag (e.g. `"bolt-assembly"`) is
retained. Meta tags like `"openeye"`, `"completed"`, `"abandoned"`,
`"error"` are dropped.

## EU users (GDPR)

If any data you collect originates in the EU, you are a data controller
under GDPR and you need lawful basis to share it with Context (the
processor). Recommended path:

1. **Do not enable Context sharing** unless the people in frame have given
   informed consent for their procedure data to be used for AI model
   improvement.
2. The developer enabling `OPENEYE_CONTEXT_OPTIN=true` is asserting that
   such consent exists. There is no in-code mechanism that proves this —
   it is your responsibility.
3. Use a tenant-scoped opt-in: enable Context sharing only for the tenants
   that have agreed to data sharing in their contract with you.

Context's role and data handling are described at
[getcontext.info/dpa](https://getcontext.info/dpa) *(this URL will be live
when Context's DPA is published — until then, contact
support@getcontext.info)*.

## Right to be forgotten

To request deletion of a previously shared trajectory:

1. Email `support@getcontext.info` with the `trajectory_id` value(s).
2. Context will remove the records and confirm.
3. On your side, reset the sync marker so OpenEye won't consider them
   "sent" anymore (in case you want to re-evaluate):

   ```bash
   curl -X POST http://127.0.0.1:7770/context/forget/<trajectory-id>
   ```

To request deletion of all data associated with your API key, email
`support@getcontext.info`.

## Verifying what's been shipped

Every Context-bound trajectory is recorded in the local
`context_sync_state` table with the batch ID it went out in:

```sql
SELECT trajectory_id, synced_at, batch_id
FROM context_sync_state
ORDER BY synced_at DESC;
```

You can audit at any time — the device retains the record even after the
data has left.

## Disabling per-tenant

The sync worker treats opt-in as global. To scope by tenant:

1. Run multiple sidecars (one per tenant set), each with its own
   `OPENEYE_HOME` directory and its own opt-in setting.
2. Or fork `sidecar/context_sync.py` and add a tenant filter to
   `_clean_for_context`.

Per-tenant opt-in as a first-class feature is on the roadmap. Open an
issue at github.com/dumbspacecookie/openeye if you need it.

## Endpoint contract (for Context backend implementers)

Context's `/v1/openeye` endpoint MUST be idempotent on `X-OpenEye-Batch-Id`.
A retry of the same batch must not double-insert.

| Status | Sidecar action |
|---|---|
| 2xx | Mark trajectories `synced` in local state |
| 4xx (terminal) | Log, do not retry, leave unsent |
| 408, 429, 5xx | Exponential backoff retry (default 3 attempts) |
| Network error | Same as 5xx |

The receiver should respond with at minimum `{"received": <count>}` on
success.

## Source of truth

If anything in this document conflicts with the code, the code wins:

- Strip list: `STRIPPED_FIELDS` in `sidecar/context_sync.py`
- Send eligibility: `_clean_for_context` in `sidecar/context_sync.py`
- Enable check: `is_enabled` in `sidecar/context_sync.py`

Pull requests welcome if you spot a gap.
