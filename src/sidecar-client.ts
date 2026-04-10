/**
 * OpenEye Sidecar Client
 * Spawns the Python sidecar subprocess and talks to it via localhost HTTP.
 * All calls are non-throwing — a sidecar crash never kills the pi agent.
 */

import { ChildProcess, spawn } from "node:child_process";
import * as fs from "node:fs";
import * as http from "node:http";
import * as path from "node:path";
import { fileURLToPath } from "node:url";

// ESM-safe __dirname
const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

export interface SidecarClientOptions {
  port?: number;
  /** Override path to the sidecar/ directory. Defaults to ../sidecar relative to this file. */
  sidecarDir?: string;
  /** Python executable. Defaults to OPENEYE_PYTHON env var or "python3". */
  python?: string;
  /** Milliseconds to wait for sidecar to boot. Default 8000. */
  bootTimeout?: number;
}

export type VerifyResult = "pass" | "fail" | "uncertain";

const DEFAULT_PORT = 7770;

// ── HTTP helpers ──────────────────────────────────────────────────────────────

async function httpRequest(
  port: number,
  method: "GET" | "POST",
  endpoint: string,
  body?: unknown
): Promise<unknown> {
  return new Promise((resolve, reject) => {
    const payload = body !== undefined ? JSON.stringify(body) : undefined;
    const opts: http.RequestOptions = {
      hostname: "127.0.0.1",
      port,
      path: endpoint,
      method,
      headers: {
        "Content-Type": "application/json",
        ...(payload ? { "Content-Length": String(Buffer.byteLength(payload)) } : {}),
      },
    };
    const req = http.request(opts, (res) => {
      const chunks: Buffer[] = [];
      res.on("data", (c: Buffer) => chunks.push(c));
      res.on("end", () => {
        try {
          resolve(JSON.parse(Buffer.concat(chunks).toString("utf-8")));
        } catch {
          resolve({});
        }
      });
    });
    req.on("error", reject);
    if (payload) req.write(payload);
    req.end();
  });
}

async function waitReady(port: number, timeout: number): Promise<boolean> {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    try {
      const res = (await httpRequest(port, "GET", "/health")) as { ok?: boolean };
      if (res?.ok) return true;
    } catch {
      // not yet up
    }
    await new Promise((r) => setTimeout(r, 200));
  }
  return false;
}

// ── Client class ──────────────────────────────────────────────────────────────

export class SidecarClient {
  private port: number;
  private proc?: ChildProcess;
  private _ready = false;

  constructor(private opts: SidecarClientOptions = {}) {
    this.port = opts.port ?? Number(process.env.OPENEYE_PORT ?? DEFAULT_PORT);
  }

  // ── Lifecycle ──────────────────────────────────────────────────────────────

  async start(opts: SidecarClientOptions = {}): Promise<void> {
    // Re-use if already running
    try {
      const res = (await httpRequest(this.port, "GET", "/health")) as { ok?: boolean };
      if (res?.ok) {
        this._ready = true;
        this.log(`Attached to running sidecar on port ${this.port}`);
        return;
      }
    } catch {
      // not running — spawn it
    }

    const sidecarDir =
      opts.sidecarDir ??
      this.opts.sidecarDir ??
      path.resolve(__dirname, "..", "sidecar");

    const serverPy = path.join(sidecarDir, "server.py");
    if (!fs.existsSync(serverPy)) {
      throw new Error(
        `OpenEye sidecar not found at: ${serverPy}\n` +
        `Expected sidecar/ directory at: ${sidecarDir}`
      );
    }

    const python =
      opts.python ??
      this.opts.python ??
      process.env.OPENEYE_PYTHON ??
      "python3";

    const uvArgs = ["-m", "uvicorn", "server:app",
      "--host", "127.0.0.1",
      "--port", String(this.port),
      "--workers", "1",
      "--log-level", "warning",
    ];
    this.log(`Spawning: ${python} ${uvArgs.join(" ")} (cwd: ${sidecarDir})`);
    this.proc = spawn(python, uvArgs, {
      env: { ...process.env, OPENEYE_PORT: String(this.port) },
      cwd: sidecarDir,
      stdio: ["ignore", "pipe", "pipe"],
    });

    this.proc.stdout?.on("data", (d: Buffer) =>
      process.stderr.write(`[openeye-sidecar] ${d}`)
    );
    this.proc.stderr?.on("data", (d: Buffer) =>
      process.stderr.write(`[openeye-sidecar] ${d}`)
    );
    this.proc.on("exit", (code) => {
      this._ready = false;
      if (code !== 0 && code !== null)
        this.log(`Sidecar exited with code ${code}`);
    });

    const timeout = opts.bootTimeout ?? this.opts.bootTimeout ?? 8000;
    this._ready = await waitReady(this.port, timeout);

    if (!this._ready) {
      this.log(`Sidecar did not become ready within ${timeout}ms`);
    } else {
      this.log(`Sidecar ready on port ${this.port}`);
    }
  }

