/**
 * Context compression pipeline orchestrator and dependency classification.
 *
 * Coordinates the flow: classify dependencies → build entries → estimate tokens
 * → apply compression steps → (optional) LLM summarization → reassemble.
 *
 * Provides both sync (compressContext) and async (compressContextAsync) variants.
 * The async variant adds LLM-based summarization as a final compression step.
 *
 * @module context/pipeline
 */

import type {
  CompressionResult,
  CompressionStats,
  CompressionStep,
  CompressionStrategy,
  ContextCompressionConfig,
  ContextCompressionDefaults,
  ContextEntry,
  ContextPriority,
  ContentType,
  DependencyClassification,
  VerbatimMap,
} from "./types.js";
import { COMPRESSION_DEFAULTS } from "./types.js";
import { TokenBudgetManager, resolveTokenBudget } from "./budget.js";
import { createCompressor } from "./compressors.js";
import type { ContextCompressor } from "./compressors.js";
import { summarizeContextEntries, isLlmSummarizationAvailable } from "./llm-summarizer.js";
import type { LlmSummarizerConfig } from "./llm-summarizer.js";
import {
  buildContextEntries,
  estimateNodeOutputTokens,
  sortByPriority,
  totalTokens,
} from "./token-counter.js";
import type { WorkflowNode, WorkflowSpec } from "../types.js";

// ── Dependency classification ──

/**
 * Classify all transitive dependencies of a node by priority and depth.
 *
 * Priority assignment:
 *   - "critical": direct parent when it's the only direct dependency (sole upstream)
 *   - "high": direct dependencies (in dependsOn)
 *   - "medium": transitive dependencies within 2 hops
 *   - "low": transitive dependencies beyond 2 hops
 *   - "ephemeral": (not assigned by default; can be set via custom logic)
 *
 * @param workflow - The workflow specification
 * @param node - The node whose dependencies to classify
 * @returns Array of dependency classifications sorted by depth (closest first)
 */
export function classifyDependencies(
  workflow: WorkflowSpec,
  node: WorkflowNode,
): DependencyClassification[] {
  const nodesById = new Map(workflow.nodes.map((n) => [n.id, n]));
  const visited = new Map<string, { depth: number; isDirect: boolean }>();

  // BFS from node's dependsOn
  const queue: Array<{ id: string; depth: number; isDirect: boolean }> = node.dependsOn.map(
    (depId) => ({ id: depId, depth: 0, isDirect: true }),
  );

  while (queue.length > 0) {
    const { id, depth, isDirect } = queue.shift()!;
    if (visited.has(id)) {
      const existing = visited.get(id)!;
      // Keep the closest depth and direct flag
      visited.set(id, {
        depth: Math.min(existing.depth, depth),
        isDirect: existing.isDirect || isDirect,
      });
      continue;
    }
    visited.set(id, { depth, isDirect });

    // Traverse parents
    const depNode = nodesById.get(id);
    if (depNode) {
      for (const parentId of depNode.dependsOn) {
        queue.push({ id: parentId, depth: depth + 1, isDirect: false });
      }
    }
  }

  // Determine priority based on depth and direct-ness
  const isSoleDirectDep = node.dependsOn.length === 1;

  return Array.from(visited.entries()).map(([nodeId, { depth, isDirect }]) => {
    let priority: ContextPriority;
    if (isDirect && isSoleDirectDep) {
      priority = "critical";
    } else if (isDirect) {
      priority = "high";
    } else if (depth <= 1) {
      priority = "medium";
    } else {
      priority = "low";
    }

    return { nodeId, priority, depth, isDirect };
  });
}

// ── Pipeline construction ──

/**
 * Build the ordered pipeline of compressors from configuration.
 * Excludes llm-summarize (handled separately in the async path).
 * Falls back to global defaults if no per-node config is provided.
 */
export function buildPipeline(
  config: ContextCompressionConfig | undefined,
  defaults: ContextCompressionDefaults | undefined,
): ContextCompressor[] {
  const global = defaults ?? COMPRESSION_DEFAULTS;
  const steps = config?.steps ?? global.defaultSteps;

  if (!steps || steps.length === 0) {
    // Default pipeline when no config: dedup → error-purge
    return [
      createCompressor("dedup"),
      createCompressor("error-purge"),
    ];
  }

  // Filter out llm-summarize from sync pipeline (it's handled in async path)
  return steps
    .filter((step) => step.strategy !== "llm-summarize")
    .map((step) => createCompressor(step.strategy));
}

