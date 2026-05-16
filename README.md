# OpenEye

> **alpha software.** the package is not on npm yet — install from source
> (see below). interfaces may change. file issues at
> github.com/dumbspacecookie/openeye.

every AR headset maker — HoloLens, Snap Spectacles, Apple, Android — ships a device that can see. none of them ship a brain. every developer building on these platforms has to figure out the intelligence layer themselves, from scratch, every time, for every device. the work doesn't compound. what one team learns on HoloLens doesn't help the team building on WebXR. every deployment is a silo.

OpenEye is the shared brain.

a thin piece of software sits on the device, captures what the camera sees, and turns it into a natural-language description of the scene. that description goes to OpenEye. OpenEye runs an AI agent with tools for verifying procedure steps, recalling prior sessions, and writing down what it learns. each session becomes structured memory the agent can search later, and an exportable training trajectory you can fine-tune on.

OpenEye doesn't ship a vision model — **you bring your own**. drop in Claude vision, GPT-4o, Gemini, Groq Llama vision, or a local Ollama model with moondream/llava. a working reference adapter for both cloud and local is in [`examples/vision-adapter/`](examples/vision-adapter/).

---

## install

OpenEye isn't on npm yet. install directly from the repo:

```bash
git clone https://github.com/dumbspacecookie/openeye.git
cd openeye
npm install
npm run build
pip install -r sidecar/requirements.txt
```

then link or import from your project:

```bash
npm link                # in the openeye/ directory
npm link @openeye/pi-openeye   # in your project
```

or reference it locally:

```json
"dependencies": {
  "@openeye/pi-openeye": "file:../path/to/openeye"
}
```

---

## quick start

```typescript
import { OpenEyeAgent, setupProviders, makeStreamFn, ANTHROPIC_SONNET } from "@openeye/pi-openeye";
import { describeFrameWithClaude } from "./examples/vision-adapter/claude-vision-adapter.js";
import * as fs from "node:fs";

setupProviders();

const agent = await OpenEyeAgent.create({
  model: ANTHROPIC_SONNET,
  streamFn: makeStreamFn(),
  systemPrompt: "You are a procedure assistant. Verify steps with precision.",
  tenantId: "your-org",
});

const vsId = await agent.client.createVisualSession({
  deviceType: "android-tablet",
  procedureId: "bolt-assembly-v1",
  procedureName: "M6 Bolt Assembly",
});

// 1. vision adapter (you bring this) turns a camera frame into text
const frameBytes = fs.readFileSync("./frame.jpg");
const description = await describeFrameWithClaude(
  frameBytes,
  "Operator is installing an M6 bolt. Describe hand position, tool, and bolt state.",
);

// 2. OpenEye logs the description and lets the agent verify the step
const frameId = await agent.client.logFrame({
  visualSessionId: vsId!,
  sequenceNum: 1,
  sceneDescription: description,
  stepContext: "step-1-position-bracket",
});

await agent.prompt(`Frame 1: ${description}\nVerify step-1-position-bracket.`);

await agent.client.endVisualSession(vsId!, "completed");
await agent.captureAndClose({ completed: true, visualSessionId: vsId! });
```

no API key for the agent? run it locally with Ollama:

```bash
ollama pull llama3.3
```

```typescript
import { ollamaModel } from "@openeye/pi-openeye";
const agent = await OpenEyeAgent.create({ model: ollamaModel("llama3.3"), streamFn: makeStreamFn() });
```

---

## what this gives you

| capability | OpenEye |
|---|---|
| agent runtime + tool calling | yes |
| persistent session memory (FTS5) | yes |
| visual frame logging + search | yes |
| step verification with pass/fail/uncertain outcomes | yes |
| training data export (ShareGPT JSONL) | yes |
| DPO preference pair export | yes |
| HuggingFace dataset push | yes |
| MCP server (Claude Desktop, Cursor) | yes |
| reference vision adapters (Claude + Ollama) | yes |
| vision model | **no — you bring this** |
| hosted cloud | **no — bring your own ingest endpoint** ([contract](docs/cloud-sync.md)) |

what makes this useful is the loop: a session generates pass/fail outcomes against real procedure steps, those outcomes become a reward signal, and the full conversation gets packaged as a ShareGPT trajectory ready for DPO training in TRL, LLaMA-Factory, or Axolotl. you supply the fine-tuning pipeline — OpenEye supplies the data.

