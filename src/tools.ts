/**
 * OpenEye Pi Tools
 * Registered as AgentTool objects in the pi agent.
 * The agent calls these exactly like any other pi tool.
 */

import { Type } from "@mariozechner/pi-ai";
import type { AgentTool } from "@mariozechner/pi-agent-core";
import type { SidecarClient } from "./sidecar-client.js";

export function createOpenEyeTools(
  client: SidecarClient,
  opts: { tenantId?: string; cloudSync?: boolean } = {}
): AgentTool<any>[] {
  const tenantId = opts.tenantId;
  const cloudSync = opts.cloudSync ?? false;

  return [
    {
      name: "search_memory",
      label: "Search Memory",
      description: "FTS5 search across all past agent sessions. Use to recall previous interactions, decisions, and outcomes.",
      parameters: Type.Object({
        query: Type.String({ description: "Search query" }),
        limit: Type.Optional(Type.Number({ description: "Max results (default 20)" })),
      }),
      async execute(_toolCallId: string, params: any) {
        const results = await client.searchMemory({ query: params.query, tenantId, limit: params.limit });
        return { content: [{ type: "text" as const, text: JSON.stringify(results, null, 2) }], details: {} };
      },
    },
    {
      name: "search_frames",
      label: "Search Frames",
      description: "FTS5 search across all past frame descriptions. Use to find previous visual observations.",
      parameters: Type.Object({
        query: Type.String({ description: "Search query for frame scene descriptions" }),
        procedure_id: Type.Optional(Type.String({ description: "Filter by procedure ID" })),
        limit: Type.Optional(Type.Number({ description: "Max results (default 20)" })),
      }),
      async execute(_toolCallId: string, params: any) {
        const results = await client.searchFrames({
          query: params.query, tenantId, procedureId: params.procedure_id, limit: params.limit,
        });
        return { content: [{ type: "text" as const, text: JSON.stringify(results, null, 2) }], details: {} };
      },
    },
    {
      name: "recall_skill",
      label: "Recall Skill",
      description: "Retrieve relevant procedural skills for the current task.",
      parameters: Type.Object({
        task: Type.String({ description: "Task description to match skills against" }),
        domain: Type.Optional(Type.String({ description: "Filter by domain" })),
        top_k: Type.Optional(Type.Number({ description: "Number of skills to return (default 5)" })),
      }),
      async execute(_toolCallId: string, params: any) {
        const skills = await client.recallSkills({ task: params.task, domain: params.domain, topK: params.top_k });
        return { content: [{ type: "text" as const, text: JSON.stringify(skills, null, 2) }], details: {} };
      },
    },
    {
      name: "write_skill",
      label: "Write Skill",
      description: "Persist a new skill doc after completing a complex task. Future sessions will recall this skill automatically.",
      parameters: Type.Object({
        name: Type.String({ description: "Unique skill name (kebab-case)" }),
        content: Type.String({ description: "Skill content (markdown)" }),
        description: Type.Optional(Type.String({ description: "One-line description" })),
        domain: Type.Optional(Type.String({ description: "Domain: medical, manufacturing, field-service, or general" })),
      }),
      async execute(_toolCallId: string, params: any) {
        const result = await client.writeSkill(params);
        return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }], details: {} };
      },
    },
    {
      name: "start_visual_session",
      label: "Start Visual Session",
      description: "Begin a tracked AR/XR visual session.",
      parameters: Type.Object({
        device_type: Type.String({ description: "Device type: hololens, webxr, ios, android, snap_spectacles" }),
        procedure_id: Type.Optional(Type.String({ description: "Procedure being performed" })),
        procedure_name: Type.Optional(Type.String({ description: "Human-readable procedure name" })),
      }),
      async execute(_toolCallId: string, params: any) {
        const vsId = await client.createVisualSession({
          deviceType: params.device_type, procedureId: params.procedure_id,
          procedureName: params.procedure_name, tenantId,
        });
        return { content: [{ type: "text" as const, text: JSON.stringify({ visual_session_id: vsId }) }], details: {} };
      },
    },
    {
      name: "end_visual_session",
      label: "End Visual Session",
      description: "Close a visual session with an outcome.",
      parameters: Type.Object({
        visual_session_id: Type.String(),
        outcome: Type.Optional(Type.String({ description: "completed, abandoned, or error" })),
      }),
      async execute(_toolCallId: string, params: any) {
        await client.endVisualSession(params.visual_session_id, params.outcome);
        return { content: [{ type: "text" as const, text: JSON.stringify({ ok: true }) }], details: {} };
      },
    },
    {
      name: "log_frame",
      label: "Log Frame",
      description: "Record a frame's scene description from an AR device.",
      parameters: Type.Object({
        visual_session_id: Type.String(),
        sequence_num: Type.Number({ description: "Frame sequence number" }),
        scene_description: Type.String({ description: "Natural language description of the scene" }),
        objects_detected: Type.Optional(Type.Array(Type.String())),
        step_context: Type.Optional(Type.String({ description: "Which procedure step this frame belongs to" })),
        confidence: Type.Optional(Type.Number({ description: "Scene confidence 0-1" })),
      }),
      async execute(_toolCallId: string, params: any) {
        const frameId = await client.logFrame({
          visualSessionId: params.visual_session_id, sequenceNum: params.sequence_num,
          sceneDescription: params.scene_description, objectsDetected: params.objects_detected,
          stepContext: params.step_context, confidence: params.confidence, tenantId, cloudSync,
        });
        return { content: [{ type: "text" as const, text: JSON.stringify({ frame_id: frameId }) }], details: {} };
      },
    },
    {
      name: "verify_step",
      label: "Verify Step",
      description: "Record a step verification result — the core RL reward signal.",
      parameters: Type.Object({
        visual_session_id: Type.String(),
        step_id: Type.String({ description: "Step identifier" }),
        result: Type.Union([Type.Literal("pass"), Type.Literal("fail"), Type.Literal("uncertain")]),
        frame_id: Type.Optional(Type.Number({ description: "Frame this verification applies to" })),
        step_name: Type.Optional(Type.String({ description: "Human-readable step name" })),
        confidence: Type.Optional(Type.Number({ description: "Confidence 0-1" })),
        reasoning: Type.Optional(Type.String({ description: "Why this result was chosen" })),
      }),
      async execute(_toolCallId: string, params: any) {
        const vid = await client.logStepVerification({
          visualSessionId: params.visual_session_id, stepId: params.step_id,
          result: params.result, frameId: params.frame_id, stepName: params.step_name,
          confidence: params.confidence, reasoning: params.reasoning, tenantId, cloudSync,
        });
        return { content: [{ type: "text" as const, text: JSON.stringify({ verification_id: vid }) }], details: {} };
      },
    },
  ];
}
