import { describe, it, expect, vi, beforeEach } from "vitest";

// Mock the sidecar client to avoid spawning a real Python process
vi.mock("../src/sidecar-client.js", () => {
  return {
    SidecarClient: vi.fn().mockImplementation(() => ({
      start: vi.fn().mockResolvedValue(undefined),
      stop: vi.fn(),
      isReady: vi.fn().mockReturnValue(true),
      createSession: vi.fn().mockResolvedValue("test-session-id"),
      endSession: vi.fn().mockResolvedValue(undefined),
      appendMessage: vi.fn().mockResolvedValue(1),
      buildSkillsContext: vi.fn().mockResolvedValue(""),
      captureTrajectory: vi.fn().mockResolvedValue("test-trajectory-id"),
      exportTrajectories: vi.fn().mockResolvedValue(5),
      exportDPOPairs: vi.fn().mockResolvedValue(3),
      searchMemory: vi.fn().mockResolvedValue([]),
      searchFrames: vi.fn().mockResolvedValue([]),
      recallSkills: vi.fn().mockResolvedValue([]),
      writeSkill: vi.fn().mockResolvedValue({ id: "s1" }),
      createVisualSession: vi.fn().mockResolvedValue("vs-id"),
      endVisualSession: vi.fn().mockResolvedValue(undefined),
      logFrame: vi.fn().mockResolvedValue(1),
      logStepVerification: vi.fn().mockResolvedValue(1),
      pushToHub: vi.fn().mockResolvedValue({ pushed: 10, repoId: "user/repo", url: "https://huggingface.co/datasets/user/repo", dryRun: false }),
    })),
  };
});

describe("SidecarClient mock", () => {
  it("createSession returns a session ID", async () => {
    const { SidecarClient } = await import("../src/sidecar-client.js");
    const client = new SidecarClient();
    await client.start();
    const sid = await client.createSession({ source: "test" });
    expect(sid).toBe("test-session-id");
  });

  it("exportTrajectories returns count", async () => {
    const { SidecarClient } = await import("../src/sidecar-client.js");
    const client = new SidecarClient();
    await client.start();
    const count = await client.exportTrajectories({ outputPath: "test.jsonl" });
    expect(count).toBe(5);
  });

  it("exportDPOPairs returns count", async () => {
    const { SidecarClient } = await import("../src/sidecar-client.js");
    const client = new SidecarClient();
    await client.start();
    const count = await client.exportDPOPairs({ outputPath: "dpo.jsonl" });
    expect(count).toBe(3);
  });

  it("pushToHub returns result with correct fields", async () => {
    const { SidecarClient } = await import("../src/sidecar-client.js");
    const client = new SidecarClient();
    await client.start();
    const result = await client.pushToHub({
      repoId: "user/repo", hfToken: "hf_test",
      tags: ["test"], dryRun: false,
    });
    expect(result).not.toBeNull();
    expect(result!.pushed).toBe(10);
    expect(result!.url).toContain("huggingface.co");
  });
});

describe("createOpenEyeTools", () => {
  it("returns 8 tools", async () => {
    const { SidecarClient } = await import("../src/sidecar-client.js");
    const { createOpenEyeTools } = await import("../src/tools.js");
    const client = new SidecarClient();
    const tools = createOpenEyeTools(client);
    expect(tools).toHaveLength(8);
    const names = tools.map((t: any) => t.name);
    expect(names).toContain("search_memory");
    expect(names).toContain("verify_step");
    expect(names).toContain("log_frame");
  });
});
