/**
 * Context management and compression module for ClawMind workflows.
 *
 * @module context
 */

// Types
export type {
  CompressionStrategy,
  ContextPriority,
  TokenBudget,
  CompressionStep,
  ContextCompressionConfig,
  DependencyClassification,
  ContentType,
  ContentSegment,
  ContextEntry,
  CompressionStats,
  CompressionResult,
  CompressionProvenance,
  VerbatimMap,
  ContextCompressionDefaults,
} from "./types.js";

export { COMPRESSION_DEFAULTS, PRIORITY_ORDER } from "./types.js";

// Token counting
export {
  estimateTextTokens,
  estimateJsonTokens,
  estimateNodeOutputTokens,
  estimateContextTokens,
  classifyContent,
  buildContextEntries,
  sortByPriority,
  totalTokens,
  splitContentSegments,
  splitNodeOutputSegments,
  reassembleSegments,
} from "./token-counter.js";

// Budget management
export { resolveTokenBudget, TokenBudgetManager } from "./budget.js";

// Compressors
export {
  DedupCompressor,
  FuzzyDedupCompressor,
  ErrorPurgeCompressor,
  TruncateCompressor,
  PriorityEvictCompressor,
  KeyValueExtractCompressor,
  SentenceScoreCompressor,
  VerbatimCompressor,
  createCompressor,
} from "./compressors.js";
export type { ContextCompressor } from "./compressors.js";

// Pipeline
export {
  classifyDependencies,
  buildPipeline,
  compressContext,
  compressContextAsync,
} from "./pipeline.js";
export type { CompressContextOptions } from "./pipeline.js";

// LLM summarizer
export {
  summarizeContextEntries,
  isLlmSummarizationAvailable,
} from "./llm-summarizer.js";
export type { LlmSummarizerConfig, LlmSummarizeResult } from "./llm-summarizer.js";

// Tail mode
export { buildTailSessionFile } from "./tail-mode.js";
export type { TailModeOptions } from "./tail-mode.js";

// Session reader
export {
  readSessionFile,
  readSessionFileSync,
  writeSessionFile,
  parseSessionLine,
  createSystemMessage,
  modifyToolResultContent,
  estimateSessionTokens,
  resolveToolResultNames,
} from "./session-reader.js";
export type {
  SessionMessage,
  ParsedSession,
  ContentBlock,
  TextBlock,
  ToolUseBlock,
  ToolResultBlock,
} from "./session-reader.js";

// Content detector
export {
  detectContentType,
} from "./content-detector.js";
export type {
  ToolContentType,
  ContentDetectionResult,
} from "./content-detector.js";

// Tool output prepass
export {
  applyToolOutputPrepass,
} from "./tool-output-prepass.js";
export type {
  ToolOutputRule,
  ToolOutputPrepassResult,
} from "./tool-output-prepass.js";

// Tool result budget (age-based truncation)
export {
  budgetToolResults,
  DEFAULT_EXEMPT_TOOLS,
  DEFAULT_KEEP_RECENT,
  DEFAULT_MAX_TOOL_RESULT_TOKENS,
} from "./tool-result-budget.js";
export type {
  ToolResultBudgetOptions,
  ToolResultBudgetResult,
} from "./tool-result-budget.js";

// Semantic dedup (cross-message SimHash dedup)
export {
  simhashFingerprint,
  hammingDistance,
  deduplicateMessages,
} from "./semantic-dedup.js";
export type {
  SemanticDedupResult,
} from "./semantic-dedup.js";

// Conversation summarizer (deterministic turn summarization)
export {
  summarizeOldTurns,
} from "./conversation-summarizer.js";
export type {
  SummarizeOptions,
  SummarizeResult,
} from "./conversation-summarizer.js";

// Tiered compaction + circuit breaker
export {
  CircuitBreaker,
  determineCompactionLevel,
  applyTieredCompaction,
  DEFAULT_THRESHOLDS,
} from "./tiered-compaction.js";
export type {
  CompactionLevel,
  CompactionThresholds,
  TieredCompactionResult,
  CompactionStages,
  CircuitBreakerState,
  TieredCompactionOptions,
} from "./tiered-compaction.js";

// Session compressor
export {
  compactSession,
  compactSessionFile,
  maybeCompactSessionFile,
  maybeCompactSessionFileSafe,
  sidecarPathFor,
  SIDECAR_SUFFIX,
  MaybeCompactResult,
  slidingWindowCompact,
  compactionNoticeMessage,
  cleanupAbandonedSession,
  resetCompactionCircuitBreaker,
  SESSION_COMPRESSION_DEFAULTS,
} from "./session-compressor.js";
export type {
  SessionCompressionConfig,
  SessionCompressionResult,
  SessionCompressionStats,
} from "./session-compressor.js";

// Session watch compressor (runtime hook-based compression)
export {
  createBeforePromptBuildHook,
  createToolResultPersistHook,
  createBeforeToolCallHook,
  createCompressionHooks,
  truncateToolResult,
  registerSessionCompressionConfig,
  unregisterSessionCompressionConfig,
  getSessionCompressionEntry,
  updateSessionActualTokenEstimate,
} from "./session-watch-compressor.js";
export type {
  BeforePromptBuildEvent,
  BeforePromptBuildResult,
  ToolResultPersistEvent,
  ToolResultPersistResult,
  BeforeToolCallEvent,
  BeforeToolCallResult,
  SessionWatchCompressorConfig,
  CompressionHooks,
} from "./session-watch-compressor.js";