---

## how it works

when a frame arrives from a device, your vision adapter describes it in plain language. that description goes to the AI agent. the agent has access to a set of tools:

| tool | what it does |
|---|---|
| `search_memory` | FTS5 search across all past agent sessions |
| `search_frames` | FTS5 search across all past frame descriptions |
| `recall_skill` | retrieve relevant procedural skills for the current task |
| `write_skill` | persist a new skill doc after completing a complex task |
| `start_visual_session` | begin a tracked AR/XR session |
| `end_visual_session` | close a visual session |
| `log_frame` | record a frame's scene description |
| `verify_step` | record a step result — pass / fail / uncertain |

step verifications become the reward signal: `reward = (passes + 0.5 × uncertain) / total`. at the end of a session the whole conversation gets packaged into a ShareGPT trajectory, ready for any DPO-compatible trainer.

**important**: the reward signal reflects the agent's own judgments against scene descriptions, not external ground truth. to use this as real RL data, you should periodically validate trajectories against a human-labeled subset, or use it as supervised data rather than treating it as objective truth. fine-tuning on this raw signal alone risks training the model to be confidently wrong.

---

## use cases

**lead use cases (production-ready):**

**manufacturing & assembly** — bolt installation verification, equipment pre-operation checks, assembly sequence compliance, visual QC at line stations. android tablet or AR overlay over the work area. low regulatory burden, B2B procurement appetite.

**training & onboarding** — any procedure where a trainee needs a second set of eyes that remembers everything it's ever seen. apprentice mechanics, new line operators, lab technicians. mistakes during training don't cost much, which makes this the safest first deployment.

**field service & inspection** — lockout/tagout compliance, PPE verification, pre-job safety checklists, equipment inspection. technicians already carry phones. a sample skill file for LOTO ships in `skills/field-service/`.

**not yet recommended:**

**medical / surgical** — the technical pieces work, but anything influencing surgical decisions is subject to FDA 510(k)/De Novo review. the example skills in `skills/medical/` are illustrative starting points written by an engineer, **not validated clinical protocols**. don't use them as compliance baselines without independent medical and regulatory review.

---

## training data and HuggingFace

```typescript
// export training trajectories
const count = await agent.exportTrajectories("./trajectories.jsonl");

// export DPO preference pairs (TRL/Axolotl-compatible)
const pairs = await agent.exportDPOPairs("./dpo_pairs.jsonl");

// push directly to HuggingFace
const result = await agent.pushToHub("myuser/my-procedure-runs", {
  tags: ["procedure-verification", "bolt-assembly"],
});
console.log(`published ${result.pushed} trajectories to ${result.url}`);
```

---

## MCP server

use OpenEye's tools from Claude Desktop, Cursor, Windsurf, or any MCP-compatible client:

```json
{
  "mcpServers": {
    "openeye": {
      "command": "python3",
      "args": ["sidecar/mcp_server.py"],
      "env": { "ANTHROPIC_API_KEY": "sk-ant-..." }
    }
  }
}
```

all 8 tools are available immediately.

---

## supported agent models

swap with one line, no other code changes:

| provider | env var |
|---|---|
| Anthropic (Claude Opus, Sonnet, Haiku) | `ANTHROPIC_API_KEY` |
| Groq (Llama 3.3 — fastest, free tier) | `GROQ_API_KEY` |
| Ollama (local, no key, no cost) | just `ollama pull llama3.3` |
| OpenAI, Google, Mistral, Bedrock, OpenRouter | see `src/models.ts` |
| Any OpenAI-compatible endpoint | pass `baseUrl` + `apiKey` to `customModel()` |

---

## data and privacy

raw frame pixels never leave the device **only if your vision adapter runs on-device**. the Ollama adapter in `examples/vision-adapter/` keeps pixels local; the Claude/OpenAI/Gemini cloud adapters do not. **pick the right one for your deployment**.

what OpenEye itself stores is the natural-language description of what your vision adapter saw — not the image. opt-in cloud sync is off by default and configurable per data type. every record is scoped to a tenant ID so a single deployment can serve multiple organisations with complete data isolation between them.

opting into cloud sync? OpenEye doesn't run a hosted backend — you operate the receiving endpoint. see [`docs/cloud-sync.md`](docs/cloud-sync.md) for the HTTP contract, retry semantics, idempotency requirements, and row schemas.

