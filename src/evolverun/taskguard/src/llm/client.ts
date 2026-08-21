/**
 * Shared LLM client — thin wrapper over an OpenAI-compatible chat completions API.
 *
 * Uses LLM_BASE_URL, LLM_API_KEY, LLM_MODEL environment variables
 * (consistent with the validation and context-compression modules).
 *
 * @module llm/client
 */

// ── Configuration ──

const LLM_BASE_URL = process.env.LLM_BASE_URL ?? "";
const LLM_API_KEY = process.env.LLM_API_KEY ?? "";
const LLM_MODEL = process.env.LLM_MODEL ?? "gpt-4o";

// ── Types ──

export type LlmCallOptions = {
  /** System prompt. */
  systemPrompt: string;
  /** User prompt. */
  userPrompt: string;
  /** Model override (takes precedence over LLM_MODEL env). */
  model?: string;
  /** Sampling temperature (default: 0.2). */
  temperature?: number;
  /** Maximum response tokens (default: 1024). */
  maxTokens?: number;
  /** Request timeout in ms (default: 30000). */
  timeoutMs?: number;
  /** Whether to request JSON response format (default: false). */
  jsonMode?: boolean;
};

export type LlmCallResult = {
  /** Raw text content from the LLM response. */
  content: string;
  /** Token usage from the API response, if available. */
  usage?: { promptTokens: number; completionTokens: number; totalTokens: number };
  /** Model that was actually used. */
  model: string;
};

export type LlmAvailability = {
  available: boolean;
  reason?: string;
};

// ── Availability check ──

/** Check whether LLM is configured and available. */
export function checkLlmAvailability(): LlmAvailability {
  if (!LLM_BASE_URL) return { available: false, reason: "LLM_BASE_URL not set" };
  if (!LLM_API_KEY) return { available: false, reason: "LLM_API_KEY not set" };
  return { available: true };
}

// ── Core call ──

/**
 * Call an OpenAI-compatible chat completions API.
 *
 * Throws on network errors, non-2xx responses, or timeouts.
 * Returns the raw content string — callers parse JSON as needed.
 */
export async function callLlm(options: LlmCallOptions): Promise<LlmCallResult> {
  const { systemPrompt, userPrompt, model, temperature, maxTokens, timeoutMs, jsonMode } = options;

  const baseUrl = LLM_BASE_URL.replace(/\/$/, "");
  const url = `${baseUrl}/v1/chat/completions`;
  const resolvedModel = model ?? LLM_MODEL;

  const body: Record<string, unknown> = {
    model: resolvedModel,
    messages: [
      { role: "system", content: systemPrompt },
      { role: "user", content: userPrompt },
    ],
    temperature: temperature ?? 0.2,
    max_tokens: maxTokens ?? 1024,
  };

  if (jsonMode) {
    body.response_format = { type: "json_object" };
  }

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs ?? 30000);

  try {
    const response = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${LLM_API_KEY}`,
      },
      body: JSON.stringify(body),
      signal: controller.signal,
    });

    if (!response.ok) {
      const text = await response.text().catch(() => response.statusText);
      throw new Error(`LLM API ${response.status}: ${text}`);
    }

    const data = (await response.json()) as {
      choices: Array<{ message: { content: string } }>;
      usage?: { prompt_tokens: number; completion_tokens: number; total_tokens: number };
    };

    const content = data.choices?.[0]?.message?.content ?? "";

    const usage = data.usage
      ? {
          promptTokens: data.usage.prompt_tokens,
          completionTokens: data.usage.completion_tokens,
          totalTokens: data.usage.total_tokens,
        }
      : undefined;

    return { content, usage, model: resolvedModel };
  } finally {
    clearTimeout(timer);
  }
}