/**
 * Check if any step in the pipeline config uses LLM summarization.
 */
function hasLlmSummarizeStep(
  config: ContextCompressionConfig | undefined,
  defaults: ContextCompressionDefaults | undefined,
): boolean {
  const global = defaults ?? COMPRESSION_DEFAULTS;
  const steps = config?.steps ?? global.defaultSteps;
  return steps?.some((step) => step.strategy === "llm-summarize") ?? false;
}

/**
 * Extract LLM summarizer config from compression step params.
 */
function extractLlmConfig(
  config: ContextCompressionConfig | undefined,
  defaults: ContextCompressionDefaults | undefined,
): LlmSummarizerConfig | undefined {
  const global = defaults ?? COMPRESSION_DEFAULTS;
  const steps = config?.steps ?? global.defaultSteps;
  const llmStep = steps?.find((step) => step.strategy === "llm-summarize");
  if (!llmStep?.params) return undefined;

  const params = llmStep.params;
  return {
    baseUrl: params.baseUrl as string | undefined,
    apiKey: params.apiKey as string | undefined,
    model: params.model as string | undefined,
    targetRatio: params.targetRatio as number | undefined,
    maxResponseTokens: params.maxResponseTokens as number | undefined,
    timeoutMs: params.timeoutMs as number | undefined,
    minTokensForSummarization: params.minTokensForSummarization as number | undefined,
    eligibleContentTypes: params.eligibleContentTypes as ContentType[] | undefined,
  } satisfies LlmSummarizerConfig;
}

// ── Context compression (sync — no LLM) ──

/** Options for compressContext. */
export type CompressContextOptions = {
  workflow: WorkflowSpec;
  node: WorkflowNode;
  nodeOutput: Record<string, Record<string, unknown>>;
  config?: ContextCompressionConfig;
  globalDefaults?: ContextCompressionDefaults;
};

/**
 * Compress the node output context using the configured pipeline (sync).
 * LLM summarization is NOT applied in the sync path.
 * Use compressContextAsync() for the full pipeline including LLM summarization.
 *
 * Flow:
 * 1. Classify dependencies → build ContextEntry[] with priorities
 * 2. Estimate tokens → check budget
 * 3. If under budget and no explicit steps → return as-is
 * 4. Apply zero-cost compression steps in order (dedup, error-purge, truncate, etc.)
 * 5. If still over budget after configured steps → apply overflow strategy
 * 6. Reassemble compressed nodeOutput and return with stats
 */
export function compressContext(options: CompressContextOptions): CompressionResult {
  const { workflow, node, nodeOutput, config, globalDefaults } = options;
  const defaults = globalDefaults ?? COMPRESSION_DEFAULTS;

  // Skip if compression is disabled and no per-node config
  if (!defaults.enabled && !config) {
    return noCompressionResult(nodeOutput);
  }

  const startTime = Date.now();
  const inputTokens = estimateNodeOutputTokens(nodeOutput);

  // 1. Classify dependencies
  const classifications = classifyDependencies(workflow, node);

  // 2. Build context entries
  let entries = buildContextEntries(classifications, nodeOutput);

  // 3. Check budget
  const budget = resolveTokenBudget(config, defaults);
  const budgetManager = new TokenBudgetManager(budget);
  const warningTriggered = budgetManager.isOverWarning(inputTokens);

  // 4. If under budget and no explicit steps configured, return as-is
  const pipeline = buildPipeline(config, defaults);
  if (!budgetManager.isOverBudget(inputTokens) && !config?.steps?.length) {
    return noCompressionResult(nodeOutput);
  }

  // 5. Apply zero-cost compression steps
  const stepsApplied = applyZeroCostSteps(pipeline, entries, config, defaults);
  entries = stepsApplied.entries;

  // 6. If still over budget, apply overflow strategy
  const overflowResult = applyOverflowStrategy(budgetManager, entries, budget);
  entries = overflowResult.entries;

  // 7. Collect stats and reassemble
  return assembleResult(
    entries,
    nodeOutput,
    inputTokens,
    startTime,
    warningTriggered,
    [...stepsApplied.names, ...overflowResult.names],
  );
}

