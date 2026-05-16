# Changelog

All notable changes to OpenEye are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Schema v3 with DB helpers for per-procedure reward weights, retention,
  and Context-flywheel tracking.
- Background workers: periodic retention pruning and an in-process event
  bus that fans out verdict events via SSE (`GET /sessions/{id}/events`).
- Context flywheel modules (`pii_scrub.py`, `context_sync.py`) and the
  associated server endpoints (`/context/*`, `/context/tenants/*`) for
  default-off, loud-opt-in sharing of completed trajectories with Context.
- Per-procedure reward weight configuration
  (`/procedures/reward-config`).
- Sidecar shared-secret auth middleware
  (`OPENEYE_SIDECAR_TOKEN`), structured error envelopes with request IDs,
  and a request-logging middleware.
- TypeScript helpers: `events.ts` (typed SSE consumer) and
  `frame-sampler.ts` (drop-on-overflow rate limiter).
- Reference SDKs (Kotlin, Swift) and reference Context-receiver server.
- Benchmark harness + dataset v1, community-skills library, ops dashboard.

### Fixed
- DPO export: reward=0.0 trajectories are now correctly selected as the
  rejected side of preference pairs (previous `or 0` / `or 1` logic
  treated 0.0 as falsy).
- Cloud sync: exponential-backoff retries with batch-id idempotency
  headers, plus explicit field stripping so internal IDs cannot leak.
- HuggingFace push: retry transient 5xx / network errors instead of
  failing the whole upload on the first flake.

### Changed
- Pinned `@mariozechner/pi-*` to `~0.66.0` (patch range only).
- README rewritten to match the alpha "install from source" reality
  and the bring-your-own-vision-adapter positioning.

## [0.1.0] — initial alpha

First tagged release. Not yet published to npm — install from source.
