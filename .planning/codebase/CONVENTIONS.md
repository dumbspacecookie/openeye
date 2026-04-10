# Coding Conventions

**Analysis Date:** 2026-04-09

## Naming Patterns

**Files:**
- kebab-case for file names: `sidecar-client.ts`, `preflight.ts`, `memory.ts`
- No underscores in file names

**Functions:**
- camelCase for function names: `checkPreflight()`, `createSession()`, `appendMessage()`, `searchMemory()`
- Functions exported from modules use camelCase: `createOpenEyeTools()`, `ollamaModel()`, `customModel()`
- Private methods use leading underscore with camelCase: `_ready`, `_client`, `_agent`, `_userId`

**Variables:**
- camelCase for variable names throughout: `sessionId`, `tenantId`, `cloudSync`, `userMessage`, `trajectoryId`, `visualSessionId`
- Constants use camelCase: `DEFAULT_PORT`, `ANTHROPIC_OPUS`, `OPENAI_GPT41`, `GOOGLE_GEMINI_25_PRO`
- Private properties use leading underscore: `_sessionId`, `_model`, `_systemPrompt`, `_client`, `_userId`, `_tenantId`

**Types & Interfaces:**
- PascalCase for interface names: `OpenEyeAgentOptions`, `MemoryOptions`, `MemoryRecord`, `MemorySearchResult`, `SidecarClientOptions`, `PreflightResult`
- Short type names in tool definitions: `Type.String()`, `Type.Object()`, `Type.Array()`, `Type.Optional()`
- Type union literals for specific values: `Type.Union([Type.Literal("pass"), Type.Literal("fail"), Type.Literal("uncertain")])`

## Code Style

**Formatting:**
- TypeScript with strict mode enabled (`"strict": true` in tsconfig.json)
- 2-space indentation (observed throughout)
- No explicit semicolon enforcement detected (implicit semicolons used)
- Line length appears to be ~100-120 characters

**Linting:**
- TypeScript compiler in strict mode provides type safety
- No ESLint or Prettier config detected
- Config files included: `tsconfig.json` with `"strict": true`, `"declaration": true`, `"sourceMap": true`

**Module System:**
- ES modules (ESM) exclusively: `"type": "module"` in package.json
- File imports use `.js` extension: `import { SidecarClient } from "./sidecar-client.js"`
- ESM-safe `__dirname` implementation via `fileURLToPath(import.meta.url)` in `src/sidecar-client.ts:14-15`

## Import Organization

**Order:**
1. Node built-in modules first: `import { ChildProcess, spawn } from "node:child_process"`
2. Third-party imports: `import { Agent } from "@mariozechner/pi-agent-core"`
3. Type imports: `import type { AgentEvent, AgentTool } from "@mariozechner/pi-agent-core"`
4. Local relative imports: `import { SidecarClient } from "./sidecar-client.js"`

**Path Aliases:**
- No path aliases detected in tsconfig.json
- Relative imports use `.js` extension for transpiled output

**Export Patterns:**
- Barrel exports re-exporting from modules: `export { SidecarClient } from "./sidecar-client.js"` in `src/index.ts`
- Named exports for utilities: `export function createOpenEyeTools()`, `export function setupProviders()`
- Export objects with multiple functions: models exported as `ANTHROPIC_OPUS`, `ANTHROPIC_SONNET`, etc.

## Error Handling

**Patterns:**
- Safe call wrapper in `SidecarClient`: `private async call<T>()` returns `null` on error instead of throwing
- Non-throwing design for sidecar failures: "a sidecar crash never kills the pi agent" (documented in `src/sidecar-client.ts:4`)
- Null coalescing for optional returns: `res?.session_id ?? null`
- Try-catch with error logging: `try { ... } catch (err) { this.log(...); return null; }`
- Explicit null checks before operations: `if (!this._ready) return null`
- Direct errors thrown for critical failures: `throw new Error()` when sidecar file not found or missing HF token

**Common Patterns:**
- Safe subprocess spawning with lifecycle hooks in `src/sidecar-client.ts:146-150`
- Promise-based retry loops with timeout: `waitReady()` function in `src/sidecar-client.ts:68-80`
- Graceful degradation when sidecar unavailable: Conditional session creation and messaging

## Logging

**Framework:** console via stderr for sidecar output

**Patterns:**
- Private `log()` method in `SidecarClient`: writes to `process.stderr` with `[openeye]` prefix
- Subprocess output routed to stderr with `[openeye-sidecar]` prefix: `src/sidecar-client.ts:141-145`
- Status messages: "Sidecar ready", "Attached to running sidecar", "Sidecar exited"
- Used for initialization diagnostics and failure reporting

## Comments

**When to Comment:**
- Module-level documentation: Each file has a JSDoc comment block explaining its purpose
- Complex algorithms: HTTP retry loop documented
- Non-obvious design decisions: "ESM-safe __dirname" explained in `src/sidecar-client.ts:13`

**JSDoc/TSDoc:**
- File-level JSDoc blocks at module start: `/** ... */` comments describe file purpose
- Function JSDoc blocks for public methods: `@param`, `@returns` documented
- Type descriptions in function signatures: `/** System prompt */` inline descriptions
- Option object fields documented: `{ tenantId?: string; /** Description */ }`
- Tool parameter descriptions: `Type.String({ description: "..." })` throughout `src/tools.ts`

## Function Design

**Size:**
- Small focused functions: `isReady()` is single-line getter
- Medium functions (20-50 lines): `OpenEyeAgent.create()` handles initialization logic
- Longer functions acceptable for single responsibility: `SidecarClient.start()` handles entire startup sequence

**Parameters:**
- Options objects for functions with multiple parameters: `async create(opts: OpenEyeAgentOptions)`
- Spread optional values in objects: `{ port?: number; sidecarDir?: string; python?: string; bootTimeout?: number }`
- Type-safe parameters: All parameters typed with interfaces/types

**Return Values:**
- Async functions return Promises: `async create(): Promise<OpenEyeAgent>`
- Nullable returns for fallible operations: `Promise<string | null>` for session ID creation
- Union types for specific results: `"pass" | "fail" | "uncertain"` for step verification
- Empty object returns for void operations: `{ content: [...], details: {} }`

## Module Design

**Exports:**
- One main class per file: `OpenEyeAgent`, `SidecarClient`, `Memory`
- Supporting functions/interfaces exported: `createOpenEyeTools()`, `checkPreflight()`, `setupProviders()`
- Type exports for interface exposure: `export type { SidecarClientOptions, VerifyResult }`

**Barrel Files:**
- `src/index.ts` re-exports all public API from other modules
- Re-exports organized by category: Anthropic models, OpenAI models, Google models, etc.
- Usage: `import { OpenEyeAgent, setupProviders, ANTHROPIC_SONNET } from "@openeye/pi-openeye"`

**Class Design:**
- Private constructor with static factory method: `private constructor()` with `static async create()`
- Private fields for encapsulation: `private _agent`, `private _client`, `private _sessionId`
- Public getters for read-only access: `get agent(): Agent`, `get client(): SidecarClient`
- Async initialization: Factory pattern separates construction from async startup

---

*Convention analysis: 2026-04-09*
