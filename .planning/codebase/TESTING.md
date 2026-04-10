# Testing Patterns

**Analysis Date:** 2026-04-09

## Test Framework

**Runner:**
- Vitest 3.2.4
- Config: `vitest.config.ts`
- Node environment: `environment: "node"`

**Assertion Library:**
- Vitest built-in assertions (`expect`)
- No additional assertion libraries detected

**Run Commands:**
```bash
npm test              # Run all tests (--run flag)
npm run typecheck     # TypeScript type checking
npm run build         # Compile TypeScript to dist/
```

## Test File Organization

**Location:**
- Co-located in dedicated `tests/` directory
- Not co-located with source files

**Naming:**
- `.test.ts` suffix: `preflight.test.ts`, `runner.test.ts`
- Pattern: `[module].test.ts`

**Structure:**
```
tests/
├── preflight.test.ts
└── runner.test.ts
```

## Test Structure

**Suite Organization:**
```typescript
describe("preflight", () => {
  it("returns ok when python3 and uvicorn are available", async () => {
    // test body
  });

  it("returns errors for nonexistent python", async () => {
    // test body
  });
});
```

**Patterns:**
- Top-level `describe()` block per feature: `describe("preflight", () => { ... })`
- Individual test cases with `it()`: `it("returns ok when...", async () => { ... })`
- Async test support: `async () => { ... }` for promise-based testing
- No explicit setup/teardown observed in current tests
- Direct test body without additional wrappers

## Mocking

**Framework:** Vitest native mocking via `vi`

**Patterns:**
```typescript
vi.mock("../src/sidecar-client.js", () => {
  return {
    SidecarClient: vi.fn().mockImplementation(() => ({
      start: vi.fn().mockResolvedValue(undefined),
      isReady: vi.fn().mockReturnValue(true),
      createSession: vi.fn().mockResolvedValue("test-session-id"),
      appendMessage: vi.fn().mockResolvedValue(1),
      // ... more mocked methods
    })),
  };
});
```

**What to Mock:**
- External dependencies: `SidecarClient` mocked to avoid spawning Python subprocess
- Async operations: Mocked to return resolved promises immediately
- Subprocess-dependent code: All sidecar interactions mocked in test runner tests

**What NOT to Mock:**
- Core logic functions like `checkPreflight()` - real checks against Python environment
- Type definitions and interfaces
- Local utility functions

**Mock Implementation Pattern:**
- Module-level `vi.mock()` at top of test file
- Implementation returned as function factory
- Each method mocked individually with `vi.fn().mockResolvedValue()` or `vi.fn().mockReturnValue()`
- Consistent naming between real and mocked methods

## Fixtures and Factories

**Test Data:**
- Inline mock return values in `vi.fn().mockResolvedValue(data)`
- Strings for IDs: `"test-session-id"`, `"test-trajectory-id"`, `"vs-id"`
- Numbers for counts: `5` (exported trajectories), `3` (DPO pairs), `1` (message ID)
- Empty arrays for lists: `vi.fn().mockResolvedValue([])`

**Location:**
- No separate fixture files
- Mocks defined at top of test files
- Hard-coded test data in `vi.fn()` implementations

## Coverage

**Requirements:** None enforced (not specified in configuration)

**View Coverage:**
- Not configured in current setup
- Can be enabled via: `vitest --coverage` (when coverage reporter installed)

## Test Types

**Unit Tests:**
- Scope: Individual functions and classes in isolation
- Approach: Mock all dependencies, test behavior
- Example: Testing `checkPreflight()` with fake Python executable

**Integration Tests:**
- Scope: Multiple components working together
- Approach: Mock only external services (sidecar)
- Example: Testing `SidecarClient` methods with mocked HTTP responses
- Example: Testing `createOpenEyeTools()` returns correct tool definitions

**E2E Tests:**
- Framework: Not used
- Rationale: Python sidecar subprocess integration is complex; mocked in tests

## Common Patterns

**Async Testing:**
```typescript
it("returns ok when python3 and uvicorn are available", async () => {
  const result = await checkPreflight();
  expect(result).toHaveProperty("ok");
});
```

**Environment-Dependent Tests:**
```typescript
it("returns ok when python3 and uvicorn are available", async () => {
  const result = await checkPreflight();
  // This test depends on local environment — python3 may or may not be available
  expect(result).toHaveProperty("ok");
});
```
- Tests check for property existence rather than specific values when environment-dependent

**Error Testing:**
```typescript
it("returns errors for nonexistent python", async () => {
  const result = await checkPreflight("python_nonexistent_xyz");
  expect(result.ok).toBe(false);
  expect(result.errors.length).toBeGreaterThan(0);
  expect(result.python).toBeNull();
});
```
- Pass fake/nonexistent arguments to trigger error conditions
- Assert on error array length and null values

**Mocked Constructor Testing:**
```typescript
it("createSession returns a session ID", async () => {
  const { SidecarClient } = await import("../src/sidecar-client.js");
  const client = new SidecarClient();
  await client.start();
  const sid = await client.createSession({ source: "test" });
  expect(sid).toBe("test-session-id");
});
```
- Dynamic import after mock setup
- New instance per test to avoid state leakage
- Test mock return values, not real behavior

**Tool Export Testing:**
```typescript
it("returns 8 tools", async () => {
  const { createOpenEyeTools } = await import("../src/tools.js");
  const client = new SidecarClient();
  const tools = createOpenEyeTools(client);
  expect(tools).toHaveLength(8);
  const names = tools.map((t: any) => t.name);
  expect(names).toContain("search_memory");
  expect(names).toContain("verify_step");
});
```
- Dynamic imports for clean module reloading
- Test array length and specific element existence
- Extract and check properties from complex objects

## Test Execution

**Test Discovery:**
- Pattern in config: `include: ["tests/**/*.test.ts"]`
- All `.test.ts` files in `tests/` directory run automatically

**Isolation:**
- Module-level mocks reset between test suites
- No shared state between tests via dynamic imports
- Fresh mock implementations per test file

---

*Testing analysis: 2026-04-09*
