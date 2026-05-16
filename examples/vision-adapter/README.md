# Vision Adapter — turning camera frames into scene descriptions

OpenEye's `logFrame()` takes a `sceneDescription` string, not a pixel buffer.
**You bring the vision model.** This directory shows the two practical
patterns: cloud vision (any image-capable LLM) and local vision (Ollama).

The two pieces of code below have identical interfaces. Drop either one in
front of `agent.client.logFrame(...)` and the rest of OpenEye works
unchanged.

---

## Pattern A — Claude vision (cloud, recommended for prototyping)

`claude-vision-adapter.ts`

Pros: highest description quality out of the box, same key you already use
for the agent, no extra infrastructure.
Cons: per-frame cost, network dependency, frames leave the device.

```typescript
import { describeFrameWithClaude } from "./claude-vision-adapter.js";

const description = await describeFrameWithClaude(
  jpegBuffer,
  "Operator is performing bolt installation. Describe hand position, tool, and bolt state.",
);
await agent.client.logFrame({
  visualSessionId: vsId,
  sequenceNum: i,
  sceneDescription: description,
});
```

## Pattern B — Ollama moondream (local, recommended for production / privacy)

`ollama-vision-adapter.ts`

Pros: no per-frame cost, no network egress, raw pixels never leave the
machine, runs on consumer hardware. Matches the "raw frame pixels never
leave the device" claim literally.
Cons: lower description quality than Claude/GPT-4V, you have to install
Ollama and pull the model once.

Setup:

```bash
ollama pull moondream  # ~2GB, runs on CPU
```

Usage:

```typescript
import { describeFrameWithMoondream } from "./ollama-vision-adapter.js";

const description = await describeFrameWithMoondream(jpegBuffer, prompt);
```

---

## Which one to use

| Stage | Recommendation |
|---|---|
| Prototype / desktop demo | Claude vision (Pattern A) — fastest path to a working video |
| On-device (HoloLens, tablet, embedded) | Ollama moondream (Pattern B) — runs offline, no leaks |
| Mixed | Send every Nth frame to Claude for higher fidelity; describe the rest locally |

## What this is not

This adapter does **not** validate that the description is correct. The
description is whatever the vision model says it sees. Garbage in, garbage
out. For safety-critical procedures, your verification logic should:

1. Cross-check across multiple frames (does the description stay
   consistent over 3 seconds?)
2. Require minimum confidence (drop frames where the model expresses
   uncertainty in the description)
3. Use the `verify_step` `uncertain` result liberally — it counts as 0.5
   reward, not 0, so the model learns when to abstain

## Beyond these two

Any image-capable model works the same way. Common drop-ins:

| Provider | Model | Notes |
|---|---|---|
| OpenAI | `gpt-4o` or `gpt-4o-mini` | Use `messages[].content` with `image_url` |
| Google | `gemini-2.0-flash` | Cheapest cloud option |
| Groq | `llama-3.2-90b-vision` | Fast, free tier |
| Anthropic Bedrock | Claude via AWS | If you need IAM-scoped access |
| Ollama | `llava`, `llama3.2-vision` | Local alternatives to moondream |

The pattern is always: image bytes in → text description out → pass to
`agent.client.logFrame()`.
