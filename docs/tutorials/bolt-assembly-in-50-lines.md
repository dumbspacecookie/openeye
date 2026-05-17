# An AR procedure verifier in 50 lines

*A tutorial. Copy-paste runnable. Uses OpenEye + Ollama (so it's free
and fully local) or Claude vision (so it's better and costs $0.01).*

I needed a thing that watches someone bolt together a part and tells
them when they've done it wrong. The naive version is: throw frames at
GPT-4o, ask "did they do step 3 correctly," log the answer. That works
but it forgets every frame the second the request returns. No memory.
No reward signal. No way to learn from the 200th run that the operators
keep skipping the same step.

OpenEye is the thin layer that turns that one-shot LLM call into a
loop with memory + verdicts + exportable training data. Here's the
50-line version.

## what you need

- node 20+
- python 3.10+ (for the sidecar)
- ollama with moondream pulled (`ollama pull moondream`) — or an
  ANTHROPIC_API_KEY if you want Claude vision instead
- a frame from a camera. literally a jpg of someone holding a wrench.

## install

```bash
npm install @dumbspacecookie/openeye
pip install -r node_modules/@dumbspacecookie/openeye/sidecar/requirements.txt
```

The Python sidecar handles state (FTS5 over SQLite for memory search,
visual session tracking, trajectory capture). It auto-spawns when you
create an agent; you don't have to start it yourself.

## the code

```typescript
import {
  OpenEyeAgent,
  setupProviders,
  makeStreamFn,
  ANTHROPIC_SONNET,
} from "@dumbspacecookie/openeye";
import { describeFrameWithMoondream } from "./ollama-vision-adapter.js";
import * as fs from "node:fs";

setupProviders();

const agent = await OpenEyeAgent.create({
  model: ANTHROPIC_SONNET,
  streamFn: makeStreamFn(),
  systemPrompt:
    "You verify bolt-assembly steps. Be precise. If a step is unclear, " +
    "use verify_step with result='uncertain' — never guess.",
  tenantId: "shop-floor-1",
});

const vsId = await agent.client.createVisualSession({
  deviceType: "android-tablet",
  procedureId: "m6-bolt-assembly-v1",
  procedureName: "M6 bolt assembly",
});

const STEPS = [
  { id: "step-1", what: "position the bracket flush against the rail" },
  { id: "step-2", what: "thread the M6 bolt by hand, two full turns" },
  { id: "step-3", what: "torque to 8 Nm with a calibrated wrench" },
];

for (const [i, step] of STEPS.entries()) {
  const frameBytes = fs.readFileSync(`./frames/step-${i + 1}.jpg`);
  const description = await describeFrameWithMoondream(
    frameBytes,
    `Operator should be: ${step.what}. Describe hand position, tool, bolt state.`,
  );

  await agent.client.logFrame({
    visualSessionId: vsId!,
    sequenceNum: i + 1,
    sceneDescription: description,
    stepContext: step.id,
  });

  await agent.prompt(
    `Frame ${i + 1}: ${description}\n\nVerify ${step.id} (${step.what}).`,
  );
}

await agent.client.endVisualSession(vsId!, "completed");
await agent.captureAndClose({ completed: true, visualSessionId: vsId! });

// One file. Every step's verdict, the agent's reasoning, the reward
// signal — ready for DPO training in TRL or LLaMA-Factory.
await agent.exportTrajectories("./trajectory.jsonl");
```

That's it. 50 lines, 3 verifications, one exported training trajectory.

## what just happened

For each frame, the vision adapter (Moondream running locally on
Ollama, or Claude if you swapped it) generated a scene description.
OpenEye's agent loop received that description and called `verify_step`
with `pass`, `fail`, or `uncertain`. Every verdict went into FTS5 memory
— next time you boot the agent it can `search_memory` for "M6 bolt"
and find prior runs.

When the session closed, OpenEye packaged the whole conversation as a
ShareGPT trajectory with reward = `(passes + 0.5 * uncertain) / total`.
That JSONL file is now training-ready for DPO fine-tuning. Run
`examples/fine-tune/train_dpo.py` against it and you've got a model
that's better at verifying YOUR procedures than the base model was.

## the loop that compounds

The reason this is interesting and not just "another agent wrapper":
every session generates ground truth (the step IDs and your written
expectations) AND a model judgment (the verdict). The deltas between
them are training signal. The 200th run is better than the first
because the model has seen 199 worth of your specific procedure data.

You bring the vision model, your shop floor, your procedures. OpenEye
brings the memory + verdict + trajectory loop. Get a hundred opted-in
deployments and the next OpenEye-shipped base model is meaningfully
better at AR procedure verification than anything off the shelf.

## next steps

- swap moondream for Claude vision: change one import, set
  `ANTHROPIC_API_KEY`. Quality jumps; cost goes from $0 to ~$0.01/frame.
- wire the SSE event bus to a Slack pager: every `verify_step` with
  `fail` lands in #shop-floor-alerts. Subscribe to
  `GET /sessions/{id}/events`, filter on result, post.
- ship a tablet build: the same code runs on an android-tablet
  Capacitor app. The vision adapter takes a camera frame instead of a
  filesystem read.

## links

- repo: https://github.com/dumbspacecookie/openeye
- npm: https://www.npmjs.com/package/@dumbspacecookie/openeye
- vision adapters (Claude + Ollama, both working):
  `examples/vision-adapter/` in the repo
- fine-tune script: `examples/fine-tune/train_dpo.py`
- the loud opt-in disclosure for training data sharing:
  `docs/context-data.md`

MIT licensed, alpha, bring your own vision model. Mostly harmless.
