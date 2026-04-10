# External Integrations

**Analysis Date:** 2026-04-09

## APIs & External Services

**LLM Providers:**
- **Anthropic** - Claude models (Opus, Sonnet, Haiku)
  - SDK/Client: @mariozechner/pi-ai (abstraction layer)
  - Auth: `ANTHROPIC_API_KEY` env var
  - Models: `src/models.ts` exports `ANTHROPIC_OPUS`, `ANTHROPIC_SONNET`, `ANTHROPIC_HAIKU`

- **OpenAI** - GPT-4.1, o3, o4-mini
  - SDK/Client: @mariozechner/pi-ai (via OpenAI-compatible endpoint)
  - Auth: `OPENAI_API_KEY` env var
  - Models: `src/models.ts` exports `OPENAI_GPT41`, `OPENAI_GPT41_MINI`, `OPENAI_O4_MINI`, `OPENAI_O3`

- **Google** - Gemini 2.5 Pro, 2.0 Flash
  - SDK/Client: @mariozechner/pi-ai
  - Auth: `GEMINI_API_KEY` env var
  - Models: `src/models.ts` exports `GOOGLE_GEMINI_25_PRO`, `GOOGLE_GEMINI_20_FLASH`

- **Groq** - Llama 3.3 70B, Llama 3.1 8B
  - SDK/Client: @mariozechner/pi-ai
  - Auth: `GROQ_API_KEY` env var
  - Models: `src/models.ts` exports `GROQ_LLAMA33_70B`, `GROQ_LLAMA31_8B`

- **Mistral** - Large, Small models
  - SDK/Client: @mariozechner/pi-ai
  - Auth: `MISTRAL_API_KEY` env var
  - Models: `src/models.ts` exports `MISTRAL_LARGE`, `MISTRAL_SMALL`

- **OpenRouter** - Access to 100+ models
  - SDK/Client: @mariozechner/pi-ai via OpenRouter proxy
  - Auth: `OPENROUTER_API_KEY` env var
  - Function: `openRouterModel(modelSlug)` in `src/models.ts`
  - Pre-configured: `OR_DEEPSEEK_V3`, `OR_LLAMA4_MAVERICK`, `OR_QWEN3_235B`

- **AWS Bedrock** - Claude models via AWS
  - SDK/Client: @mariozechner/pi-ai (boto3 backend)
  - Auth: `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION` env vars
  - Models: `src/models.ts` exports `BEDROCK_CLAUDE_SONNET`, `BEDROCK_CLAUDE_OPUS`

