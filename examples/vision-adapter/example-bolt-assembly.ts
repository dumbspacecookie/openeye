/**
 * End-to-end example: bolt assembly verification on a tablet.
 *
 * Captures (or loads) frames, describes each one with a vision adapter,
 * logs it to OpenEye, asks the agent to verify the current step, and
 * exports the session as a training trajectory.
 *
 * Run with cloud vision (default):
 *   ANTHROPIC_API_KEY=sk-ant-... node --loader tsx example-bolt-assembly.ts
 *
 * Run with local Ollama:
 *   VISION=ollama node --loader tsx example-bolt-assembly.ts
 */

import * as fs from "node:fs";
import * as path from "node:path";
import {
  OpenEyeAgent,
  setupProviders,
  makeStreamFn,
  ANTHROPIC_SONNET,
} from "@openeye/pi-openeye";

import { describeFrameWithClaude } from "./claude-vision-adapter.js";
import { describeFrameWithMoondream } from "./ollama-vision-adapter.js";

const USE_OLLAMA = process.env.VISION === "ollama";

const PROCEDURE = {
  id: "bolt-assembly-v1",
  name: "M6 Bolt Assembly — Bracket A to Frame B",
  steps: [
    { id: "s1", name: "Position bracket on frame mounting points" },
    { id: "s2", name: "Insert M6 bolt through bracket hole into frame" },
    { id: "s3", name: "Hand-tighten bolt clockwise until snug" },
    { id: "s4", name: "Torque to 12 Nm with calibrated wrench" },
  ],
};

async function describeFrame(bytes: Buffer, step: { name: string }): Promise<string> {
  const prompt =
    `Manufacturing procedure: ${PROCEDURE.name}. ` +
    `Current step: ${step.name}. ` +
    "Describe operator's hands, the tool in use, the visible component state, " +
    "and whether the operator's actions match the step description.";

  if (USE_OLLAMA) {
    return describeFrameWithMoondream(bytes, prompt);
  }
  return describeFrameWithClaude(bytes, prompt);
}

async function main(): Promise<void> {
  setupProviders();

  const agent = await OpenEyeAgent.create({
    model: ANTHROPIC_SONNET,
    streamFn: makeStreamFn(),
    systemPrompt:
      "You are a manufacturing QA assistant. You verify procedure steps " +
      "against scene descriptions captured from an operator's tablet camera. " +
      "Use verify_step with result='pass' only when the description clearly " +
      "matches the step. Use 'uncertain' when the camera angle is bad or " +
      "the action is ambiguous. Use 'fail' when the description shows a " +
      "clear deviation.",
    tenantId: "demo-factory-001",
  });

  const vsId = await agent.client.createVisualSession({
    deviceType: "android-tablet",
    procedureId: PROCEDURE.id,
    procedureName: PROCEDURE.name,
  });
  if (!vsId) throw new Error("Could not create visual session");

  // In a real app, frames come from the device camera. For this example,
  // they live on disk: ./frames/step1.jpg, ./frames/step2.jpg, etc.
  const framesDir = path.resolve(import.meta.dirname ?? __dirname, "frames");
  let sequenceNum = 0;

  for (const step of PROCEDURE.steps) {
    const framePath = path.join(framesDir, `${step.id}.jpg`);
    if (!fs.existsSync(framePath)) {
      console.warn(`Skipping ${step.id}: no frame at ${framePath}`);
      continue;
    }

    sequenceNum++;
    const bytes = fs.readFileSync(framePath);

    // 1. Vision model describes the frame
    const description = await describeFrame(bytes, step);
    console.log(`\n[${step.id}] ${description}\n`);

    // 2. OpenEye logs the description
    const frameId = await agent.client.logFrame({
      visualSessionId: vsId,
      sequenceNum,
      sceneDescription: description,
      stepContext: step.id,
    });

    // 3. Agent verifies whether the step was completed correctly
    await agent.prompt(
      `Frame ${sequenceNum} (${step.id} — ${step.name}):\n` +
        `Scene: ${description}\n\n` +
        `Call verify_step with step_id="${step.id}"${
          frameId ? `, frame_id=${frameId}` : ""
        }, visual_session_id="${vsId}", and your assessment.`,
    );
  }

  // 4. Close out the session and capture training trajectory
  await agent.client.endVisualSession(vsId, "completed");
  await agent.captureAndClose({ completed: true, visualSessionId: vsId });

  // 5. Optional: export trajectories for fine-tuning
  const exportPath = path.resolve("./bolt-assembly-trajectories.jsonl");
  const count = await agent.exportTrajectories(exportPath);
  console.log(`\nExported ${count} trajectories to ${exportPath}`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
