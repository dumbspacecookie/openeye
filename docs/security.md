# Security Notes

The OpenEye sidecar is designed for **localhost-only** operation. The
TypeScript client spawns it on `127.0.0.1`, talks to it over loopback,
and shuts it down when the parent process exits. Under that deployment
the sidecar's HTTP surface is unreachable from the network.

This document covers the few cases where that default isn't enough.

## Default posture

- **Bind host:** `127.0.0.1`. The sidecar refuses connections from other
  interfaces unless you override `OPENEYE_BIND_HOST`.
- **Auth:** none required by default. The localhost-only bind is the
  security boundary.
- **TLS:** none. The sidecar speaks HTTP, not HTTPS. Loopback traffic
  doesn't transit the network so this is fine for the default case.

## When the default isn't enough

### Multi-user shared host

If OpenEye runs on a host shared by multiple OS users (a desktop with
multiple logins, a Jupyter server, a build agent), any local process can
reach `127.0.0.1:7770` and read every tenant's sessions, frames, and
trajectories. There is no per-OS-user isolation.

**Mitigation:** set a shared secret.

```bash
export OPENEYE_SIDECAR_TOKEN="$(openssl rand -hex 32)"
```

When set:
- The sidecar requires `Authorization: Bearer $OPENEYE_SIDECAR_TOKEN`
  on every request (except `/health`).
- The TypeScript client picks up the same env var and sends the header
  automatically.
- Other processes on the box can still reach the port but can't read
  or write data.

You can also pass the token programmatically:

```typescript
const agent = await OpenEyeAgent.create({
  sidecarToken: process.env.MY_TOKEN, // or a fresh random value
  // ...
});
```

### Containerized deployments

If OpenEye runs inside Docker / Kubernetes and you want to reach the
sidecar from another container in the same pod, you'll need to bind to
`0.0.0.0`. **Always set `OPENEYE_SIDECAR_TOKEN` when you do this.**

```bash
docker run -d -p 7770:7770 \
  -e OPENEYE_BIND_HOST=0.0.0.0 \
  -e OPENEYE_SIDECAR_TOKEN=changeme \
  -e ANTHROPIC_API_KEY=sk-ant-... \
  openeye/sidecar
```

Without the token, port 7770 becomes a data-leak surface for anyone with
network access to the container.

### Public exposure

**Do not expose the sidecar directly to the internet.** It has no rate
limiting, no input sanitization beyond what FastAPI + Pydantic provide,
and no audit logging. If you need a public surface, put a reverse proxy
(Caddy, nginx, Cloudflare) in front of it with:
- TLS termination
- Rate limiting per token
- Request size limits
- A WAF if you have customers in jurisdictions that require it

## Sidecar concurrency

The sidecar uses SQLite with WAL mode and serializes all writes through
a single `threading.Lock`. This is the load-bearing assumption that makes
the schema correct without a multi-statement transaction layer.

Consequence: the sidecar runs with `--workers 1` by default. The
`OPENEYE_WORKERS` env var exists for symmetry but emitting a warning if
set to anything else, because multiple uvicorn workers will fight for the
SQLite write lock and produce inconsistent state under load.

If you need real multi-worker concurrency, swap SQLite for Postgres
(commented stub in `sidecar/requirements.txt`).

## Reporting

Found a security issue? Email `security@getcontext.info` rather than
opening a public issue. We'll respond within 72 hours.