- **Ollama** - Local/self-hosted models
  - SDK/Client: @mariozechner/pi-ai via OpenAI-compatible endpoint
  - Auth: None required (local)
  - Configuration: `OLLAMA_BASE_URL` env var (default: http://localhost:11434)
  - Function: `ollamaModel(modelId, opts)` in `src/models.ts`
  - Pre-configured: `OLLAMA_LLAMA33`, `OLLAMA_QWEN25_VL`, `OLLAMA_LLAVA`, `OLLAMA_MISTRAL_NEMO`

**Custom OpenAI-compatible:**
- Function: `customModel(modelId, opts)` in `src/models.ts` allows pointing to any OpenAI-compatible endpoint
- Auth: `baseUrl` and optional `apiKey` in options

## Data Storage

**Databases:**
- SQLite 3 (local/embedded)
  - Path: `$OPENEYE_HOME/openeye.db` (default: ~/.openeye/openeye.db)
  - Client: Node `sidecar-client.ts` communicates with Python FastAPI sidecar
  - Schema: `sidecar/state.py` defines tables for sessions, messages, visual_sessions, frames, step_verifications, trajectories, skills
  - Features: FTS5 full-text search for memory/frame recall, WAL mode for concurrency, sync_pending flags for cloud sync

**Optional: PostgreSQL** (mentioned in `sidecar/requirements.txt`)
- Swap SQLite for Postgres in multi-worker deployments
- Requires: `asyncpg>=0.29.0`, `sqlalchemy[asyncio]>=2.0.0` (commented out in requirements)

**File Storage:**
- Local filesystem only - trajectories exported as JSONL to specified output paths
- Methods: `exportTrajectories(outputPath)`, `exportDPOPairs(outputPath)` in `src/sidecar-client.ts`

**Caching:**
- In-memory agent state via pi-agent-core
- No Redis/Memcached integration detected
- Skill recall uses FTS5 similarity matching in SQLite

## Authentication & Identity

**Auth Provider:**
- Custom (no external auth service)
- Multi-tenant isolation via `tenantId` parameter passed through tools (`src/tools.ts`, `src/index.ts`)
- User tracking: `userId` field in sessions and visual sessions for audit

**Implementation:**
- API keys stored in environment variables (no secrets backend detected)
- SidecarClient HTTP requests use standard Bearer token for cloud sync (`Authorization: Bearer {CLOUD_KEY}`)
- Tenant-based access control at application level (filtered by tenant_id in queries)

## Monitoring & Observability

**Error Tracking:**
- Not detected (no Sentry/Rollbar integration)

**Logs:**
- Python sidecar: configured via `logging.basicConfig` in `sidecar/server.py` (INFO level)
- Output: stderr with `[openeye-sidecar]` prefix (`src/sidecar-client.ts` line 140-144)
- TypeScript: stderr logging via `process.stderr.write` for sidecar lifecycle events

**Event Tracking:**
- Agent events: `onEvent` callback in `OpenEyeAgentOptions` (`src/index.ts` line 74)
- All agent lifecycle events published to callback (if provided)
- Session messages persisted to SQLite for audit trail

## CI/CD & Deployment

**Hosting:**
- Fly.io (primary deployment target via `fly.toml`)
- Docker containerization supported (`Dockerfile`)
- Can also run as Node subprocess + Python process locally

**CI Pipeline:**
- Not detected (no GitHub Actions/.gitlab-ci.yml files found)
- npm scripts available: `npm run build`, `npm run test`, `npm run typecheck`

**Deployment Config:**
- `fly.toml` specifies app name `openeye-sidecar`, region `iad`, port `7770`, HTTPS enforced
- Docker Compose not used; sidecar spawned as subprocess or separate container
- Volume mount: `/data` for persistent SQLite storage

## Environment Configuration

**Required env vars:**
- One of: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`, `GROQ_API_KEY`, `MISTRAL_API_KEY`, `OPENROUTER_API_KEY` (depending on chosen LLM)
- For HuggingFace integration: `HF_TOKEN` (for pushing trajectories)
- For cloud sync: `OPENEYE_CLOUD_URL`, `OPENEYE_CLOUD_KEY` (opt-in)
- For Bedrock: `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`

**Optional env vars:**
- `OPENEYE_HOME` - Data directory (default: ~/.openeye)
- `OPENEYE_PORT` - Sidecar HTTP port (default: 7770)
- `OPENEYE_PYTHON` - Python executable (default: python3)
- `OLLAMA_BASE_URL` - Ollama endpoint (default: http://localhost:11434)
- `OPENEYE_SYNC_INTERVAL` - Cloud sync interval in seconds (default: 60)
- `OPENEYE_SYNC_BATCH` - Rows per sync batch (default: 50)
- `OPENEYE_SYNC_SESSIONS`, `OPENEYE_SYNC_VERIFICATIONS`, `OPENEYE_SYNC_TRAJECTORIES`, `OPENEYE_SYNC_SKILLS` - Enable/disable specific table sync

**Secrets location:**
- Environment variables (`.env` file supported via Node dotenv pattern, not committed)
- See `env.example` for template

## Webhooks & Callbacks

**Incoming:**
- `/health` - Sidecar health check (`sidecar/server.py`)
- REST endpoints for session management, frame logging, step verification, skill operations
- Full list: `/sessions/create`, `/sessions/{id}/end`, `/sessions/{id}/messages`, `/search/messages`, `/search/frames`, `/visual-sessions/create`, `/visual-sessions/{id}/end`, `/frames/log`, `/steps/log`, `/skills/write`, `/skills/recall`, `/skills/context`, `/trajectories/capture`, `/trajectories/export`, `/trajectories/export-dpo`, `/trajectories/push-to-hub`

**Outgoing:**
- HuggingFace Hub API - Push trajectories to dataset repos via HTTPS (`sidecar/huggingface.py`)
  - Endpoint: `https://huggingface.co/api`
  - Auth: Bearer token from `HF_TOKEN` env var
  - Methods: Dataset card creation, JSONL upload

- OpenEye Cloud - Optional cloud sync (`sidecar/cloud_sync.py`)
  - Base URL: `OPENEYE_CLOUD_URL` env var
  - Auth: Bearer token from `OPENEYE_CLOUD_KEY`
  - Endpoint: `{OPENEYE_CLOUD_URL}/ingest/{table_name}`
  - Syncs: visual_sessions, step_verifications, trajectories, skills (configurable)
  - Pattern: Batch POST of rows marked `sync_pending=1`

---

*Integration audit: 2026-04-09*
