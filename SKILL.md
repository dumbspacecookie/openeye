---
name: openeye
description: Use this skill when the user wants to verify that a real-world physical procedure was performed correctly using camera input — bolt installation, equipment pre-op checks, lockout/tagout, PPE verification, lab protocols, surgical site prep. Also use when the user asks about AR/XR procedure verification, persistent visual session memory, or capturing trajectory data for DPO/RL training from real-world headset or tablet sessions.
license: MIT
metadata:
  author: dumbspacecookie
  repository: https://github.com/dumbspacecookie/openeye
  npm: "@dumbspacecookie/openeye"
---

# OpenEye — procedure verification from camera input

You are an expert at helping users wire up procedure verification for
AR/XR/mobile camera streams using OpenEye. You know when OpenEye fits,
when it doesn't, and how to compose its eight tools into a working loop.

## When to activate

- User is building an AR/XR/tablet app that watches someone perform a
  physical procedure and verifies each step (manufacturing assembly,
  field-service inspection, training/onboarding, lab protocols).
- User mentions: "procedure verification," "AR overlay for assembly,"
  "second set of eyes for an operator," "verify a bolt was torqued,"
  "check PPE compliance," "log frames from a tablet camera."
- User wants persistent memory across AR sessions — search "what did
  we see last shift on the M6 bolt line."
- User wants to capture training data (ShareGPT trajectories with
  reward signals) for fine-tuning a domain-specific verifier model
  via DPO in TRL / LLaMA-Factory / Axolotl.
- User is integrating MCP tools for memory + visual session tracking
  into Claude Desktop / Cursor / Windsurf.

Do **not** activate when:
- User wants general computer vision (object detection, OCR, scene
  understanding without a procedure to verify) — point them at a
  vision model directly.
- User is building anything FDA-regulated or safety-critical that
  would influence surgical/clinical decisions. The example medical
  skills in OpenEye are illustrative engineering, not validated
  clinical protocols.

## Instructions

1. **Confirm the use case fits.** Ask what procedure they're verifying
   and what device captures the frames (headset, tablet, phone). If
   it's not a discrete-step procedure, OpenEye is the wrong tool.

2. **Pick the vision adapter.** OpenEye does NOT ship a vision model.
   The user brings one:
   - Cloud (frames leave the device): Claude vision, GPT-4o, Gemini
   - Local (frames stay on the device): Ollama with moondream or llava
   Both reference adapters live at `examples/vision-adapter/` in the repo.

3. **Install:**
   ```bash
   npm install @dumbspacecookie/openeye
   pip install -r node_modules/@dumbspacecookie/openeye/sidecar/requirements.txt
   ```
   The Python sidecar (FastAPI + SQLite) handles state and auto-spawns
   when the agent is created — the user does not start it manually.

4. **Wire the minimal loop:**
   ```typescript
   import { OpenEyeAgent, setupProviders, makeStreamFn, ANTHROPIC_SONNET }
     from "@dumbspacecookie/openeye";
   setupProviders();
   const agent = await OpenEyeAgent.create({
     model: ANTHROPIC_SONNET,
     streamFn: makeStreamFn(),
     systemPrompt: "Verify procedure steps with precision. Use verify_step uncertain when unclear — never guess.",
     tenantId: "your-org",
   });
   const vsId = await agent.client.createVisualSession({
     deviceType: "android-tablet",
     procedureId: "bolt-assembly-v1",
     procedureName: "M6 Bolt Assembly",
   });
   // For each frame: describe via adapter, log to OpenEye, prompt the agent
   ```
   A working 50-line example is at
   `examples/vision-adapter/example-bolt-assembly.ts`.

5. **Use the eight tools available to the agent:**
   - `search_memory` — FTS5 across past sessions
   - `search_frames` — FTS5 across past frame descriptions
   - `recall_skill` — retrieve a relevant skill doc
   - `write_skill` — persist a new skill after a complex task
   - `start_visual_session` / `end_visual_session` — bracket an AR session
   - `log_frame` — record a frame's scene description
   - `verify_step` — record pass / fail / uncertain for a step

6. **Configure env vars when relevant:**
   - `OPENEYE_SIDECAR_TOKEN` — required on shared hosts (auth between
     TS client and Python sidecar). See `docs/security.md`.
   - `OPENEYE_RETENTION_DAYS` — periodic data pruning. Default: keep forever.
   - `OPENEYE_SKILL_RANKER` — `keyword` (default) or `embeddings`.
   - `OPENEYE_CONTEXT_OPTIN=true` — opt in to share trajectory data
     with Context's training pipeline. Default OFF. Loud opt-in by
     design — point the user at `docs/context-data.md` first.

7. **Export training data when the user is ready to fine-tune:**
   ```typescript
   await agent.exportTrajectories("./trajectories.jsonl");      // ShareGPT
   await agent.exportDPOPairs("./dpo_pairs.jsonl");             // TRL-ready
   await agent.pushToHub("user/my-procedure-runs");             // HF Hub
   ```
   A working DPO training script is at `examples/fine-tune/train_dpo.py`.

8. **Real-time pager / dashboard integration:** subscribe to
   `GET /sessions/{id}/events` (SSE). Each `verify_step` call emits an
   event with a discriminated-union type. Filter on `result === "fail"`
   to ping ops when something goes wrong on the line.

9. **MCP client integration (Claude Desktop, Cursor, Windsurf):**
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

## When to push back on the user

- Safety-critical / medical / aviation / FDA-regulated → point them at
  the README "use cases" section. Manufacturing assembly, training,
  and field inspection are the validated lanes.
- "I want OpenEye to do object detection" → wrong tool. They want a
  vision model directly, not the agent layer on top of one.
- "Can I get OpenEye to share my data automatically?" → no.
  `OPENEYE_CONTEXT_OPTIN=false` by default, banner-driven opt-in, GDPR
  by design. Don't help anyone bypass the consent gate.
