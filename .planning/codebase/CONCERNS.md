# Codebase Concerns

**Analysis Date:** 2026-04-09

## Tech Debt

**Silent JSON parse failures:**
- Issue: HTTP responses with invalid JSON are silently resolved as empty objects `{}`, masking network or server errors
- Files: `src/sidecar-client.ts:56-59`
- Impact: Failed API calls appear successful, leading to null return values that propagate silently. Difficult to debug production issues.
- Fix approach: Log parse errors, return error objects instead of `{}`, or throw on invalid responses from known-good endpoints

**Loose type casting with `as any`:**
- Issue: Widespread use of `as any` type assertions (47 occurrences) to interface with pi framework types
- Files: `src/index.ts:57,62,138,154,157,164-165`, `src/memory.ts:60,77`, `src/models.ts:79,89,92,106,124`
- Impact: Type safety lost at integration boundaries. Runtime errors may occur if framework structures change unexpectedly.
- Fix approach: Create type definitions for pi framework interfaces or negotiate stricter types upstream

**Non-throwing error handling in sidecar client:**
- Issue: All HTTP calls return `null` on failure rather than throwing. Callers must check for null at every site.
- Files: `src/sidecar-client.ts:176-188`
- Impact: Easy to miss null checks. Silent failures accumulate (null sessionId → no memory persistence, null trajId → no training data captured).
- Fix approach: Consider returning result objects `{ ok: boolean; error?: string; data?: T }` or throwing on critical failures only

**Insufficient error context in logs:**
- Issue: Error logs only include `${err}` without stack traces or structured data
- Files: `src/sidecar-client.ts:185`
- Impact: Hard to diagnose issues in production. Error type and root cause lost.
- Fix approach: Log full stack traces, error type, endpoint details, and request body size

## Known Bugs

**Session creation silently fails if sidecar unavailable:**
- Symptoms: `sessionId` is null, no memory persistence, no training data exported
- Files: `src/index.ts:120-128`
- Trigger: Sidecar startup timeout expires or Python/uvicorn not installed
- Workaround: Check `client.isReady()` before calling `prompt()` or `captureAndClose()`

**No validation of HTTP response structure:**
- Symptoms: Missing fields in sidecar responses cause null/undefined values downstream
- Files: `src/sidecar-client.ts:196-200`, `src/memory.ts:60-66,77-82`
- Trigger: Sidecar version mismatch or partial response from network interruption
- Workaround: None. Requires restart.

**Memory class uses deprecated FTS5 wildcard query:**
- Symptoms: `getAll()` with query `"*"` may not work in all SQLite versions or may be inefficient
- Files: `src/memory.ts:69-76`
- Trigger: Call to `memory.getAll()`
- Workaround: Use `search()` with a broad query instead

**Sidecar process lifecycle not fully cleaned on error:**
- Symptoms: Python process may remain running if Node crashes or times out
- Files: `src/sidecar-client.ts:134-160`
- Trigger: Process error after spawn but before `start()` returns
- Workaround: Manual `pkill -f "uvicorn server:app"`

## Security Considerations

**API keys exposed in environment variable loading:**
- Risk: `process.env` reads are synchronous and keys are held in memory. No explicit clearing on agent shutdown.
- Files: `src/models.ts:104,125,253`, `env.example` (full list)
- Current mitigation: Keys loaded lazily by `makeStreamFn()`, not stored in agent
- Recommendations:
  - Consider securely clearing env keys after sidecar startup
  - Add warning in docs about setting `HF_TOKEN` in `.env` files (should be in environment only)
  - Validate that sensitive env vars are never logged (already done, but worth documenting)

**HTTP sidecar on localhost without authentication:**
- Risk: Any local process can call sidecar endpoints, potentially reading memory/trajectories or pushing unauthorized data to HuggingFace
- Files: `src/sidecar-client.ts:42,127-131`
- Current mitigation: Port 7770 only binds to 127.0.0.1, not 0.0.0.0
- Recommendations:
  - Add optional API key validation at sidecar startup
  - Document isolation guarantees for multi-tenant setups
  - Consider Unix socket instead of TCP for local deployment

**Tenant isolation not validated on client side:**
- Risk: Client code passes `tenantId` as parameter, but no client-side validation. Sidecar must enforce isolation.
- Files: `src/index.ts:124,132`, `src/tools.ts:15,28,43,89,123,145`
- Current mitigation: Sidecar enforces tenant scoping in SQL queries (assumed)
- Recommendations:
  - Add client-side assertion: throw if tenantId is empty string or undefined in production mode
  - Add integration test verifying tenant isolation
  - Document that sidecar *must* validate tenantId in all queries

