# OpenEye

every AR headset maker — HoloLens, Snap Spectacles, Apple, Android — ships a device that can see. none of them ship a brain. every developer building on these platforms has to figure out the intelligence layer themselves, from scratch, every time, for every device. the work doesn't compound. what one team learns on HoloLens doesn't help the team building on WebXR. every deployment is a silo.

OpenEye is the shared brain.

a thin piece of software sits on the device, captures what the camera sees, and sends a description of it to OpenEye. OpenEye figures out what's happening, verifies whether a step in a procedure was completed correctly, and sends back a structured answer. it works on any device that has a camera. it doesn't need to be retrained for each one.

the part that makes it compound: every time a user opts in, their session becomes a lesson. the model learns from real-world outcomes — not synthetic data, not preference ratings, but actual pass/fail results from people doing real procedures in the real world. the more it's used, the better it gets at the specific domain it's being used in.

---

## install

```bash
npm install @openeye/pi-openeye
pip install fastapi "uvicorn[standard]"
```

---

## quick start

```typescript
import { OpenEyeAgent, setupProviders, makeStreamFn, ANTHROPIC_SONNET } from "@openeye/pi-openeye";

setupProviders();

const agent = await OpenEyeAgent.create({
  model: ANTHROPIC_SONNET,
  streamFn: makeStreamFn(),
  systemPrompt: "You are an AR procedure assistant. Verify steps with precision.",
  tenantId: "your-org",
});

const vsId = await agent.client.createVisualSession({
  deviceType: "hololens",
  procedureId: "my-procedure-001",
  procedureName: "My Procedure",
});

const frameId = await agent.client.logFrame({
  visualSessionId: vsId!,
  sequenceNum: 1,
  sceneDescription: "operator placing component A into slot B, both hands visible",
  objectsDetected: ["component-a", "slot-b", "hands"],
  stepContext: "step-1-assembly",
  confidence: 0.91,
});

await agent.prompt(`Frame 1: component A placed in slot B. Verify step-1-assembly.`);

await agent.client.endVisualSession(vsId!, "completed");
await agent.captureAndClose({ completed: true, visualSessionId: vsId! });
```

no API key? run it locally with Ollama — free, no account:

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
| RL training data export (ShareGPT) | yes |
| DPO preference pair export | yes |
| image → description (no vision pipeline needed) | yes |
| data flywheel: sessions improve future sessions | yes |
| HuggingFace dataset push | yes |
| MCP server (Claude Desktop, Cursor) | yes |

the key distinction is **visually grounded RL signal**. a trajectory where an agent correctly identified a procedure step by looking at what was happening in the room is qualitatively better training data than one where it answered a text question correctly. that distinction compounds.

---

## how it works

when a frame arrives from a device, it gets described in plain language — not stored as an image. that description goes to the AI agent. the agent has access to a set of tools:

| tool | what it does |
|---|---|
| `search_memory` | FTS5 search across all past agent sessions |
| `search_frames` | FTS5 search across all past frame descriptions |
| `recall_skill` | retrieve relevant procedural skills for the current task |
| `write_skill` | persist a new skill doc after completing a complex task |
| `start_visual_session` | begin a tracked AR/XR session |
| `end_visual_session` | close a visual session |
| `log_frame` | record a frame's scene description |
| `verify_step` | record a step result — the core RL reward signal |

those step verification results — pass, fail, uncertain — become the reward signal. at the end of a session, the whole conversation gets packaged into a training trajectory in ShareGPT format, ready for any DPO-compatible trainer (TRL, LLaMA-Factory, Axolotl).

---

## use cases

**medical and surgical** — step verification in surgical training, laparoscopic procedure coaching, sterile field compliance.

**manufacturing and QA** — equipment pre-operation checks, assembly verification, visual quality control.

**field service and inspection** — lockout/tagout compliance, PPE verification, safety checklist completion.

**training and onboarding** — any domain where a human needs a second set of eyes that remembers everything it's ever seen and gets better at the specific job over time.

---

## training data and HuggingFace