  stop(): void {
    this.proc?.kill();
    this.proc = undefined;
    this._ready = false;
  }

  isReady(): boolean { return this._ready; }

  private log(msg: string): void {
    process.stderr.write(`[openeye] ${msg}\n`);
  }

  // ── Safe call wrapper ──────────────────────────────────────────────────────

  private async call<T = unknown>(
    method: "GET" | "POST",
    endpoint: string,
    body?: unknown
  ): Promise<T | null> {
    if (!this._ready) return null;
    try {
      return (await httpRequest(this.port, method, endpoint, body)) as T;
    } catch (err) {
      this.log(`Request failed ${method} ${endpoint}: ${err}`);
      return null;
    }
  }

  // ── Sessions ───────────────────────────────────────────────────────────────

  async createSession(opts: {
    source?: string; userId?: string; tenantId?: string;
    model?: string; systemPrompt?: string; title?: string;
  }): Promise<string | null> {
    const res = await this.call<{ session_id: string }>("POST", "/sessions/create", {
      source: opts.source ?? "pi", user_id: opts.userId, tenant_id: opts.tenantId,
      model: opts.model, system_prompt: opts.systemPrompt, title: opts.title,
    });
    return res?.session_id ?? null;
  }

  async endSession(sessionId: string, reason = "normal"): Promise<void> {
    await this.call("POST", `/sessions/${sessionId}/end`, { reason });
  }

  async appendMessage(
    sessionId: string, role: string, content?: string, toolCalls?: unknown,
    toolName?: string, tokenCount?: number, finishReason?: string
  ): Promise<number | null> {
    const res = await this.call<{ message_id: number }>(
      "POST", `/sessions/${sessionId}/messages`,
      { role, content, tool_calls: toolCalls, tool_name: toolName,
        token_count: tokenCount, finish_reason: finishReason });
    return res?.message_id ?? null;
  }

  // ── Search ─────────────────────────────────────────────────────────────────

  async searchMemory(opts: { query: string; tenantId?: string; limit?: number }): Promise<unknown[]> {
    const res = await this.call<{ results: unknown[] }>("POST", "/search/messages", {
      query: opts.query, tenant_id: opts.tenantId, limit: opts.limit ?? 20,
    });
    return res?.results ?? [];
  }

  async searchFrames(opts: {
    query: string; tenantId?: string; procedureId?: string; limit?: number;
  }): Promise<unknown[]> {
    const res = await this.call<{ results: unknown[] }>("POST", "/search/frames", {
      query: opts.query, tenant_id: opts.tenantId,
      procedure_id: opts.procedureId, limit: opts.limit ?? 20,
    });
    return res?.results ?? [];
  }

  // ── Visual sessions ────────────────────────────────────────────────────────

  async createVisualSession(opts: {
    deviceType: string; deviceId?: string; procedureId?: string;
    procedureName?: string; userId?: string; tenantId?: string;
    sessionId?: string; metadata?: Record<string, unknown>;
  }): Promise<string | null> {
    const res = await this.call<{ visual_session_id: string }>("POST", "/visual-sessions/create", {
      device_type: opts.deviceType, device_id: opts.deviceId,
      procedure_id: opts.procedureId, procedure_name: opts.procedureName,
      user_id: opts.userId, tenant_id: opts.tenantId,
      session_id: opts.sessionId, metadata: opts.metadata,
    });
    return res?.visual_session_id ?? null;
  }

  async endVisualSession(vsId: string, outcome = "completed"): Promise<void> {
    await this.call("POST", `/visual-sessions/${vsId}/end`, { outcome });
  }

  // ── Frames ─────────────────────────────────────────────────────────────────

  async logFrame(opts: {
    visualSessionId: string; sequenceNum: number; sceneDescription: string;
    tenantId?: string; width?: number; height?: number; objectsDetected?: string[];
    stepContext?: string; confidence?: number; cloudSync?: boolean;
  }): Promise<number | null> {
    const res = await this.call<{ frame_id: number }>("POST", "/frames/log", {
      visual_session_id: opts.visualSessionId, sequence_num: opts.sequenceNum,
      scene_description: opts.sceneDescription, tenant_id: opts.tenantId,
      width: opts.width, height: opts.height, objects_detected: opts.objectsDetected,
      step_context: opts.stepContext, confidence: opts.confidence,
      cloud_sync: opts.cloudSync ?? false,
    });
    return res?.frame_id ?? null;
  }