**Cloud sync credentials passed as query parameter:**
- Risk: Cloud API key (`OPENEYE_CLOUD_KEY`) potentially logged in sidecar stdout/stderr
- Files: `env.example:15`, sidecar cloud_sync.py (not inspected)
- Current mitigation: Only present if explicitly set in env
- Recommendations:
  - Ensure sidecar never logs cloud keys to stdout/stderr
  - Use header-based auth instead of query parameters for cloud sync calls

## Performance Bottlenecks

**Blocking sidecar startup on every agent creation:**
- Problem: `OpenEyeAgent.create()` spawns Python subprocess synchronously, waits 8 seconds with polling every 200ms. Blocks entire agent thread.
- Files: `src/index.ts:106-107`, `src/sidecar-client.ts:95-159`
- Cause: No connection pooling. Each agent creation incurs full Python startup + uvicorn boot.
- Improvement path:
  - Cache sidecar singleton and reuse across agent instances
  - Use signal/event waiting instead of polling (reduce 8s wait from 40 polls to 1 signal)
  - Parallelize sidecar boot with skills context loading

**Memory search with FTS5 has no query result limits at SQLite layer:**
- Problem: Large FTS5 result sets are fetched entirely then truncated in Python, not limited in SQL
- Files: `src/memory.ts:54-66`, `src/sidecar-client.ts:220-225`
- Cause: Default LIMIT 20, but no index hints or query optimization visible
- Improvement path:
  - Add explicit SQLite LIMIT in sidecar queries
  - Profile FTS5 performance on 10k+ session corpus
  - Consider pagination API for large result sets

**No connection pooling for HTTP requests:**
- Problem: Each sidecar call creates new HTTP connection, no keep-alive
- Files: `src/sidecar-client.ts:41-65`
- Cause: Using raw `http.request()` without agent
- Improvement path:
  - Use HTTP keep-alive with agent
  - Consider gRPC or binary protocol for high-volume deployments

**No batch API for logging multiple frames/verifications:**
- Problem: Each `logFrame()` or `logStepVerification()` call is one HTTP request
- Files: `src/tools.ts:119-126,141-148`
- Cause: Agent tool execution is serial, one per tool call
- Improvement path:
  - Add batch endpoints to sidecar for logging multiple frames in one request
  - Buffer pending logs and flush on session end

## Fragile Areas

**Sidecar client initialization logic:**
- Files: `src/sidecar-client.ts:95-159`
- Why fragile:
  - Multiple fallback code paths (attach to running, spawn new, fail quietly)
  - No validation that Python/uvicorn are installed before spawn attempt
  - `waitReady()` polls with hardcoded 200ms interval, may miss health check if timing unlucky
  - Sidecar crash after `_ready = true` is not detected
- Safe modification:
  - Add `checkPreflight()` call before spawn
  - Implement backoff exponential strategy instead of fixed interval
  - Add health check subscription after startup completes
  - Test coverage gap: no test for "sidecar dies after ready"

**Agent event subscription with loose typing:**
- Files: `src/index.ts:154-171`
- Why fragile:
  - Agent event type not validated, using `(event as any).message`
  - Content array unpacking has no length checks
  - Filter and map assume text block structure without validation
- Safe modification:
  - Create proper `AgentEvent` type with discriminated union for event types
  - Add guard checks: `if (msg.content?.length > 0) { ...map... }`
  - Use optional chaining throughout: `Array.isArray(msg.content) && msg.content.filter(...)`

**Memory class mapping assumptions:**
- Files: `src/memory.ts:60-66,77-82`
- Why fragile:
  - Assumes sidecar response always contains `session_id`, `snippet`, `content`, `timestamp`
  - Falls back with `r.id ?? r.session_id ?? i` — if all null, uses array index as ID
  - Score calculation `1 / (1 + i)` has no semantic meaning, hardcoded
- Safe modification:
  - Add response validation schema
  - Define explicit type for sidecar response
  - Deprecate `getAll()` with wildcard query; use typed pagination

**No version negotiation with sidecar:**
- Files: `src/index.ts` (no sidecar version check), `src/sidecar-client.ts` (no version header)
- Why fragile:
  - Breaking changes in sidecar API go unnoticed until runtime failures
  - Each client method assumes endpoint exists without validation
- Safe modification:
  - Add `/version` endpoint to sidecar
  - Check version at `SidecarClient.start()`, warn or error if incompatible

**Test coverage of null paths:**
- Files: `src/index.ts:153-172`, `src/sidecar-client.ts:181-188`
- Why fragile:
  - Only happy path tested (`isReady() = true`)
  - No tests for `sessionId = null`, `result = null` behavior
  - Error handling in event subscription untested
- Safe modification:
  - Add test: agent created with sidecar unavailable
  - Add test: null sessionId → prompt/captureAndClose do not crash
  - Add test: sidecar response missing required fields

## Scaling Limits