```typescript
// export training trajectories
const count = await agent.exportTrajectories("./trajectories.jsonl");

// export DPO preference pairs (TRL/Axolotl-compatible)
const pairs = await agent.exportDPOPairs("./dpo_pairs.jsonl");

// push directly to HuggingFace
const result = await agent.pushToHub("myuser/my-procedure-runs", {
  tags: ["procedure-verification", "hand-hygiene"],
});
console.log(`published ${result.pushed} trajectories to ${result.url}`);
```

---

## mem0 compatibility

if you're already using mem0 or Zep, the migration is two lines:

```typescript
import { Memory } from "@openeye/pi-openeye/memory";
const memory = new Memory({ userId: "dr-chen", tenantId: "city-hospital" });
await memory.start();
await memory.add("user completed step 3");
const results = await memory.search("step 3");
```

`.add()`, `.search()`, `.getAll()`, `.delete()`, `.update()` all work the same way.

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

## supported models

swap with one line, no other code changes:

| provider | env var |
|---|---|
| Anthropic (Claude Opus, Sonnet, Haiku) | `ANTHROPIC_API_KEY` |
| OpenAI (GPT-4.1, o3, o4-mini) | `OPENAI_API_KEY` |
| Google (Gemini 2.5 Pro, 2.0 Flash) | `GEMINI_API_KEY` |
| Groq (Llama 3.3 — fastest, free tier) | `GROQ_API_KEY` |
| Mistral (EU data residency) | `MISTRAL_API_KEY` |
| AWS Bedrock (Claude via IAM) | `AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY` |
| OpenRouter (200+ models, one key) | `OPENROUTER_API_KEY` |
| Ollama (local, no key, no cost) | just `ollama pull llama3.3` |
| Any OpenAI-compatible endpoint | pass `baseUrl` + `apiKey` to `customModel()` |

---

## deploy

**local** (default — sidecar starts automatically as a subprocess):

```bash
npm install @openeye/pi-openeye
pip install fastapi "uvicorn[standard]"
```

**Docker**:

```bash
docker run -d -p 7770:7770 -v openeye-data:/data \
  -e ANTHROPIC_API_KEY=sk-ant-... \
  --name openeye openeye/sidecar
```

**Fly.io**:

```bash
fly launch --name openeye-sidecar
fly volumes create openeye_data --size 1
fly secrets set ANTHROPIC_API_KEY=sk-ant-...
fly deploy
```

---

## data and privacy

raw frame pixels never leave the device. what gets stored is the natural-language description of what the camera sees — not the image itself. opt-in cloud sync is off by default and configurable per data type. every record is scoped to a tenant ID so a single deployment can serve multiple organisations with complete data isolation between them.

---

## configuration

| env var | default | description |
|---|---|---|
| `OPENEYE_HOME` | `~/.openeye` | data directory (SQLite DB + skills) |
| `OPENEYE_PORT` | `7770` | sidecar HTTP port |
| `OPENEYE_PYTHON` | `python3` | Python executable |
| `OPENEYE_CLOUD_URL` | — | cloud endpoint for opt-in sync |
| `OPENEYE_CLOUD_KEY` | — | API key for cloud sync |
| `OPENEYE_SYNC_INTERVAL` | `60` | background sync interval in seconds |
| `OPENEYE_VISION_MODEL` | — | vision model for image-native logFrame |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama endpoint |

---

## tests

```bash
# Python sidecar tests
python -m pytest tests/test_sidecar.py -v

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
├── skills/               # Community skill protocols (medical, manufacturing, field-service)
├── eval/                 # Evaluation benchmark (100 examples, deterministic)
├── schemas/              # JSON Schema for skill front matter
├── tests/                # Python + TypeScript tests
├── dist/                 # Compiled TypeScript output
├── package.json
├── tsconfig.json
└── Dockerfile
```

---

## license

MIT — built on [pi-mono](https://github.com/badlogic/pi-mono) (MIT) and [hermes-agent](https://github.com/NousResearch/hermes-agent) (MIT).

---

*— dumbspacecookie*