  // ── Step verifications ─────────────────────────────────────────────────────

  async logStepVerification(opts: {
    visualSessionId: string; stepId: string; result: VerifyResult;
    frameId?: number; stepName?: string; confidence?: number; reasoning?: string;
    modelUsed?: string; latencyMs?: number; tenantId?: string; cloudSync?: boolean;
  }): Promise<number | null> {
    const res = await this.call<{ verification_id: number }>("POST", "/steps/log", {
      visual_session_id: opts.visualSessionId, step_id: opts.stepId, result: opts.result,
      frame_id: opts.frameId, step_name: opts.stepName, confidence: opts.confidence,
      reasoning: opts.reasoning, model_used: opts.modelUsed, latency_ms: opts.latencyMs,
      tenant_id: opts.tenantId, cloud_sync: opts.cloudSync ?? false,
    });
    return res?.verification_id ?? null;
  }

  // ── Skills ─────────────────────────────────────────────────────────────────

  async writeSkill(opts: {
    name: string; content: string; description?: string; domain?: string;
  }): Promise<unknown> {
    return this.call("POST", "/skills/write", {
      name: opts.name, content: opts.content, description: opts.description,
      domain: opts.domain ?? "general", source: "generated",
    });
  }

  async recallSkills(opts: { task: string; domain?: string; topK?: number }): Promise<unknown[]> {
    const res = await this.call<{ skills: unknown[] }>("POST", "/skills/recall", {
      task: opts.task, domain: opts.domain, top_k: opts.topK ?? 5,
    });
    return res?.skills ?? [];
  }

  async buildSkillsContext(task: string, domain?: string): Promise<string> {
    const res = await this.call<{ context: string }>("POST", "/skills/context", { task, domain });
    return res?.context ?? "";
  }

  // ── Trajectories ──────────────────────────────────��───────────────────────

  async captureTrajectory(opts: {
    sessionId: string; completed: boolean; model: string; systemPrompt?: string;
    visualSessionId?: string; tenantId?: string; tags?: string[]; cloudSync?: boolean;
  }): Promise<string | null> {
    const res = await this.call<{ trajectory_id: string }>("POST", "/trajectories/capture", {
      session_id: opts.sessionId, completed: opts.completed, model: opts.model,
      system_prompt: opts.systemPrompt, visual_session_id: opts.visualSessionId,
      tenant_id: opts.tenantId, tags: opts.tags, cloud_sync: opts.cloudSync ?? false,
    });
    return res?.trajectory_id ?? null;
  }

  async exportTrajectories(opts: { outputPath: string; completedOnly?: boolean }): Promise<number> {
    const res = await this.call<{ exported: number }>("POST", "/trajectories/export", {
      output_path: opts.outputPath, completed_only: opts.completedOnly ?? true,
    });
    return res?.exported ?? 0;
  }

  async exportDPOPairs(opts: {
    outputPath: string; chosenThreshold?: number;
    rejectedThreshold?: number; completedOnly?: boolean;
  }): Promise<number> {
    const res = await this.call<{ exported: number }>("POST", "/trajectories/export-dpo", {
      output_path: opts.outputPath, chosen_threshold: opts.chosenThreshold ?? 0.8,
      rejected_threshold: opts.rejectedThreshold ?? 0.4,
      completed_only: opts.completedOnly ?? true,
    });
    return res?.exported ?? 0;
  }

  async pushToHub(opts: {
    repoId: string; hfToken: string; private?: boolean;
    tags?: string[]; dryRun?: boolean; completedOnly?: boolean;
  }): Promise<{ pushed: number; repoId: string; url: string; dryRun: boolean } | null> {
    const res = await this.call<{
      pushed: number; repo_id: string; url: string; dry_run: boolean;
    }>("POST", "/trajectories/push-to-hub", {
      repo_id: opts.repoId, hf_token: opts.hfToken,
      private: opts.private ?? false, tags: opts.tags ?? [],
      dry_run: opts.dryRun ?? false, completed_only: opts.completedOnly ?? true,
    });
    if (!res) return null;
    return { pushed: res.pushed, repoId: res.repo_id, url: res.url, dryRun: res.dry_run };
  }
}
