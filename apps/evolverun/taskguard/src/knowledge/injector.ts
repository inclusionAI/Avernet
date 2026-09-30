/**
 * Knowledge injection for workflow nodes.
 *
 * Extracts keywords from node input, searches configured knowledge bases,
 * and formats results for injection into the node's prompt template context.
 *
 * Adapted from ClawMind's before-prompt-build hook for ClawFlow's
 * per-node knowledge injection model.
 */
import type { KnowledgeBase, KnowledgeBaseSearchResult, CacheEntry, KnowledgeContext } from "./types.js";
import type { KnowledgeConfig } from "../config/types.js";
import { searchAllKnowledgeBases } from "./search.js";
import { extractKeywords } from "./extractor.js";

/** Default maximum length for the formatted knowledge context (chars). */
const DEFAULT_MAX_CONTEXT_CHARS = 4000;

/** Patterns that may indicate prompt injection in KB content. */
const INJECTION_PATTERNS = [
  /\[SYSTEM\]/gi,
  /ignore\s+previous\s+instructions/gi,
  /\<\/?system\>/gi,
  /you\s+are\s+now\s+/gi,
];

/**
 * Sanitize KB content to reduce prompt injection risk.
 * Replaces suspicious patterns with safe placeholders.
 */
function sanitizeKbContent(content: string): string {
  let sanitized = content;
  for (const pattern of INJECTION_PATTERNS) {
    sanitized = sanitized.replace(pattern, "[filtered]");
  }
  return sanitized;
}

/**
 * Format search results into a markdown block for prompt injection.
 *
 * Each result is rendered as:
 * ```
 * ### Source: <source> | Title: <title> | Relevance: <relevance>
 * <content>
 * ---
 * ```
 */
function formatKnowledgeResults(results: KnowledgeBaseSearchResult[], maxChars: number): string {
  if (results.length === 0) return "";

  const lines: string[] = ["[ClawMind Knowledge Context]", ""];
  let totalLen = 0;

  for (const r of results) {
    const sanitized = sanitizeKbContent(r.content);
    const header = `### Source: ${r.source} | Title: ${r.title} | Relevance: ${r.relevance.toFixed(2)}`;
    const block = `${header}\n${sanitized}\n---`;
    const blockLen = block.length + 2; // +2 for newlines

    if (totalLen + blockLen > maxChars) {
      // Truncate this block to fit
      const remaining = maxChars - totalLen - header.length - 20;
      if (remaining > 50) {
        lines.push(`${header}\n${sanitized.slice(0, remaining)}\n..._truncated_\n---`);
      }
      break;
    }

    lines.push(block);
    totalLen += blockLen;
  }

  return lines.join("\n");
}

/**
 * Prepare knowledge context for a workflow node.
 *
 * Extracts keywords from the node's input, searches knowledge bases,
 * and returns a formatted context block ready for template injection.
 *
 * If the search times out, returns an empty context with timedOut=true.
 * If knowledge is disabled or no keywords are found, returns empty results.
 *
 * @param inputText The node's input text to extract keywords from
 * @param config Knowledge configuration
 * @param knowledgeBases Initialized knowledge base adapters
 * @param cache Optional search cache
 * @returns KnowledgeContext with formatted text, raw results, and timeout flag
 */
export async function prepareKnowledgeContext(
  inputText: string,
  config: KnowledgeConfig,
  knowledgeBases: KnowledgeBase[],
  cache?: Map<string, CacheEntry>,
): Promise<KnowledgeContext> {
  const empty: KnowledgeContext = {
    formattedText: "",
    results: [],
    timedOut: false,
  };

  if (!config.enabled || knowledgeBases.length === 0) {
    return empty;
  }

  // Extract keywords from the input
  const keywords = extractKeywords(inputText, 10);
  if (keywords.length === 0) {
    return empty;
  }

  const query = keywords.join(" ");
  const maxContextChars = DEFAULT_MAX_CONTEXT_CHARS;

  // Search with timeout
  let timedOut = false;
  let results: KnowledgeBaseSearchResult[];

  try {
    const searchPromise = searchAllKnowledgeBases(
      knowledgeBases,
      query,
      config.maxResults,
      cache,
      config.cacheTtlMs,
    );

    results = await Promise.race([
      searchPromise,
      new Promise<KnowledgeBaseSearchResult[]>((resolve) =>
        setTimeout(() => {
          timedOut = true;
          resolve([]);
        }, config.timeoutMs),
      ),
    ]);
  } catch {
    return { ...empty, timedOut: true };
  }

  if (timedOut || results.length === 0) {
    return { formattedText: "", results: [], timedOut };
  }

  const formattedText = formatKnowledgeResults(results, maxContextChars);
  return { formattedText, results, timedOut: false };
}