/**
 * @dumbspacecookie/openeye
 *
 * Persistent memory, visual session tracking, and RL trajectory capture
 * for the pi agent. Built for OpenEye AR/XR computer vision backend.
 *
 * Usage:
 *   import { OpenEyeAgent, setupProviders, ANTHROPIC_SONNET, makeStreamFn } from "@dumbspacecookie/openeye";
 *
 *   setupProviders();
 *
 *   const agent = await OpenEyeAgent.create({
 *     model: ANTHROPIC_SONNET,
 *     streamFn: makeStreamFn(),
 *     systemPrompt: "You are an AR procedure assistant.",
 *     tenantId: "acme-hospital",
 *   });
 *
 *   await agent.prompt("Verify step 3 in procedure XR-07");
 *   await agent.captureAndClose({ completed: true });
 */

import { Agent } from "@mariozechner/pi-agent-core";
import type { AgentEvent, AgentTool } from "@mariozechner/pi-agent-core";
import type { Model } from "@mariozechner/pi-ai";
import { SidecarClient, type SidecarClientOptions } from "./sidecar-client.js";
import { createOpenEyeTools } from "./tools.js";

// Re-export everything public
export { SidecarClient } from "./sidecar-client.js";
export type { SidecarClientOptions, VerifyResult } from "./sidecar-client.js";
export { createOpenEyeTools } from "./tools.js";
export {
  setupProviders, makeStreamFn,
  // Anthropic
  ANTHROPIC_OPUS, ANTHROPIC_SONNET, ANTHROPIC_HAIKU,
  // OpenAI
  OPENAI_GPT41, OPENAI_GPT41_MINI, OPENAI_O4_MINI, OPENAI_O3,
  // Google
  GOOGLE_GEMINI_25_PRO, GOOGLE_GEMINI_20_FLASH,
  // Groq
  GROQ_LLAMA33_70B, GROQ_LLAMA31_8B,
  // Mistral
  MISTRAL_LARGE, MISTRAL_SMALL,
  // OpenRouter
  openRouterModel, OR_DEEPSEEK_V3, OR_LLAMA4_MAVERICK, OR_QWEN3_235B,
  // Bedrock
  BEDROCK_CLAUDE_SONNET, BEDROCK_CLAUDE_OPUS,
  // Ollama
  ollamaModel, OLLAMA_LLAMA33, OLLAMA_QWEN25_VL, OLLAMA_LLAVA, OLLAMA_MISTRAL_NEMO,
  // Custom
  customModel,
} from "./models.js";

export interface OpenEyeAgentOptions {
  /** pi model to use — import from models.ts or pass your own */
  model: Model<any>;
  /**
   * streamFn wires the model to the LLM API, including API key injection.
   * Use makeStreamFn() for automatic key-from-env behaviour.
   */
  streamFn?: (model: Model<any>, context: any, options?: any) => any;
  /** System prompt */
  systemPrompt?: string;
  /** Tenant ID for multi-tenant data isolation */
  tenantId?: string;
  /** Flag step verifications and trajectories for cloud sync */
  cloudSync?: boolean;
  /** Extra pi tools beyond the built-in OpenEye set */
  extraTools?: AgentTool[];
  /** Options for the Python sidecar */
  sidecar?: SidecarClientOptions;
  /** Called on every agent lifecycle event */
  onEvent?: (event: AgentEvent) => void;
}

export class OpenEyeAgent {
  private _agent: Agent;
  private _client: SidecarClient;
  private _sessionId: string | null;
  private _model: string;
  private _systemPrompt: string;
  private _tenantId?: string;
  private _cloudSync: boolean;

  private constructor(
    agent: Agent, client: SidecarClient, sessionId: string | null,
    model: string, systemPrompt: string, tenantId?: string, cloudSync?: boolean
  ) {
    this._agent = agent;
    this._client = client;
    this._sessionId = sessionId;
    this._model = model;
    this._systemPrompt = systemPrompt;
    this._tenantId = tenantId;
    this._cloudSync = cloudSync ?? false;
  }

