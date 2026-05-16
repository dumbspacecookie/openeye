/**
 * mem0 / Zep compatibility layer for OpenEye.
 * Drop-in: import { Memory } from "@dumbspacecookie/openeye/memory"
 */
import { SidecarClient, type SidecarClientOptions } from "./sidecar-client.js";

export interface MemoryOptions {
  userId?: string;
  tenantId?: string;
  sidecar?: SidecarClientOptions;
}

export interface MemoryRecord {
  id: string;
  memory: string;
  metadata: Record<string, unknown>;
  createdAt: number;
}

export interface MemorySearchResult extends MemoryRecord {
  score: number;
}

export class Memory {
  private _client: SidecarClient;
  private _userId?: string;
  private _tenantId?: string;

  constructor(opts: MemoryOptions = {}) {
    this._client = new SidecarClient(opts.sidecar);
    this._userId = opts.userId;
    this._tenantId = opts.tenantId;
  }

  async start(): Promise<void> {
    if (!this._client.isReady()) await this._client.start();
  }

  stop(): void { this._client.stop(); }

  async add(content: string, opts: { metadata?: Record<string, unknown> } = {}): Promise<string> {
    const sessionId = await this._client.createSession({
      userId: this._userId,
      tenantId: this._tenantId,
      title: opts.metadata ? JSON.stringify(opts.metadata) : undefined,
      source: "memory",
    });
    if (!sessionId) throw new Error("Failed to create memory session");
    const messageId = await this._client.appendMessage(sessionId, "user", content);
    await this._client.endSession(sessionId, "memory");
    return String(messageId);
  }

  async search(query: string, opts: { limit?: number } = {}): Promise<MemorySearchResult[]> {
    const results = await this._client.searchMemory({
      query,
      tenantId: this._tenantId,
      limit: opts.limit ?? 20,
    });
    return (results as any[]).map((r, i) => ({
      id: String(r.id ?? r.session_id ?? i),
      memory: r.snippet ?? r.content ?? "",
      metadata: { sessionId: r.session_id, model: r.model, source: r.source },
      createdAt: r.timestamp ?? 0,
      score: 1 / (1 + i),
    }));
  }

  async getAll(opts: { limit?: number } = {}): Promise<MemoryRecord[]> {
    // Use searchMemory with a broad query, or list sessions
    // For now, search with a wildcard-like approach
    const results = await this._client.searchMemory({
      query: "*",
      tenantId: this._tenantId,
      limit: opts.limit ?? 100,
    });
    return (results as any[]).map((r, i) => ({
      id: String(r.session_id ?? r.id ?? i),
      memory: r.snippet ?? r.content ?? "",
      metadata: { model: r.model, source: r.source },
      createdAt: r.timestamp ?? 0,
    }));
  }

  async delete(id: string): Promise<void> {
    await this._client.endSession(id, "deleted");
  }

  async deleteAll(): Promise<void> {
    const all = await this.getAll();
    await Promise.all(all.map((r) => this.delete(r.id)));
  }

  async update(id: string, content: string): Promise<void> {
    await this._client.appendMessage(id, "user", content);
  }
}
