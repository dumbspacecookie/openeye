# OpenEye Ops Dashboard

A zero-dependency static page that hits the OpenEye sidecar's existing
HTTP endpoints. No build, no bundler, no server-side component — open
`index.html` in a browser.

## What it shows

- **Sidecar health** — live status, DB path, last-seen response
- **Context sharing status** — global opt-in, consent attestation, API key, last sync error, consecutive failures
- **Tenant opt-in roster** — every tenant that's been set with opt-in/out toggle inline; add new tenants from the form
- **Recent sessions** — last 20 sessions with source, tenant, model, message + tool call counts, start time
- **Danger zone** — revoke consent, forget a trajectory's Context-sync marker

Polls every 10 seconds.

## Who this is for

Not engineers. This is the page a compliance officer or operations lead
opens to see "is the data flywheel working, who's opted in, did anything
break in the last hour." Everything they'd otherwise need to `curl` is
here.

## Run

```bash
# Option 1 — open the file directly (file:// URL)
open examples/ops-dashboard/index.html

# Option 2 — serve over HTTP if your browser blocks fetch from file://
python -m http.server 8000 -d examples/ops-dashboard/
# then open http://localhost:8000
```

## Configure

On first load:
1. Enter the sidecar URL (default `http://127.0.0.1:7770`)
2. If the sidecar has `OPENEYE_SIDECAR_TOKEN` set, paste it in the token
   field — the dashboard adds it as `Authorization: Bearer <token>` on
   every request
3. Click **connect**

Settings persist in `localStorage`.

## Security note

The dashboard is plain HTML and the token lives in `localStorage` — fine
for a single trusted operator on a workstation. **Do not deploy this on
a shared web server without TLS and access control.** The OpenEye
sidecar HTTP API is administrative and should not be reachable from
untrusted networks ([see security.md](../../docs/security.md)).

## What this does NOT do

- No historical charts — just live counts. A real ops UI would graph
  trajectories-per-hour, reward distribution, sync latency.
- No log streaming — for that, run `fly logs` (or equivalent) against
  the sidecar process.
- No per-tenant data deletion — you'd need to delete trajectories at
  the receiver, then use the "forget marker" control here.
- No multi-sidecar view — single endpoint at a time.

These are reasonable v2 features. The current scope is "what stops a
compliance officer from asking you to run a SQL query."