### sharing data back to Context (loud opt-in)

OpenEye is built by [Context](https://getcontext.info). To make
procedure-verification models better over time, OpenEye can ship
opted-in trajectory data — completed sessions with their reward signals —
to Context for training. **This is off by default.** Nothing leaves your
machine until you set:

```bash
export OPENEYE_CONTEXT_OPTIN=true
export OPENEYE_CONTEXT_API_KEY=ctx-...
```

What gets shipped: trajectory ID, model used, reward signal, procedure
tag, and the agent conversation (with system prompts stripped). What
**never** gets shipped: tenant IDs, user IDs, system prompts, visual
session IDs, skill files, or any raw frame descriptions.

Full disclosure including EU/GDPR notes and revocation procedure:
[`docs/context-data.md`](docs/context-data.md).

Check status anytime:
```bash
curl http://127.0.0.1:7770/context/status
```

---

## deploy

**local** (default — sidecar starts automatically as a subprocess):

```bash
git clone https://github.com/dumbspacecookie/openeye.git
cd openeye && npm install && npm run build
pip install -r sidecar/requirements.txt
```

**Docker**:

```bash
docker run -d -p 7770:7770 -v openeye-data:/data \
  -e ANTHROPIC_API_KEY=sk-ant-... \
  --name openeye openeye/sidecar
```

---

## configuration

| env var | default | description |
|---|---|---|
| `OPENEYE_HOME` | `~/.openeye` | data directory (SQLite DB + skills + consent marker) |
| `OPENEYE_PORT` | `7770` | sidecar HTTP port |
| `OPENEYE_BIND_HOST` | `127.0.0.1` | sidecar bind address (don't change unless you read [docs/security.md](docs/security.md)) |
| `OPENEYE_SIDECAR_TOKEN` | — | optional shared secret for sidecar HTTP auth — required on shared hosts |
| `OPENEYE_PYTHON` | `python3` | Python executable |
| `OPENEYE_WORKERS` | `1` | uvicorn workers — leave at 1 unless you've moved off SQLite |
| `OPENEYE_LOG_LEVEL` | `INFO` | sidecar log level |
| `OPENEYE_CLOUD_URL` | — | your cloud endpoint for opt-in sync ([cloud-sync.md](docs/cloud-sync.md)) |
| `OPENEYE_CLOUD_KEY` | — | bearer token for cloud sync |
| `OPENEYE_SYNC_INTERVAL` | `60` | cloud sync interval in seconds |
| `OPENEYE_SYNC_MAX_RETRIES` | `4` | retry attempts per batch on transient failure |
| `OPENEYE_CONTEXT_OPTIN` | — | set `true` to enable Context training-data sharing ([context-data.md](docs/context-data.md)) |
| `OPENEYE_CONTEXT_API_KEY` | — | your Context API key |
| `OPENEYE_CONTEXT_CONSENT_CONFIRMED` | — | set `true` (CI only) to skip the consent attestation prompt |
| `OPENEYE_CONTEXT_SYNC_INTERVAL` | `300` | Context sync interval in seconds |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama endpoint for local vision/agent |

---

## tests

```bash
# Python sidecar tests (56 passing)
python -m pytest tests/ -v

# TypeScript integration tests
npm test

# TypeScript typecheck
npm run typecheck

# Validate community skills
python sidecar/validate_skill.py skills/
```

---

## project structure

```
openeye/
├── src/                  # TypeScript source (agent, client, models, tools)
├── sidecar/              # Python backend (FastAPI server, SQLite state, skills)
├── skills/               # Example skill protocols (manufacturing, field-service, medical*)
├── examples/             # Reference adapters and end-to-end demos
│   └── vision-adapter/   # Claude + Ollama vision adapters
├── eval/                 # Evaluation benchmark
├── docs/                 # Operator docs (cloud-sync contract, etc.)
├── schemas/              # JSON Schema for skill front matter
├── tests/                # Python + TypeScript tests
├── dist/                 # Compiled TypeScript output
├── package.json
├── tsconfig.json
└── Dockerfile
```

*medical skills are illustrative engineering examples, not validated clinical protocols. see "use cases" above.

---

## license

MIT — built on [pi-mono](https://github.com/badlogic/pi-mono) (MIT) and [hermes-agent](https://github.com/NousResearch/hermes-agent) (MIT).

---

*— dumbspacecookie*
