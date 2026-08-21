/**
 * Knowledge injection types for ClawFlow.
 *
 * Adapted from ClawMind's knowledge subsystem for workflow-node-level
 * injection. Each node can optionally query knowledge bases before execution,
 * and the results are injected into the template context as `knowledgeContext`.
 */

/** A single search result from any knowledge base. */
export type KnowledgeBaseSearchResult = {
  /** Unique ID for deduplication (e.g. "yuque-12345" or "agentmind-kb-0"). */
  id: string;
  /** Document or result title. */
  title: string;
  /** Snippet content (already truncated and sanitized). */
  content: string;
  /** Source identifier (e.g. "yuque" or "agentmind"). */
  source: string;
  /** Relevance score in [0, 1]. Higher is more relevant. */
  relevance: number;
};

/** Interface that all knowledge base adapters must implement. */
export interface KnowledgeBase {
  /** Human-readable type identifier (e.g. "yuque", "agentmind"). */
  readonly type: string;
  /** Override for max results per search (optional). */
  readonly maxResults?: number;
  /** Search the knowledge base. Returns zero or more results. */
  search(query: string, maxResults: number): Promise<KnowledgeBaseSearchResult[]>;
}

/** Cache entry for search result deduplication and TTL. */
export type CacheEntry = {
  results: KnowledgeBaseSearchResult[];
  timestamp: number;
};

/** Formatted knowledge context ready for injection into a node's template. */
export type KnowledgeContext = {
  /** Formatted markdown block to prepend to the node prompt. */
  formattedText: string;
  /** Raw search results for potential downstream use. */
  results: KnowledgeBaseSearchResult[];
  /** Whether the search timed out. */
  timedOut: boolean;
};