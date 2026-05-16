/**
 * OpenEye Model Wiring
 *
 * Ready-to-use model objects for every supported provider.
 * Import the model you want, set the matching env var, done.
 *
 * Quick start:
 *   import { ANTHROPIC_SONNET, makeStreamFn, setupProviders } from "@dumbspacecookie/openeye";
 *   setupProviders();
 *   const agent = await OpenEyeAgent.create({ model: ANTHROPIC_SONNET, streamFn: makeStreamFn() });
 */

import { getModel, type Model } from "@mariozechner/pi-ai";
import type { Api } from "@mariozechner/pi-ai";

let _providersReady = false;

export function setupProviders(): void {
  if (_providersReady) return;
  _providersReady = true;
}

// ── Anthropic ───────────────────────────────────────────────────────────────

/** Best reasoning, largest context. Vision capable. */
export const ANTHROPIC_OPUS = getModel("anthropic", "claude-opus-4-6");

/** Best balance of speed and quality. Vision capable. Recommended default. */
export const ANTHROPIC_SONNET = getModel("anthropic", "claude-sonnet-4-6");

/** Fastest and cheapest Anthropic model. Good for high-volume step verification. */
export const ANTHROPIC_HAIKU = getModel("anthropic", "claude-haiku-4-5-20251001");

// ── OpenAI ──────────────────────────────────────────────────────────────────

/** GPT-4.1 — OpenAI's main production model. Vision capable. */
export const OPENAI_GPT41 = getModel("openai", "gpt-4.1");

/** GPT-4.1 Mini — fast and cheap. Good for classification tasks. */
export const OPENAI_GPT41_MINI = getModel("openai", "gpt-4.1-mini");

/** o4-mini — reasoning model, good for multi-step procedure logic. */
export const OPENAI_O4_MINI = getModel("openai", "o4-mini");

/** o3 — strongest OpenAI reasoning. Slower, higher cost. */
export const OPENAI_O3 = getModel("openai", "o3");

// ── Google ───────────────────────────────────────────────────────────────────

/** Gemini 2.5 Pro — Google's flagship. Longest context (1M tokens). Vision capable. */
export const GOOGLE_GEMINI_25_PRO = getModel("google", "gemini-2.5-pro");

/** Gemini 2.0 Flash — fast, cheap, vision capable. Good for real-time frame analysis. */
export const GOOGLE_GEMINI_20_FLASH = getModel("google", "gemini-2.0-flash");

// ── Groq ────────────────────────────────────────────────────────────────────

/** Llama 3.3 70B on Groq — very fast, solid quality, low cost. */
export const GROQ_LLAMA33_70B = getModel("groq", "llama-3.3-70b-versatile");

/** Llama 3.1 8B on Groq — extremely fast, sub-100ms. Use for latency-critical paths. */
export const GROQ_LLAMA31_8B = getModel("groq", "llama-3.1-8b-instant");

// ── Mistral ─────────────────────────────────────────────────────────────────

/** Mistral Large — best Mistral quality. Good European data-residency option. */
export const MISTRAL_LARGE = getModel("mistral", "mistral-large-latest");

/** Mistral Small — fast and cheap. */
export const MISTRAL_SMALL = getModel("mistral", "mistral-small-latest");

// ── OpenRouter ──────────────────────────────────────────────────────────────

/**
 * Access any OpenRouter model by its slug.
 * Full list: https://openrouter.ai/models
 */
export function openRouterModel(modelSlug: string): Model<any> {
  return getModel("openrouter", modelSlug as any);
}

export const OR_DEEPSEEK_V3 = openRouterModel("deepseek/deepseek-chat-v3-0324");
export const OR_LLAMA4_MAVERICK = openRouterModel("meta-llama/llama-4-maverick");
export const OR_QWEN3_235B = openRouterModel("qwen/qwen3-235b-a22b");

// ── Bedrock ─────────────────────────────────────────────────────────────────

/** Claude Sonnet 4.6 via Bedrock — same model, AWS data residency + IAM auth. */
export const BEDROCK_CLAUDE_SONNET = getModel("amazon-bedrock", "anthropic.claude-sonnet-4-6-v1:0" as any);

/** Claude Opus 4.6 via Bedrock. */
export const BEDROCK_CLAUDE_OPUS = getModel("amazon-bedrock", "anthropic.claude-opus-4-6-v1:0" as any);

// ── Ollama ───────────────────────────────────────────────────────────────────

/**
 * Create a model object for any locally-running Ollama model.
 * Set OLLAMA_BASE_URL if Ollama isn't on localhost:11434.
 */
export function ollamaModel(
  modelId: string,
  opts?: { contextWindow?: number; vision?: boolean }
): Model<any> {
  const baseUrl = process.env.OLLAMA_BASE_URL ?? "http://localhost:11434";
  // Use openrouter type as a compatible OpenAI-completions model
  const model = getModel("openrouter", modelId as any);
  return { ...model, baseUrl: `${baseUrl}/v1` };
}

export const OLLAMA_LLAMA33 = ollamaModel("llama3.3");
export const OLLAMA_QWEN25_VL = ollamaModel("qwen2.5-vl:72b", { vision: true, contextWindow: 32768 });
export const OLLAMA_LLAVA = ollamaModel("llava", { vision: true });
export const OLLAMA_MISTRAL_NEMO = ollamaModel("mistral-nemo");

// ── Custom OpenAI-compatible ────────────────────────────────────────────────

/**
 * Point at any OpenAI-compatible endpoint.
 */
export function customModel(
  modelId: string,
  opts: { baseUrl: string; apiKey?: string; contextWindow?: number; vision?: boolean; name?: string }
): Model<any> {
  const model = getModel("openrouter", modelId as any);
  return { ...model, baseUrl: opts.baseUrl, name: opts.name ?? modelId };
}

// ── Stream function builder ─────────────────────────────────────────────────

/**
 * Build the streamFn for a pi Agent.
 * Reads API keys from environment variables automatically for all known providers.
 */
export function makeStreamFn() {
  return async (model: Model<any>, context: any, options: any) => {
    const { stream } = await import("@mariozechner/pi-ai");
    return stream(model, context, options);
  };
}