  /**
   * Create and initialise an OpenEyeAgent.
   * Starts the Python sidecar, injects relevant past skills into the system
   * prompt, and registers all OpenEye tools on the agent.
   */
  static async create(opts: OpenEyeAgentOptions): Promise<OpenEyeAgent> {
    // Boot sidecar
    const client = new SidecarClient(opts.sidecar);
    await client.start(opts.sidecar);

    const modelId = opts.model?.id ?? "unknown";
    let systemPrompt = opts.systemPrompt ?? "";

    // Inject skills from past sessions into system prompt
    if (client.isReady() && systemPrompt) {
      const skillsCtx = await client.buildSkillsContext(systemPrompt);
      if (skillsCtx) {
        systemPrompt = `${systemPrompt}\n\n${skillsCtx}`;
      }
    }

    // Create session record in memory DB
    const sessionId = client.isReady()
      ? await client.createSession({
          source: "pi",
          tenantId: opts.tenantId,
          model: modelId,
          systemPrompt,
        })
      : null;

    // Build OpenEye + user tools
    const oeTools = createOpenEyeTools(client, {
      tenantId: opts.tenantId,
      cloudSync: opts.cloudSync ?? false,
    });
    const allTools = [...oeTools, ...(opts.extraTools ?? [])];

    // Build the pi Agent
    const agentOpts: any = {
      initialState: {
        model: opts.model,
        systemPrompt,
        tools: allTools,
      },
    };

    if (opts.streamFn) {
      agentOpts.streamFn = opts.streamFn;
    }

    const agent = new Agent(agentOpts);

    // Mirror messages to memory DB on every turn
    if (sessionId && client.isReady()) {
      agent.subscribe(async (event: AgentEvent, _signal: any) => {
        opts.onEvent?.(event);
        if (event.type === "message_end") {
          const msg = (event as any).message;
          if (msg?.role === "user" || msg?.role === "assistant") {
            const content =
              typeof msg.content === "string"
                ? msg.content
                : Array.isArray(msg.content)
                  ? msg.content
                      .filter((b: any) => b.type === "text")
                      .map((b: any) => b.text)
                      .join("")
                  : "";
            await client.appendMessage(sessionId, msg.role, content);
          }
        }
      });
    }

    return new OpenEyeAgent(agent, client, sessionId, modelId, systemPrompt, opts.tenantId, opts.cloudSync ?? false);
  }

  // ── Public surface ─────────────────────────────────────────────────────────

  get agent(): Agent { return this._agent; }
  get client(): SidecarClient { return this._client; }
  get sessionId(): string | null { return this._sessionId; }

  /** Submit a user message and run the agent turn. */
  async prompt(userMessage: string): Promise<void> {
    if (this._sessionId && this._client.isReady()) {
      await this._client.appendMessage(this._sessionId, "user", userMessage);
    }
    await this._agent.prompt(userMessage);
  }

  /**
   * End the current session and capture it as a training trajectory.
   * @returns trajectory ID, or null if sidecar unavailable
   */
  async captureAndClose(opts: {
    completed?: boolean;
    visualSessionId?: string;
    tags?: string[];
    /** Also stop the sidecar subprocess */
    stopSidecar?: boolean;
  } = {}): Promise<string | null> {
    let trajectoryId: string | null = null;
    if (this._sessionId && this._client.isReady()) {
      await this._client.endSession(this._sessionId);
      trajectoryId = await this._client.captureTrajectory({
        sessionId: this._sessionId,
        completed: opts.completed ?? true,
        model: this._model,
        systemPrompt: this._systemPrompt,
        visualSessionId: opts.visualSessionId,
        tenantId: this._tenantId,
        tags: opts.tags,
        cloudSync: this._cloudSync,
      });
    }
    if (opts.stopSidecar) {
      this._client.stop();
    }
    return trajectoryId;
  }

  /**
   * Export all completed trajectories to JSONL.
   * Compatible with hermes batch_runner and tinker-atropos.
   */
  async exportTrajectories(outputPath: string): Promise<number> {
    return this._client.exportTrajectories({ outputPath });
  }

  /**
   * Export DPO preference pairs to JSONL.
   * Pairs high-reward sessions (chosen) against low-reward ones (rejected)
   * on the same procedure. TRL-compatible format.
   */
  async exportDPOPairs(
    outputPath: string,
    opts: { chosenThreshold?: number; rejectedThreshold?: number } = {}
  ): Promise<number> {
    return this._client.exportDPOPairs({
      outputPath,
      chosenThreshold: opts.chosenThreshold,
      rejectedThreshold: opts.rejectedThreshold,
    });
  }

  /**
   * Push completed trajectories to a HuggingFace dataset repository.
   */
  async pushToHub(
    repoId: string,
    opts: { token?: string; private?: boolean; tags?: string[]; dryRun?: boolean } = {}
  ): Promise<{ pushed: number; url: string; dryRun: boolean } | null> {
    const token = opts.token ?? process.env.HF_TOKEN;
    if (!token) {
      throw new Error(
        "HuggingFace token required. Pass token in opts or set HF_TOKEN env var.\n" +
        "Get a token at: https://huggingface.co/settings/tokens"
      );
    }
    const result = await this._client.pushToHub({
      repoId,
      hfToken: token,
      private: opts.private ?? false,
      tags: opts.tags,
      dryRun: opts.dryRun ?? false,
    });
    if (!result) return null;
    return { pushed: result.pushed, url: result.url, dryRun: result.dryRun };
  }

  /** Stop the sidecar subprocess. */
  stop(): void {
    this._client.stop();
  }
}