// ── Context compression (async — includes LLM summarization) ──

/**
 * Compress the node output context using the full pipeline including LLM summarization.
 *
 * This is the async variant that applies all steps from compressContext(),
 * then additionally applies LLM-based summarization to eligible entries.
 *
 * LLM summarization is applied LAST, after all zero-cost steps,
 * and only to entries with eligible content types that exceed the
 * minimum token threshold.
 */
export async function compressContextAsync(
  options: CompressContextOptions,
): Promise<CompressionResult> {
  const { workflow, node, nodeOutput, config, globalDefaults } = options;
  const defaults = globalDefaults ?? COMPRESSION_DEFAULTS;

  // First, run the sync compression (all zero-cost steps + overflow)
  const syncResult = compressContext(options);

  // If no LLM summarization step is configured, or LLM is not available, return sync result
  const shouldSummarize = hasLlmSummarizeStep(config, defaults);
  if (!shouldSummarize || !isLlmSummarizationAvailable(extractLlmConfig(config, defaults))) {
    return syncResult;
  }

  // If compression didn't actually run (wasCompressed=false and warningTriggered=false),
  // but LLM step is explicitly configured, still apply LLM summarization
  if (!syncResult.stats.wasCompressed && !syncResult.stats.warningTriggered && !config?.steps?.length) {
    return syncResult;
  }

  const llmStartTime = Date.now();

  // Rebuild entries from the sync result's context (which may already be compressed)
  const classifications = classifyDependencies(workflow, node);
  let entries = buildContextEntries(classifications, syncResult.context);

  // Apply LLM summarization
  const llmConfig = extractLlmConfig(config, defaults);
  const llmResult = await summarizeContextEntries(entries, llmConfig);

  // Update entries with LLM-summarized versions
  entries = llmResult.entries;

  // Collect updated stats
  const compressedOutput: Record<string, Record<string, unknown>> = {};
  const includedNodeOutputs: string[] = [];
  for (const entry of entries) {
    compressedOutput[entry.nodeId] = entry.output;
    includedNodeOutputs.push(entry.nodeId);
  }

  const outputTokens = estimateNodeOutputTokens(compressedOutput);
  const wasCompressed = outputTokens < syncResult.stats.inputTokens;

  // Log LLM usage
  if (llmResult.summarizedCount > 0 || llmResult.failedCount > 0 || llmResult.aggressiveCount > 0 || llmResult.deterministicCount > 0) {
    console.log(
      `[context-compression] LLM summarization: ${llmResult.summarizedCount} normal, ${llmResult.aggressiveCount} aggressive, ${llmResult.deterministicCount} deterministic, ${llmResult.failedCount} failed, ` +
      `model=${llmConfig?.model ?? "default"}, tokens: ${syncResult.stats.inputTokens}→${outputTokens}`,
    );
  }

  // Merge verbatim maps: sync result verbatim + LLM provenance
  const verbatim: VerbatimMap = new Map(syncResult.verbatim ?? []);

  return {
    context: compressedOutput,
    includedNodeOutputs,
    stats: {
      ...syncResult.stats,
      outputTokens,
      ratio: syncResult.stats.inputTokens > 0 ? outputTokens / syncResult.stats.inputTokens : 1,
      stepsApplied: [...syncResult.stats.stepsApplied, "llm-summarize"],
      durationMs: syncResult.stats.durationMs + (Date.now() - llmStartTime),
      wasCompressed,
    },
    verbatim: verbatim.size > 0 ? verbatim : undefined,
  };
}

// ── Internal helpers ──

