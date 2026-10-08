import type { FlowState } from "../types.js";
import { buildKeywordMappings, REJECT_KEYWORDS, CONFIRM_KEYWORDS } from "./choice-keywords.js";

export type WaitingFlowInfo = {
  waitingNodeId: string;
  state: FlowState;
};

export type DetectedIntent = {
  command: string;
  /** The matched choice value (if choice-based), or "confirm"/"reject". */
  matchedValue?: string;
};

/**
 * Trailing particles commonly appended to Chinese utterances.
 * Stripping these allows "同意！" and "好的。" to match while
 * preserving the "exact match" property — the core intent word
 * must still be the entire message content.
 */
const TRAILING_PARTICLES_RE = /[。！？哈呀呢吧啦哦噢嘞]+$/u;

/**
 * Strip trailing particles from a trimmed, lowercased message.
 * Returns the message with particles removed for exact-match comparison.
 *
 * Examples:
 *   "同意！"  → "同意"
 *   "好的。"  → "好的"
 *   "同意，走A" → "同意，走a" (unchanged — comma is not a particle)
 */
export function stripTrailingParticles(text: string): string {
  return text.replace(TRAILING_PARTICLES_RE, "");
}

/**
 * Exact-match check: the entire text (minus trailing particles) must equal
 * one of the keywords. This prevents "好的，帮我查数据" from matching "好的".
 */
export function isExactMatch(text: string, keywords: readonly string[]): boolean {
  const trimmed = stripTrailingParticles(text.trim().toLowerCase());
  return keywords.some((kw) => trimmed === kw.toLowerCase());
}

/**
 * Detect whether a user's natural language message matches a waiting
 * flow's intent (choice selection, confirm, or reject).
 *
 * Uses isExactMatch for all checks — the entire message (after stripping
 * trailing particles) must equal a keyword. This prevents false positives
 * like "好的，帮我查数据" being matched as a confirm.
 *
 * Priority order:
 * 1. Reject keywords (highest priority — "算了" should not accidentally match a choice)
 * 2. Choice-based matching from inputSchema (keyword aliases or enum values)
 * 3. Generic confirm keywords (lowest priority — only if no choice field exists)
 * 4. Unrecognized → null (fall through to L0 Agent with hint)
 */
export function detectHumanGateIntent(
  body: string,
  flow: WaitingFlowInfo,
  overrideInputSchema?: { type?: string; required?: string[]; properties?: Record<string, any>; fields?: Record<string, any> },
): DetectedIntent | null {
  const { waitingNodeId, state } = flow;
  const nodeState = state.nodeStates[waitingNodeId];
  if (!nodeState || nodeState.status !== "waiting") return null;

  const text = body.trim().toLowerCase();

  // 1. Check for reject intent first (highest priority)
  if (isExactMatch(text, REJECT_KEYWORDS)) {
    return { command: "reject" };
  }

  // 2. Try choice-based matching from inputSchema
  // Use overrideInputSchema if provided (e.g. from executor.inputSchema), otherwise fall back to nodeState
  const inputSchema = overrideInputSchema ?? nodeState.waitInputSchema;
  const choiceField = extractChoiceField(inputSchema);
  if (choiceField) {
    const mappings = buildKeywordMappings(choiceField);
    for (const mapping of mappings) {
      if (isExactMatch(text, mapping.keywords)) {
        return { command: `confirm choice: ${mapping.choice}`, matchedValue: mapping.choice };
      }
    }
  }

  // 3. Check for generic confirm intent (no specific choice)
  if (isExactMatch(text, CONFIRM_KEYWORDS)) {
    return { command: "confirm" };
  }

  return null;
}

/**
 * Extract the first field with `enum` from a HumanInputSchema.
 * Returns undefined if no suitable choice field is found.
 */
function extractChoiceField(
  schema: { type?: string; required?: string[]; properties?: Record<string, any>; fields?: Record<string, any> } | undefined,
): any | undefined {
  if (!schema?.properties && !schema?.fields) return undefined;

  const props = schema.properties ?? schema.fields ?? {};

  for (const [, fieldSpec] of Object.entries(props)) {
    if (fieldSpec?.type === "string" && Array.isArray(fieldSpec?.enum) && fieldSpec.enum.length > 0) {
      return fieldSpec;
    }
  }
  return undefined;
}