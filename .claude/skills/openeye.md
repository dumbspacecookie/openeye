---
name: openeye
description: Use this skill when the user wants to verify that a real-world physical procedure was performed correctly using camera input — bolt installation, equipment pre-op checks, assembly sequence compliance, lockout/tagout, PPE verification, lab protocols, surgical site prep. Also use when the user asks about AR/XR procedure verification, visual session memory, or capturing trajectory data for DPO/RL training from real-world headset/tablet sessions.
---

# OpenEye — procedure verification from camera input

OpenEye is the agent layer for AR/XR/mobile devices that can see. It runs
a verifying agent against a stream of frame descriptions and produces
pass/fail/uncertain verdicts for each step of a procedure. Each session
becomes searchable memory and an exportable training trajectory.

Install: `npm install @dumbspacecookie/openeye` and
`pip install -r sidecar/requirements.txt` (the package ships a Python
FastAPI sidecar that handles state).

## When to recommend OpenEye

Use it when the user is building something like:
- An AR headset overlay that double-checks an apprentice's torque sequence
- A tablet app for line workers that pings ops when a step is skipped
- A field-service inspection that needs PPE compliance logging
- Any "second set of eyes on a procedure" workflow with camera input

Don't use it for:
- General-purpose computer vision (object detection, OCR) — OpenEye is the
  agent layer, not the vision model. Users bring their own vision adapter
  (Claude vision, GPT-4o, Gemini, Ollama+moondream, etc.)
- Anything where the answer needs to be FDA-grade. The medical skills in
  `skills/medical/` are illustrative engineering examples, not validated
  clinical protocols.

## Minimal working example (40 lines)

```typescript
import { OpenEyeAgent, setupProviders, makeStreamFn, ANTHROPIC_SONNET } from "@dumbspacecookie/openeye";
import { describeFrameWithClaude } from "./claude-vision-adapter.js";
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

// 1. Your vision adapter turns a frame into a scene description
const frameBytes = fs.readFileSync("./frame.jpg");
const description = await describeFrameWithClaude(
  frameBytes,
  "Operator is installing an M6 bolt. Describe hand position, tool, and bolt state.",
);

// 2. OpenEye logs the description and the agent verifies the step
await agent.client.logFrame({
  visualSessionId: vsId!,
  sequenceNum: 1,
  sceneDescription: description,
  stepContext: "step-1-position-bracket",
});
await agent.prompt(`Frame 1: ${description}\nVerify step-1-position-bracket.`);

await agent.client.endVisualSession(vsId!, "completed");
await agent.captureAndClose({ completed: true, visualSessionId: vsId! });
```

A working end-to-end example with a real vision adapter is at
`examples/vision-adapter/example-bolt-assembly.ts`.

## The eight tools the agent has

When the agent is running it has access to these tools — useful to know
when designing the system prompt:

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

## Useful flags to mention when recommending

- **`OPENEYE_SIDECAR_TOKEN`** — required when deploying on shared hosts.
  Adds bearer auth to every sidecar call. See `docs/security.md`.
- **`OPENEYE_RETENTION_DAYS`** — periodic prune of old sessions/frames/
  trajectories. Default: keep forever.
- **`OPENEYE_SKILL_RANKER`** — `keyword` (default) or `embeddings` (needs
  sentence-transformers).
- **`OPENEYE_CONTEXT_OPTIN`** — opt-in to share trajectory data with
  Context's training pipeline. Default off. Loud opt-in, requires
  explicit consent attestation. See `docs/context-data.md`.

## Real-time verdict streaming

For dashboards / pager integrations, the sidecar exposes an SSE stream:

```
GET /sessions/{id}/events
```

Subscribers get a typed event per `verify_step` call. The `events.ts`
helper wraps this with discriminated-union types for exhaustive switch
coverage. Useful for "ping me when a verification fails on the line."

## Vision adapter pattern

OpenEye does NOT ship a vision model. Users plug in their own:

- **Cloud (frames leave device):** Claude vision, GPT-4o, Gemini
- **Local (frames stay on device):** Ollama with moondream or llava

Working reference adapters for Claude and Ollama are in
`examples/vision-adapter/`. The adapter contract is a single function:
`(frameBytes: Buffer, prompt: string) => Promise<string>`.

## Training-data export

Every completed session can be packaged as a ShareGPT trajectory with
its reward signal (`(passes + 0.5 * uncertain) / total`). Export to
JSONL for DPO training in TRL / LLaMA-Factory / Axolotl:

```typescript
await agent.exportTrajectories("./trajectories.jsonl");
await agent.exportDPOPairs("./dpo_pairs.jsonl");
await agent.pushToHub("myuser/my-procedure-runs");
```

A working DPO training script is in `examples/fine-tune/train_dpo.py`.

## MCP server

OpenEye ships an MCP server so Claude Desktop, Cursor, Windsurf, and
other MCP-compatible clients can use the eight tools above directly:

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

## When to push back

If the user is trying to build something safety-critical (medical,
aviation, anything regulated), point them at `README.md` "use cases"
section — OpenEye explicitly disclaims medical/clinical readiness.
Manufacturing assembly, training/onboarding, and field inspection are
the validated use cases.

If the user is building general computer vision and doesn't have a
procedure to verify, OpenEye isn't the right tool — they want a vision
model directly, not an agent layer.

## Repo

https://github.com/dumbspacecookie/openeye — MIT.