**Single Python process sidecar:**
- Current capacity: ~100 concurrent agent instances (estimated based on SQLite write lock)
- Limit: SQLite can handle ~100 writers before lock contention becomes severe
- Scaling path:
  - Sidecar currently single-process (`--workers 1` hardcoded in spawn args)
  - Multi-worker setup requires separate DB or shared write queue
  - Consider PostgreSQL backend for multi-worker deployment

**Memory search performance with FTS5:**
- Current capacity: ~10k sessions before search latency exceeds 100ms
- Limit: FTS5 indexes grow without automatic pruning
- Scaling path:
  - Add archival/cleanup API to sidecar
  - Use `VACUUM` and index analysis periodically
  - Profile with realistic dataset before production rollout

**HTTP polling for sidecar startup:**
- Current capacity: One agent startup per 8 seconds (single-threaded polling)
- Limit: 5-10 agents/minute on node thread
- Scaling path:
  - Parallelize startup (create multiple agents concurrently)
  - Use named pipes or Unix sockets to avoid TCP overhead
  - Cache singleton sidecar, don't restart per agent

## Dependencies at Risk

**@mariozechner/pi-agent-core@0.66.0:**
- Risk: Upstream dependency locked to exact version. No security updates unless manually updated.
- Impact: If vulnerability found in pi-ai or pi-agent-core, OpenEye cannot patch without major refactor
- Current mitigation: Using caret (`^0.66.0`), allows patch upgrades
- Migration plan:
  - Monitor releases at https://github.com/badlogic/pi-mono/releases
  - Add CI check to flag when upstream minor versions lag behind latest
  - Consider abstracting pi interface to allow alternative runtimes

**Node version requirement (>=20.0.0):**
- Risk: Node 20 LTS support ends in October 2026. No plan for migration path.
- Impact: Future deployments may require old Node version or face compatibility issues
- Current mitigation: Node 22 LTS available; package works with 22+
- Migration plan:
  - Update engines field to `>=20.0.0` (already correct)
  - Test on Node 22 in CI
  - Plan migration to Node 22 LTS when 20 enters maintenance

**Python sidecar with no version pinning:**
- Risk: No `requirements.txt` or `Dockerfile` specifies Python version or dependency versions
- Impact: Sidecar may break on Python 3.13+ or when FastAPI drops old Python support
- Current mitigation: Checked in sidecar/ directory with dependencies implicit
- Migration plan:
  - Create `requirements.txt` with pinned versions
  - Add CI test against Python 3.9, 3.10, 3.11, 3.12
  - Document minimum Python version

## Missing Critical Features

**No graceful degradation if sidecar unavailable:**
- Problem: Agent still accepts calls and silently drops data. No user-facing warning.
- Blocks: Production deployments need fallback behavior or clear failure signals
- Recommendation: Add `--strict-mode` flag to throw on sidecar unavail instead of silent nulls

**No data export/import for backup/migration:**
- Problem: SQLite database in `~/.openeye/` is not part of public API
- Blocks: Users cannot back up their procedures, migrate between machines, or switch cloud providers
- Recommendation: Add `exportDatabase()` and `importDatabase()` methods to Memory class

**No query timeout or result size limits:**
- Problem: `searchMemory()` with broad query could return 10k results, blocking agent
- Blocks: Large deployments risk memory exhaustion
- Recommendation: Add `timeout` and `maxResults` parameters to search methods

**No streaming API for large exports:**
- Problem: `exportTrajectories()` and `exportDPOPairs()` buffer entire result set
- Blocks: Exporting 100k trajectories may cause OOM
- Recommendation: Add streaming export API returning AsyncIterable

## Test Coverage Gaps

**No integration tests for full agent lifecycle:**
- What's not tested: Create agent → prompt → captureAndClose → export trajectory
- Files: `tests/runner.test.ts` only mocks sidecar
- Risk: Breaking changes in OpenEyeAgent surface go undetected
- Priority: High — this is the core user-facing API

**No error path testing:**
- What's not tested: Sidecar unavailable, timeout, invalid responses, session creation failure
- Files: `tests/runner.test.ts` mocks all success cases
- Risk: Silent failures in production
- Priority: High — error handling is critical

**No end-to-end test with real Python sidecar:**
- What's not tested: Actual HTTP communication, sidecar startup, SQLite persistence
- Files: No tests spawn real sidecar
- Risk: Integration bugs only appear in production
- Priority: Medium — requires Python test environment

**No test for event subscription data loss:**
- What's not tested: If message appending fails, does agent still run? Data consistency?
- Files: `src/index.ts:154-171` has no tests
- Risk: Silent data loss if sidecar crashes mid-turn
- Priority: Medium

**No concurrent agent test:**
- What's not tested: Multiple agents sharing sidecar, SQLite lock behavior
- Files: No concurrency tests
- Risk: Locking issues only appear under load
- Priority: Medium — scaling concern

---

*Concerns audit: 2026-04-09*
