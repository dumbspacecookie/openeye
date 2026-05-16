/**
 * Reference vision adapter — Ollama moondream (local).
 *
 * Turns a JPEG/PNG buffer into a plain-text scene description without any
 * network egress. Pixels never leave the machine. Use this when the
 * "raw frame pixels never leave the device" claim must be literally true.
 *
 * Setup:
 *   1. Install Ollama: https://ollama.com/download
 *   2. Pull moondream:  ollama pull moondream
 *   3. Make sure Ollama is running (it starts a local server on :11434)
 *
 * Tradeoffs vs. Claude vision:
 *   - Zero cost per frame, zero network
 *   - ~3-5x lower description quality on complex scenes
 *   - Runs on CPU (~2-5s/frame on a modern laptop) or GPU (sub-second)
 */

const DEFAULT_OLLAMA_URL =
  process.env.OLLAMA_BASE_URL ?? "http://127.0.0.1:11434";
const DEFAULT_MODEL = "moondream";

export interface OllamaVisionOptions {
  /** Override the Ollama base URL. Falls back to OLLAMA_BASE_URL env or localhost. */
  baseUrl?: string;
  /** Model name as pulled by `ollama pull`. Default "moondream". */
  model?: string;
  /** Per-request timeout in ms. Default 30000. */
  timeoutMs?: number;
}

/**
 * Generate a scene description for one frame using a local Ollama model.
 *
 * @param imageBytes  Raw image bytes (JPEG/PNG).
 * @param contextPrompt  Procedure-specific instruction.
 * @param opts  Base URL / model / timeout overrides.
 * @returns  A plain-text description ready for logFrame({ sceneDescription }).
 */
export async function describeFrameWithMoondream(
  imageBytes: Buffer | Uint8Array,
  contextPrompt: string,
  opts: OllamaVisionOptions = {},
): Promise<string> {
  const base = (opts.baseUrl ?? DEFAULT_OLLAMA_URL).replace(/\/$/, "");
  const url = `${base}/api/generate`;
  const base64 = Buffer.from(imageBytes).toString("base64");

  const body = {
    model: opts.model ?? DEFAULT_MODEL,
    prompt:
      `${contextPrompt}\n\n` +
      "Describe what you see in a single dense paragraph. No preamble, " +
      "no list, no markdown. If something is unclear, say 'partially obscured' " +
      "instead of guessing.",
    images: [base64],
    stream: false,
  };

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), opts.timeoutMs ?? 30000);

  try {
    const resp = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: controller.signal,
    });

    if (!resp.ok) {
      const errText = await resp.text();
      throw new Error(`Ollama ${resp.status}: ${errText}`);
    }

    const data = (await resp.json()) as { response?: string };
    if (!data.response) {
      throw new Error("Ollama returned empty response");
    }
    return data.response.trim();
  } catch (err) {
    if ((err as Error).name === "AbortError") {
      throw new Error(
        `Ollama vision call timed out after ${opts.timeoutMs ?? 30000}ms. ` +
          "On CPU, moondream needs 3-5s per frame — consider raising the timeout.",
      );
    }
    if ((err as NodeJS.ErrnoException).code === "ECONNREFUSED") {
      throw new Error(
        `Cannot reach Ollama at ${base}. Is the server running? ` +
          "Try: ollama serve",
      );
    }
    throw err;
  } finally {
    clearTimeout(timer);
  }
}
