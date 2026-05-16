# changelog

what's new, what broke, what got better. semver-ish — i'll break things
before 1.0, that's the whole point of 0.x.

> *"in the beginning the Universe was created. this has made a lot of
> people very angry and been widely regarded as a bad move."*
> — DNA. also approximately how it felt building this.

## unreleased

new:
- schema v3 + per-procedure reward weights (`/procedures/reward-config`)
- retention worker (`OPENEYE_RETENTION_DAYS`) + `/retention/prune-now`
- sse event bus — tail verdicts in real time at `/sessions/{id}/events`
- context flywheel (loud opt-in, default off, see docs/context-data.md)
- tenant opt-in roster — `/context/tenants/*`, default-deny
- sidecar auth — `OPENEYE_SIDECAR_TOKEN`, structured errors w/ request ids
- ts: frame sampler (drops on overflow, doesn't queue) + typed sse client
- kotlin + swift sdk stubs in `sdk/`
- reference receiver in `examples/context-receiver/` + admin DSAR endpoints
- benchmark v1 + community-skills + ops dashboard

fixed:
- dpo: reward=0.0 was treated as falsy by `or 0` / `or 1` and skipped.
  perfect failures are real, not absent
- cloud-sync: exp-backoff retries + batch-id idempotency + strip internal
  fields even if your receiver is sloppy
- hf push: same retry treatment — large uploads were dying on the first 500

changed:
- pinned `@mariozechner/pi-*` to `~0.66.0` (patch range). caret bit me
- readme rewritten — alpha-honest, install-from-source, bring your own vision

## 0.1.0 — initial alpha

first tag. not on npm yet, install from source. mostly harmless.
