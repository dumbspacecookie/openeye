/**
 * Integration tests for SidecarClient — uses a real HTTP server in-process
 * (not the Python sidecar) to exercise lifecycle, timeouts, auth, and
 * retry behavior end-to-end.
 *
 * These tests run with no Python dependency.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as http from "node:http";
import { AddressInfo } from "node:net";

import { SidecarClient } from "../src/sidecar-client.js";

interface MockSidecarOptions {
  healthOk?: boolean;
  delayMs?: number;
  expectedToken?: string;
  customHandlers?: Record<string, (req: http.IncomingMessage, res: http.ServerResponse, body: any) => void>;
}

async function startMockSidecar(opts: MockSidecarOptions = {}): Promise<{ server: http.Server; port: number; calls: Array<{ path: string; auth?: string }> }> {
  const calls: Array<{ path: string; auth?: string }> = [];
  const server = http.createServer((req, res) => {
    let body = "";
    req.on("data", (c) => (body += c));
    req.on("end", () => {
      calls.push({ path: req.url || "", auth: req.headers["authorization"] as string | undefined });

      const respond = (status: number, payload: any) => {
        setTimeout(() => {
          res.writeHead(status, { "Content-Type": "application/json" });
          res.end(JSON.stringify(payload));
        }, opts.delayMs ?? 0);
      };

      // Auth check
      if (opts.expectedToken) {
        const auth = req.headers["authorization"];
        if (req.url !== "/health" && auth !== `Bearer ${opts.expectedToken}`) {
          respond(401, { error: "unauthorized" });
          return;
        }
      }

      // Custom handler
      const handler = opts.customHandlers?.[req.url || ""];
      if (handler) {
        try {
          const parsed = body ? JSON.parse(body) : {};
          handler(req, res, parsed);
        } catch {
          handler(req, res, {});
        }
        return;
      }

      // Defaults
      if (req.url === "/health") {
        respond(200, { ok: opts.healthOk !== false, db: ":memory:" });
        return;
      }
      if (req.url === "/sessions/create") {
        respond(200, { session_id: "test-session-from-mock" });
        return;
      }
      respond(404, { error: "not_found" });
    });
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const port = (server.address() as AddressInfo).port;
  return { server, port, calls };
}

async function stopServer(server: http.Server) {
  await new Promise<void>((resolve) => server.close(() => resolve()));
}

describe("SidecarClient lifecycle (attaches to running sidecar)", () => {
  let mock: Awaited<ReturnType<typeof startMockSidecar>>;

  beforeEach(async () => {
    mock = await startMockSidecar();
  });
  afterEach(async () => {
    await stopServer(mock.server);
  });

  it("attaches to an already-running sidecar without spawning", async () => {
    const client = new SidecarClient({ port: mock.port });
    await client.start();
    expect(client.isReady()).toBe(true);
    // The first call should be the health probe
    expect(mock.calls[0].path).toBe("/health");
  });

  it("returns null from API calls when not started", async () => {
    const client = new SidecarClient({ port: mock.port });
    // Don't call start()
    const sid = await client.createSession({ source: "test" });
    expect(sid).toBeNull();
  });

  it("createSession round-trips through the mock", async () => {
    const client = new SidecarClient({ port: mock.port });
    await client.start();
    const sid = await client.createSession({ source: "test" });
    expect(sid).toBe("test-session-from-mock");
  });
});

describe("SidecarClient timeout behavior", () => {
  it("times out when sidecar hangs longer than requestTimeout", async () => {
    const mock = await startMockSidecar({ delayMs: 500 });
    try {
      const client = new SidecarClient({
        port: mock.port,
        requestTimeout: 100,
      });
      await client.start(); // health probe uses its own short timeout
      // This call should time out after 100ms
      const result = await client.createSession({ source: "test" });
      expect(result).toBeNull(); // call() returns null on error
    } finally {
      await stopServer(mock.server);
    }
  });

  it("health probe fails fast when nothing is listening", async () => {
    const client = new SidecarClient({
      port: 1,                  // reserved port — connection will refuse
      bootTimeout: 500,         // small to keep the test snappy
      sidecarDir: "/nonexistent", // skip spawn path by making it throw
    });
    // start() will try to attach, fail, try to spawn, fail
    await expect(client.start()).rejects.toThrow();
  });
});

describe("SidecarClient auth", () => {
  it("sends Authorization header when sidecarToken is set", async () => {
    const mock = await startMockSidecar({ expectedToken: "test-shared-secret" });
    try {
      const client = new SidecarClient({
        port: mock.port,
        sidecarToken: "test-shared-secret",
      });
      await client.start();
      const sid = await client.createSession({ source: "test" });
      expect(sid).toBe("test-session-from-mock");
      // Find the create-session call and verify auth header
      const createCall = mock.calls.find((c) => c.path === "/sessions/create");
      expect(createCall?.auth).toBe("Bearer test-shared-secret");
    } finally {
      await stopServer(mock.server);
    }
  });

  it("returns null when sidecar rejects auth", async () => {
    const mock = await startMockSidecar({ expectedToken: "right-token" });
    try {
      const client = new SidecarClient({
        port: mock.port,
        sidecarToken: "wrong-token",
      });
      await client.start();
      // Health doesn't require token, so attach succeeds; create-session 401s
      const sid = await client.createSession({ source: "test" });
      // The mock returns 401 which our client parses as { error: "unauthorized" }
      // — the call wrapper still returns the body; we get a truthy object back
      // without a session_id. createSession explicitly accesses session_id so
      // it returns null.
      expect(sid).toBeNull();
    } finally {
      await stopServer(mock.server);
    }
  });

  it("does not send Authorization header when no token set", async () => {
    const mock = await startMockSidecar();
    try {
      const client = new SidecarClient({ port: mock.port });
      await client.start();
      await client.createSession({ source: "test" });
      const createCall = mock.calls.find((c) => c.path === "/sessions/create");
      expect(createCall?.auth).toBeUndefined();
    } finally {
      await stopServer(mock.server);
    }
  });
});

describe("SidecarClient frame logging", () => {
  it("logFrame posts the right payload shape", async () => {
    let captured: any = null;
    const mock = await startMockSidecar({
      customHandlers: {
        "/frames/log": (_req, res, body) => {
          captured = body;
          res.writeHead(200, { "Content-Type": "application/json" });
          res.end(JSON.stringify({ frame_id: 42 }));
        },
      },
    });
    try {
      const client = new SidecarClient({ port: mock.port });
      await client.start();
      const fid = await client.logFrame({
        visualSessionId: "vs-1",
        sequenceNum: 7,
        sceneDescription: "operator placing bolt",
        objectsDetected: ["bolt", "hand"],
        confidence: 0.91,
      });
      expect(fid).toBe(42);
      expect(captured.visual_session_id).toBe("vs-1");
      expect(captured.sequence_num).toBe(7);
      expect(captured.scene_description).toBe("operator placing bolt");
      expect(captured.objects_detected).toEqual(["bolt", "hand"]);
      expect(captured.confidence).toBe(0.91);
    } finally {
      await stopServer(mock.server);
    }
  });
});
