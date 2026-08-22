/**
 * Context management and compression types for ClawMind workflows.
 *
 * Provides token budget management, compression pipeline configuration,
 * and structured context entry classification for intelligent pruning.
 *
 * @module context/types
 */

// ── Compression Strategy ──

/** Strategy identifier for context compression. */
export type CompressionStrategy =
  | "verbatim"          // No compression, pass through unchanged
  | "dedup"             // Hash-based deduplication of identical node outputs
  | "fuzzy-dedup"       // Similarity-based deduplication of near-duplicate entries
  | "error-purge"       // Replace old error outputs with a placeholder
  | "truncate"          // Truncate individual node outputs to a max character limit
  | "priority-evict"    // Evict lowest-priority node outputs first to meet budget
  | "key-value-extract" // Extract top-level key-value summaries from verbose JSON
  | "sentence-score"    // Score sentences by info density, pack into token budget
  | "llm-summarize";    // LLM-based summarization (premium, async)

// ── Context Priority ──

/** Priority level for a node output entry in the context. */
export type ContextPriority =
  | "system"    // Always keep (workflowId, flowId, params, user metadata)
  | "critical"  // Direct parent dependency — only one upstream, must retain
  | "high"      // Direct dependency (dependsOn)
  | "medium"    // Transitive dependency within 2 hops
  | "low"       // Transitive dependency beyond 2 hops
  | "ephemeral"; // Can be dropped first (auxiliary/helper outputs)

/** Numeric ordering for priority comparisons (lower = more important). */
export const PRIORITY_ORDER: Record<ContextPriority, number> = {
  system: 0,
  critical: 1,
  high: 2,
  medium: 3,
  low: 4,
  ephemeral: 5,
};

// ── Token Budget ──

/** Token budget configuration for context compression. */
export type TokenBudget = {
  /** Maximum tokens allowed in the final compressed context. Default: 8000 */
  maxTokens: number;
  /**
   * Token count at which to emit a warning (before compression).
   * Default: 0.7 * maxTokens
   */
  warningThreshold?: number;
  /**
   * Strategy to apply when the budget is exceeded after pre-compression steps.
   * Default: "priority-evict"
   */
  overflowStrategy?: CompressionStrategy;
};

// ── Compression Step ──

/** Configuration for a single compression step in the pipeline. */
export type CompressionStep = {
  /** The compression strategy to apply. */
  strategy: CompressionStrategy;
  /** Strategy-specific parameters. */
  params?: Record<string, unknown>;
};

// ── Context Compression Config ──

/**
 * Context compression configuration.
 * Can be set at the workflow defaults level or overridden per-node.
 */
export type ContextCompressionConfig = {
  /** Token budget for the final context. If omitted, uses global default. */
  budget?: TokenBudget;
  /** Ordered pipeline of compression steps to apply. If omitted, uses global default steps. */
  steps?: CompressionStep[];
  /** Whether to include compression stats in node execution logs. Default: false */
  logStats?: boolean;
};

// ── Dependency Classification ──

/** Priority classification for a dependency node output. */
export type DependencyClassification = {
  /** The node ID of the dependency. */
  nodeId: string;
  /** Assigned priority level. */
  priority: ContextPriority;
  /** Distance in the dependency graph from the current node (0 = direct dependency). */
  depth: number;
  /** Whether this is a direct dependency (in dependsOn) vs transitive. */
  isDirect: boolean;
};

// ── Context Entry ──

/** Content type classification for compressor routing. */
export type ContentType = "code" | "prose" | "error" | "data" | "mixed";

/** A content segment produced by splitting a node output. */
export type ContentSegment = {
  /** Segment type: "code" (verbatim), "data" (structured JSON), "prose" (compressible). */
  kind: "code" | "data" | "prose";
  /** The text content of this segment. */
  text: string;
  /** Estimated token count for this segment. */
  tokenCount: number;
};

/** A single node output entry in the compression pipeline. */
export type ContextEntry = {
  /** The node ID that produced this output. */
  nodeId: string;
  /** Assigned priority level. */
  priority: ContextPriority;
  /** Depth in the dependency graph from the current node. */
  depth: number;
  /** The raw output data. */
  output: Record<string, unknown>;
  /** Estimated token count for this entry. */
  tokenCount: number;
  /** Content type classification for routing to compressors. */
  contentType: ContentType;
  /** Whether this entry was already compressed by a previous step. */
  compressed?: boolean;
  /** Marker for entries that were deduplicated (references original nodeId). */
  deduplicatedFrom?: string;
};