/** Apply all zero-cost compression steps and return updated entries + applied step names. */
function applyZeroCostSteps(
  pipeline: ContextCompressor[],
  entries: ContextEntry[],
  config: ContextCompressionConfig | undefined,
  defaults: ContextCompressionDefaults,
): { entries: ContextEntry[]; names: string[] } {
  const stepsApplied: string[] = [];
  const stepParams = config?.steps ?? defaults.defaultSteps;

  let currentEntries = entries;
  for (let i = 0; i < pipeline.length; i++) {
    const compressor = pipeline[i];
    const params = stepParams[i]?.params;
    const beforeTokens = totalTokens(currentEntries);
    currentEntries = compressor.compress(currentEntries, params);
    const afterTokens = totalTokens(currentEntries);

    if (afterTokens < beforeTokens) {
      stepsApplied.push(compressor.name);
    }
  }

  return { entries: currentEntries, names: stepsApplied };
}

/** Apply overflow strategy if still over budget. */
function applyOverflowStrategy(
  budgetManager: TokenBudgetManager,
  entries: ContextEntry[],
  budget: ReturnType<typeof resolveTokenBudget>,
): { entries: ContextEntry[]; names: string[] } {
  const currentTokens = totalTokens(entries);
  if (!budgetManager.isOverBudget(currentTokens)) {
    return { entries, names: [] };
  }

  const overflowStrategy = budget.overflowStrategy ?? "priority-evict";
  const overflowCompressor = createCompressor(overflowStrategy);
  const newEntries = overflowCompressor.compress(entries, {
    maxTokens: budgetManager.targetTokens(),
    preserveSystem: true,
  });
  return { entries: newEntries, names: [overflowStrategy] };
}

/** Assemble the final CompressionResult from entries. */
function assembleResult(
  entries: ContextEntry[],
  nodeOutput: Record<string, Record<string, unknown>>,
  inputTokens: number,
  startTime: number,
  warningTriggered: boolean,
  stepsApplied: string[],
): CompressionResult {
  const evictedNodeIds: string[] = [];
  const truncatedNodeIds: string[] = [];
  const verbatim: VerbatimMap = new Map();

  for (const entry of entries) {
    if (entry.compressed) {
      if (
        entry.output._truncated
        || entry.output._extracted
        || entry.output._purged
        || entry.output._deduplicated
        || entry.output._summarized
        || entry.output._scoreCompressed
        || entry.output._fuzzyDeduplicated
      ) {
        truncatedNodeIds.push(entry.nodeId);
      }
    }
  }

  const compressedOutput: Record<string, Record<string, unknown>> = {};
  const includedNodeOutputs: string[] = [];
  for (const entry of entries) {
    compressedOutput[entry.nodeId] = entry.output;
    includedNodeOutputs.push(entry.nodeId);

    // Store original output in verbatim map for reversible decompression
    if (entry.compressed && nodeOutput[entry.nodeId]) {
      verbatim.set(entry.nodeId, {
        output: nodeOutput[entry.nodeId]!,
        tokenCount: entry.tokenCount,
      });
    }
  }

  for (const nodeId of Object.keys(nodeOutput)) {
    if (!(nodeId in compressedOutput)) {
      evictedNodeIds.push(nodeId);
    }
  }

  const outputTokens = estimateNodeOutputTokens(compressedOutput);
  const wasCompressed = outputTokens < inputTokens;

  return {
    context: compressedOutput,
    includedNodeOutputs,
    stats: {
      inputTokens,
      outputTokens,
      ratio: inputTokens > 0 ? outputTokens / inputTokens : 1,
      stepsApplied,
      evictedNodes: evictedNodeIds,
      truncatedNodes: truncatedNodeIds,
      durationMs: Date.now() - startTime,
      warningTriggered,
      wasCompressed,
    },
    verbatim: verbatim.size > 0 ? verbatim : undefined,
  };
}

/** Return a no-compression result (identity). */
function noCompressionResult(
  nodeOutput: Record<string, Record<string, unknown>>,
): CompressionResult {
  const inputTokens = estimateNodeOutputTokens(nodeOutput);
  return {
    context: nodeOutput,
    includedNodeOutputs: Object.keys(nodeOutput),
    stats: {
      inputTokens,
      outputTokens: inputTokens,
      ratio: 1,
      stepsApplied: [],
      evictedNodes: [],
      truncatedNodes: [],
      durationMs: 0,
      warningTriggered: false,
      wasCompressed: false,
    },
    verbatim: undefined,
  };
}