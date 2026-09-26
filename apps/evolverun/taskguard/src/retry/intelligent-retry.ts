/**
 * Intelligent retry — captures error context, searches KB for corrective hints,
 * and returns retry directives with errorRecoveryContext.
 *
 * Adapted from ClawMind's after-tool-call hook for ClawFlow's node-level retry model.
 */
import type { KnowledgeBase, CacheEntry } from "../knowledge/types.js";
import type { KnowledgeConfig } from "../config/types.js";
import type { RetryConfig } from "../config/types.js";
import type { PendingErrorContext } from "./error-context-store.js";
import { ErrorContextStore } from "./error-context-store.js";
import { RetryTracker, AutoRetryTracker } from "./retry-tracker.js";
import { searchAllKnowledgeBases } from "../knowledge/search.js";
import { extractKeywords } from "../knowledge/extractor.js";

/** Directive returned when a node failure should trigger an intelligent retry. */
export type RetryDirective = {
  /** Whether an auto-retry should be attempted. */
  shouldRetry: boolean;
  /** Error recovery context to inject into the next retry's template context. */
  errorRecoveryContext: string;
  /** The retry key (flowId:nodeId) for tracking. */
  retryKey: string;
  /** The attempt number that will be retried. */
  nextAttempt: number;
};

/**
 * Handle a node failure by capturing error context and optionally searching
 * knowledge bases for corrective hints.
 */
export async function handleNodeFailure(
  flowId: string,
  workflowId: string,
  nodeId: string,
  attempt: number,
  error: string,
  lastQuery: string | undefined,
  errorContextStore: ErrorContextStore,
  retryTracker: RetryTracker,
  autoRetryTracker: AutoRetryTracker,
  config: RetryConfig,
  knowledgeBases: KnowledgeBase[],
  knowledgeConfig: KnowledgeConfig | null,
  cache?: Map<string, CacheEntry>,
): Promise<RetryDirective> {
  const retryKey = `${flowId}:${nodeId}`;
  const signature = `${nodeId}:${error.slice(0, 200)}`;
  const retryCount = retryTracker.increment(signature);
  const autoRetryCount = autoRetryTracker.get(retryKey);

  // Build the error context entry
  const entry: PendingErrorContext = {
    flowId,
    nodeId,
    error: error.slice(0, 500),
    attempt,
    query: lastQuery,
    timestamp: Date.now(),
  };

  // Search KB for corrective hints if enabled
  let kbResults: string | undefined;
  if (config.kbSearchEnabled && knowledgeBases.length > 0 && knowledgeConfig?.enabled) {
    const searchQuery = lastQuery
      ? `${lastQuery} ${error.slice(0, 100)}`
      : extractKeywords(error, 5).join(" ");

    try {
      const results = await Promise.race([
        searchAllKnowledgeBases(knowledgeBases, searchQuery, knowledgeConfig.maxResults, cache, knowledgeConfig.cacheTtlMs),
        new Promise<Awaited<ReturnType<typeof searchAllKnowledgeBases>>>((resolve) =>
          setTimeout(() => resolve([]), knowledgeConfig.timeoutMs),
        ),
      ]);

      if (results.length > 0) {
        kbResults = results
          .map((r) => `Source: ${r.source} | Title: ${r.title}\n${r.content}`)
          .join("\n---\n");
      }
    } catch {
      // KB search is best-effort
    }
  }

  entry.kbResults = kbResults;
  errorContextStore.push(entry);

  // Determine if auto-retry should be attempted
  const shouldAutoRetry = autoRetryCount < config.maxAutoRetry;

  return {
    shouldRetry: shouldAutoRetry,
    errorRecoveryContext: kbResults
      ? `[ClawMind Error Context]\nNode "${nodeId}" failed (attempt ${attempt}): ${error.slice(0, 300)}\n\nKnowledge Base Suggestions:\n${kbResults}\n\nUse the above suggestions to correct the approach on retry.`
      : `[ClawMind Error Context]\nNode "${nodeId}" failed (attempt ${attempt}): ${error.slice(0, 300)}\n\nNo knowledge base suggestions found. Retry with adjusted approach.`,
    retryKey,
    nextAttempt: attempt + 1,
  };
}