// ── Compression Result ──

/** Statistics from a compression pipeline run. */
export type CompressionStats = {
  /** Token count before compression. */
  inputTokens: number;
  /** Token count after compression. */
  outputTokens: number;
  /** Compression ratio (outputTokens / inputTokens), 1.0 = no change. */
  ratio: number;
  /** Names of compression steps that were actually applied. */
  stepsApplied: string[];
  /** Node IDs that were evicted (removed entirely). */
  evictedNodes: string[];
  /** Node IDs that had their output truncated. */
  truncatedNodes: string[];
  /** Time spent in compression (ms). */
  durationMs: number;
  /** Whether the warning threshold was exceeded before compression. */
  warningTriggered: boolean;
  /** Whether any compression step actually reduced the size. */
  wasCompressed: boolean;
};

/** Provenance metadata attached to each compressed entry by the engine. */
export type CompressionProvenance = {
  /** The compression method that produced this entry. */
  method: CompressionStrategy;
  /** Original token count before compression. */
  originalTokenCount: number;
  /** Original node output (for reversible decompression). */
  originalOutput: Record<string, unknown>;
  /** Monotonically increasing version, bumped on each re-compression. */
  version: number;
  /** Hash of the summary content for cache invalidation. */
  summaryHash?: string;
};

/** Map of nodeId → original output for lossless reversal of deterministic compression. */
export type VerbatimMap = Map<string, {
  output: Record<string, unknown>;
  tokenCount: number;
}>;

/** Result of compressing a context. */
export type CompressionResult = {
  /** The compressed node output map (nodeId → output data). */
  context: Record<string, Record<string, unknown>>;
  /** Node IDs that are included in the final context. */
  includedNodeOutputs: string[];
  /** Compression statistics. */
  stats: CompressionStats;
  /** Original outputs keyed by nodeId, enabling lossless reversal. */
  verbatim?: VerbatimMap;
};

// ── Global Defaults (application.yaml level) ──

/** Global default configuration for context compression. */
export type ContextCompressionDefaults = {
  /** Master switch for context compression. Default: false */
  enabled: boolean;
  /** Default max tokens when no per-workflow/node config is set. Default: 8000 */
  defaultMaxTokens: number;
  /** Default warning threshold ratio (0–1). Default: 0.7 */
  warningThresholdRatio: number;
  /** Default overflow strategy. Default: "priority-evict" */
  defaultOverflowStrategy: CompressionStrategy;
  /** Default compression pipeline steps when enabled. */
  defaultSteps: CompressionStep[];
};

/** Default values for ContextCompressionDefaults. */
export const COMPRESSION_DEFAULTS: ContextCompressionDefaults = {
  enabled: false,
  defaultMaxTokens: 8000,
  warningThresholdRatio: 0.7,
  defaultOverflowStrategy: "priority-evict",
  defaultSteps: [
    { strategy: "dedup" },
    { strategy: "error-purge", params: { maxAgeTurns: 2 } },
  ],
};

// ── Session Compression (node-level) ──

/** Configuration for session-level compression (compact mode and retry compression). */
export type SessionCompressionConfig = {
  /** Tool output prepass enabled. Default: true */
  toolPrepassEnabled: boolean;
  /** Maximum chars for a single tool result before truncation. Default: 5000 */
  toolResultMaxChars: number;
  /** Number of recent message pairs to keep unchanged. Default: 6 */
  recencyWindow: number;
  /** Token budget for the compressed session. Default: 50000 */
  maxSessionTokens: number;
  /** Whether to insert a compaction notice system message. Default: true */
  insertCompactionNotice: boolean;
  /** Whether to deduplicate repeated file reads. Default: true */
  deduplicateReads: boolean;
  /** TTL in ms for read dedup cache. Default: 300000 (5min) */
  readDedupTtlMs: number;
  /** Minimum session size (tokens) to trigger compression. Default: 30000 */
  minTokensToCompact: number;
};