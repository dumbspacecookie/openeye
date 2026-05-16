/**
 * Reference vision adapter — Claude vision (cloud).
 *
 * Turns a JPEG/PNG buffer into a plain-text scene description suitable for
 * passing to OpenEye's logFrame(). Uses the Anthropic Messages API directly
 * via fetch — no extra SDK dependency.
 *
 * Cost: roughly $0.003–$0.015 per frame on Sonnet 4.6 depending on resolution.
 * Use sparingly; consider running this on every Nth frame and using a local
 * model for the rest.
 *
 * Required env: ANTHROPIC_API_KEY
 */

const ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages";
const DEFAULT_MODEL = "claude-sonnet-4-6";
const DEFAULT_MAX_TOKENS = 300;

export interface ClaudeVisionOptions {
  /** Override the default Anthropic model. */
  model?: string;
  /** Max tokens in the description (default 300). Keep small — descriptions, not essays. */
  maxTokens?: number;
  /** API key. Falls back to ANTHROPIC_API_KEY env. */
  apiKey?: string;
  /** Image mime type. Auto-detected from buffer header if omitted. */
  mediaType?: "image/jpeg" | "image/png" | "image/webp" | "image/gif";
}

/**
 * Generate a scene description for one frame.
 *
 * @param imageBytes  Raw image bytes (JPEG, PNG, WebP, or GIF).
 * @param contextPrompt  Procedure-specific instruction. Example:
 *   "Operator is assembling part X. Describe hand position, tool in use,
 *    visible components, and any deviation from the standard sequence."
 * @param opts  Model / token / auth overrides.
 * @returns  A plain-text description ready for logFrame({ sceneDescription }).
 */
export async function describeFrameWithClaude(
  imageBytes: Buffer | Uint8Array,
  contextPrompt: string,
  opts: ClaudeVisionOptions = {},
): Promise<string> {
  const apiKey = opts.apiKey ?? process.env.ANTHROPIC_API_KEY;
  if (!apiKey) {
    throw new Error(
      "Anthropic API key required. Set ANTHROPIC_API_KEY or pass opts.apiKey.",
    );
  }

  const mediaType = opts.mediaType ?? detectMediaType(imageBytes);
  const base64 = Buffer.from(imageBytes).toString("base64");

  const body = {
    model: opts.model ?? DEFAULT_MODEL,
    max_tokens: opts.maxTokens ?? DEFAULT_MAX_TOKENS,
    messages: [
      {
        role: "user",
        content: [
          {
            type: "image",
            source: { type: "base64", media_type: mediaType, data: base64 },
          },
          {
            type: "text",
            text:
              `${contextPrompt}\n\n` +
              "Reply with a single dense paragraph. No preamble, no markdown, " +
              "no commentary about uncertainty — describe only what is visible. " +
              "If something is unclear, say 'partially obscured' rather than guessing.",
          },
        ],
      },
    ],
  };

  const resp = await fetch(ANTHROPIC_API_URL, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "x-api-key": apiKey,
      "anthropic-version": "2023-06-01",
    },
    body: JSON.stringify(body),
  });

  if (!resp.ok) {
    const errText = await resp.text();
    throw new Error(`Anthropic API ${resp.status}: ${errText}`);
  }

  const data = (await resp.json()) as {
    content: Array<{ type: string; text?: string }>;
  };
  const textBlock = data.content.find((c) => c.type === "text");
  if (!textBlock?.text) {
    throw new Error("Anthropic response contained no text content");
  }
  return textBlock.text.trim();
}

function detectMediaType(
  bytes: Buffer | Uint8Array,
): "image/jpeg" | "image/png" | "image/webp" | "image/gif" {
  const b = Buffer.from(bytes);
  if (b.length >= 3 && b[0] === 0xff && b[1] === 0xd8 && b[2] === 0xff) return "image/jpeg";
  if (b.length >= 8 && b[0] === 0x89 && b[1] === 0x50 && b[2] === 0x4e && b[3] === 0x47) return "image/png";
  if (b.length >= 4 && b[0] === 0x47 && b[1] === 0x49 && b[2] === 0x46) return "image/gif";
  if (b.length >= 12 && b.slice(0, 4).toString() === "RIFF" && b.slice(8, 12).toString() === "WEBP") return "image/webp";
  return "image/jpeg"; // sensible default for camera feeds
}
