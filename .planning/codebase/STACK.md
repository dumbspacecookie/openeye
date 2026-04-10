# Technology Stack

**Analysis Date:** 2026-04-09

## Languages

**Primary:**
- TypeScript 5.7.3 - Core agent library and client implementation
- JavaScript (ES2022) - Runtime target for Node.js modules

**Secondary:**
- Python 3.12 - Sidecar server for persistent memory, visual sessions, and RL trajectories

## Runtime

**Environment:**
- Node.js 20.0.0+ (specified in `package.json` engines)
- Python 3.9+ (for sidecar, Python 3.12 in Docker)

**Package Manager:**
- npm (Node.js dependencies)
- pip (Python dependencies)
- Lockfile: `package-lock.json` present

## Frameworks

**Core:**
- @mariozechner/pi-agent-core 0.66.0 - Agent orchestration and state management (`src/index.ts`)
- @mariozechner/pi-ai 0.66.0 - LLM model abstraction and streaming layer (`src/models.ts`)
- FastAPI 0.110.0+ - Python async HTTP server for sidecar (`sidecar/server.py`)
- uvicorn 0.27.0+ - ASGI server for FastAPI (`sidecar/server.py`)

**Testing:**
- vitest 3.2.4 - Test runner (`package.json` scripts: `test`, `vitest --run`)

**Build/Dev:**
- TypeScript 5.7.3 - Compilation and type checking
- Node built-in modules: `child_process`, `fs`, `http`, `path`, `url`

## Key Dependencies

**Critical:**
- @mariozechner/pi-agent-core - Agent lifecycle, tool registration, event streaming
- @mariozechner/pi-ai - Multi-LLM provider abstraction (Anthropic, OpenAI, Google, Groq, Mistral, OpenRouter, Bedrock, Ollama)

**Infrastructure (TypeScript):**
- @types/node 22.0.0 - Node.js type definitions

**Infrastructure (Python):**
- fastapi - Web framework for sidecar REST API
- uvicorn[standard] - ASGI server with logging support
- sqlite3 (stdlib) - Persistent storage with FTS5 full-text search
- urllib (stdlib) - HTTP client for cloud sync and HuggingFace API

## Configuration

**Environment:**
Environment variables configure providers and deployment:
- **LLM Keys:** `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`, `GROQ_API_KEY`, `MISTRAL_API_KEY`, `OPENROUTER_API_KEY`
- **Cloud/Hub:** `OPENEYE_CLOUD_URL`, `OPENEYE_CLOUD_KEY`, `HF_TOKEN`
- **Sidecar:** `OPENEYE_HOME` (data dir), `OPENEYE_PORT` (default 7770), `OPENEYE_PYTHON` (Python executable)
- **AWS Bedrock:** `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`
- **Ollama:** `OLLAMA_BASE_URL` (default http://localhost:11434)
- **Sync:** `OPENEYE_SYNC_INTERVAL`, `OPENEYE_SYNC_BATCH`, `OPENEYE_SYNC_SESSIONS`, `OPENEYE_SYNC_VERIFICATIONS`, `OPENEYE_SYNC_TRAJECTORIES`, `OPENEYE_SYNC_SKILLS`

**Build:**
- `tsconfig.json` - TypeScript compiler options (target ES2022, NodeNext modules, strict mode, declaration maps)
- `fly.toml` - Fly.io deployment config (port 7770, `/data` volume for SQLite)
- `Dockerfile` - Multi-stage Python 3.12 build for sidecar

**Entry Points:**
- TypeScript: `src/index.ts` exports `OpenEyeAgent` class
- Python sidecar: `sidecar/server.py` FastAPI app, spawned via `uvicorn` in subprocess
- Node entry: `dist/index.js` (compiled from src)

## Platform Requirements

**Development:**
- Node.js 20.0.0+
- Python 3.9+ (3.12 recommended)
- npm/pip package managers
- Supported: Windows, macOS, Linux

**Production:**
- Deployment target: Fly.io (configured in `fly.toml`)
- Docker container option: `docker build -t openeye/sidecar .`
- Persistent storage: `/data` volume mount required for SQLite (OPENEYE_HOME)
- Headless/serverless compatible: sidecar runs as subprocess or separate container

---

*Stack analysis: 2026-04-09*
