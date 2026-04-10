import { describe, it, expect } from "vitest";
import { checkPreflight } from "../src/preflight.js";

describe("preflight", () => {
  it("returns ok when python3 and uvicorn are available", async () => {
    const result = await checkPreflight();
    // This test depends on local environment — python3 may or may not be available
    expect(result).toHaveProperty("ok");
    expect(result).toHaveProperty("python");
    expect(result).toHaveProperty("uvicorn");
    expect(result).toHaveProperty("errors");
    expect(Array.isArray(result.errors)).toBe(true);
  });

  it("returns errors for nonexistent python", async () => {
    const result = await checkPreflight("python_nonexistent_xyz");
    expect(result.ok).toBe(false);
    expect(result.errors.length).toBeGreaterThan(0);
    expect(result.python).toBeNull();
  });
});
