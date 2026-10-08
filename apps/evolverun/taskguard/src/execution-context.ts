import { mkdir, writeFile } from "node:fs/promises";
import { homedir } from "node:os";
import { join } from "node:path";
import type { TemplateContext } from "./runner.js";
import type {
  ExecutionMode,
  WorkflowContextHistoryMode,
  WorkflowContextPolicy,
  WorkflowNode,
  WorkflowSpec,
} from "./types.js";
import { getLegacyApprovalExecutor } from "./legacy-runtime.js";
import type { CompressionStats, ContextCompressionConfig, ContextCompressionDefaults } from "./context/types.js";
import { compressContextAsync } from "./context/pipeline.js";
import { buildTailSessionFile } from "./context/tail-mode.js";
import { compactSessionFile, SESSION_COMPRESSION_DEFAULTS } from "./context/session-compressor.js";
import type { SessionCompressionConfig } from "./context/session-compressor.js";

// ── Directory creation cache ────────────────────────────────────────
// Concurrent nodes that write to the same parent dir don't need to
// call mkdir({ recursive: true }) repeatedly — the stat syscall is
// redundant after the first creation within a short window.
//
// Unlike the other caches in this project, directories can be deleted
// externally (manual cleanup, disk pressure).  A TTL avoids returning
// stale "directory exists" entries forever, and the writeFile call site
// catches ENOENT and retries with a fresh mkdir if the directory was
// removed after caching.

const CREATED_DIR_TTL_MS = 60_000; // 1 minute — balances I/O savings vs. staleness
const createdDirs = new Map<string, number>(); // dir → timestamp

async function ensureDir(dir: string): Promise<void> {
  const now = Date.now();
  const cached = createdDirs.get(dir);
  if (cached !== undefined && (now - cached) < CREATED_DIR_TTL_MS) return;
  await mkdir(dir, { recursive: true });
  createdDirs.set(dir, now);
  // Evict expired entries periodically (bounded by number of unique dirs)
  if (createdDirs.size > 200) {
    for (const [key, ts] of createdDirs) {
      if (now - ts >= CREATED_DIR_TTL_MS) createdDirs.delete(key);
    }
  }
}

/** Clear directory creation cache — for test isolation. */
export function clearCreatedDirsCache(): void {
  createdDirs.clear();
}

export type EffectiveContextPolicy = Required<
  Pick<WorkflowContextPolicy, "history" | "includeSessionHistory">
> & Omit<WorkflowContextPolicy, "history" | "includeSessionHistory">;

export type ResolveEffectiveContextPolicyParams = {
  workflow: WorkflowSpec;
  node: WorkflowNode;
  executionMode: ExecutionMode;
};

export type BuildNodeExecutionContextParams = ResolveEffectiveContextPolicyParams & {
  flowId: string;
  attempt: number;
  prompt: string;
  templateCtx: TemplateContext;
  currentSessionFile: string;
  rootDir?: string;
  /** Global compression defaults from application config. */
  compressionDefaults?: ContextCompressionDefaults;
  /** Session compression config from application config. */
  sessionCompressionConfig?: import("./context/session-compressor.js").SessionCompressionConfig;
};

export type BuiltNodeExecutionContext = {
  history: WorkflowContextHistoryMode;
  sessionFile: string;
  inheritedSessionFile: boolean;
  includedNodeOutputs: string[];
  /** Compression statistics, present when context compression was applied. */
  compressionStats?: CompressionStats;
  /**
   * Structured workflow context for injection into the agent prompt.
   * Present when history === "structured"; the embedded-agent runtime
   * may overwrite the session file, so this context must be injected
   * via the prompt parameter instead of relying on the session file.
   */
  workflowContext?: Record<string, unknown>;
};

export type BuiltStructuredWorkflowContext = {
  workflowContext: Record<string, unknown>;
  includedNodeOutputs: string[];
  /** Compression statistics, present when context compression was applied. */
  compressionStats?: CompressionStats;
};

function executorDefaultHistory(
  node: WorkflowNode,
  executionMode: ExecutionMode,
): WorkflowContextHistoryMode {
  if (node.executor.type === "embedded-agent") return "structured";
  if (node.executor.type === "subagent") return "isolated";
  if (node.executor.type === "collaboration") {
    return executionMode === "private" ? "isolated" : "structured";
  }
  if (getLegacyApprovalExecutor(node)) {
    return executionMode === "private" ? "isolated" : "structured";
  }
  return "structured";
}

function workflowDefaultPolicy(
  workflow: WorkflowSpec,
  node: WorkflowNode,
): WorkflowContextPolicy | undefined {
  if (node.executor.type === "embedded-agent") {
    return workflow.defaults?.contextPolicy?.embeddedAgent;
  }
  if (
    getLegacyApprovalExecutor(node)
    || node.executor.type === "subagent"
    || node.executor.type === "collaboration"
  ) {
    return workflow.defaults?.contextPolicy?.subagent;
  }
  return undefined;
}

function executorPolicy(node: WorkflowNode): WorkflowContextPolicy | undefined {
  if (
    node.executor.type === "embedded-agent"
    || node.executor.type === "subagent"
    || node.executor.type === "collaboration"
  ) {
    return node.executor.contextPolicy;
  }
  return getLegacyApprovalExecutor(node)?.contextPolicy;
}

export function resolveEffectiveContextPolicy(
  params: ResolveEffectiveContextPolicyParams,
): EffectiveContextPolicy {
  const workflowPolicy = workflowDefaultPolicy(params.workflow, params.node) ?? {};
  const nodePolicy = executorPolicy(params.node) ?? {};
  const merged: WorkflowContextPolicy = {
    ...workflowPolicy,
    ...nodePolicy,
  };
  const history = merged.history
    ?? (merged.includeSessionHistory ? "inherit" : executorDefaultHistory(params.node, params.executionMode));

  return {
    ...merged,
    history,
    includeSessionHistory: history === "inherit",
  };
}

function safePathPart(value: string): string {
  return value.replace(/[^a-zA-Z0-9._-]/g, "_");
}

function contextRoot(rootDir?: string): string {
  return rootDir ?? join(
    homedir(),
    ".openclaw",
    "logs",
    "clawmind",
    "embedded-sessions",
  );
}

function dependencyOutputs(
  workflow: WorkflowSpec,
  node: WorkflowNode,
  templateCtx: TemplateContext,
): Record<string, Record<string, unknown>> {
  const nodesById = new Map(workflow.nodes.map((item) => [item.id, item]));
  const outputs: Record<string, Record<string, unknown>> = {};
  const visited = new Set<string>();

  function collect(depId: string): void {
    if (visited.has(depId)) return;
    visited.add(depId);

    const depNode = nodesById.get(depId);
    for (const parentId of depNode?.dependsOn ?? []) {
      collect(parentId);
    }

    const output = templateCtx.nodeOutput[depId];
    if (output) outputs[depId] = output;
  }

  for (const depId of node.dependsOn) {
    collect(depId);
  }

  return outputs;
}

function sessionMessage(role: "system" | "user", text: string): string {
  return JSON.stringify({
    type: "message",
    timestamp: new Date().toISOString(),
    message: {
      role,
      content: [{ type: "text", text }],
    },
  });
}

export async function buildStructuredWorkflowContext(params: {
  workflow: WorkflowSpec;
  node: WorkflowNode;
  flowId: string;
  templateCtx: TemplateContext;
  history: WorkflowContextHistoryMode;
  contextPolicy?: EffectiveContextPolicy;
  compressionDefaults?: ContextCompressionDefaults;
}): Promise<BuiltStructuredWorkflowContext> {
  const rawNodeOutput = params.history === "structured"
    ? dependencyOutputs(params.workflow, params.node, params.templateCtx)
    : {};

  // Apply context compression if configured (per-node config or global defaults enabled)
  let nodeOutput = rawNodeOutput;
  let includedNodeOutputs = Object.keys(nodeOutput);
  let compressionStats: CompressionStats | undefined;

  const compressionConfig = params.contextPolicy?.compression;
  const globalDefaultsEnabled = params.compressionDefaults?.enabled === true;
  const shouldCompress = (compressionConfig || globalDefaultsEnabled) && params.history === "structured";

  console.log(
    `[context-compression] buildStructuredWorkflowContext: node=${params.node.id} flow=${params.flowId}, ` +
    `history=${params.history}, globalDefaultsEnabled=${globalDefaultsEnabled}, ` +
    `hasNodeConfig=${compressionConfig !== undefined}, shouldCompress=${shouldCompress}, ` +
    `rawNodeOutputKeys=[${Object.keys(rawNodeOutput).join(",")}]`,
  );

  if (shouldCompress) {
    const result = await compressContextAsync({
      workflow: params.workflow,
      node: params.node,
      nodeOutput: rawNodeOutput,
      config: compressionConfig,
      globalDefaults: params.compressionDefaults,
    });
    nodeOutput = result.context;
    includedNodeOutputs = result.includedNodeOutputs;
    compressionStats = result.stats;

    if (compressionStats?.wasCompressed) {
      console.log(
        `[context-compression] node=${params.node.id} flow=${params.flowId}: ` +
        `${compressionStats.inputTokens} → ${compressionStats.outputTokens} tokens ` +
        `(ratio=${compressionStats.ratio?.toFixed(2)}, steps=[${compressionStats.stepsApplied.join(",")}]) ` +
        `in ${compressionStats.durationMs}ms`,
      );
    }
  }

  return {
    workflowContext: {
      workflowId: params.workflow.id,
      flowId: params.flowId,
      nodeId: params.node.id,
      nodeTitle: params.node.title,
      history: params.history,
      params: params.templateCtx.params ?? {},
      user: params.templateCtx.user ?? {},
      workflowData: params.history === "structured" ? params.templateCtx.workflowData ?? {} : {},
      nodeOutput,
      flowHooks: params.history === "structured" ? params.templateCtx.flowHooks ?? {} : {},
    },
    includedNodeOutputs,
    compressionStats,
  };
}

export async function buildNodeExecutionContext(
  params: BuildNodeExecutionContextParams,
): Promise<BuiltNodeExecutionContext> {
  if (getLegacyApprovalExecutor(params.node) && params.executionMode !== "private" && params.executionMode !== "dingtalk-group") {
    return {
      history: "inherit",
      sessionFile: params.currentSessionFile,
      inheritedSessionFile: true,
      includedNodeOutputs: [],
      compressionStats: undefined,
      workflowContext: undefined,
    };
  }

  const policy = resolveEffectiveContextPolicy(params);

  console.log(
    `[context-compression] buildNodeExecutionContext: node=${params.node.id} flow=${params.flowId}, ` +
    `history=${policy.history}, includeSessionHistory=${policy.includeSessionHistory}, ` +
    `compressionDefaults=${params.compressionDefaults ? `enabled=${params.compressionDefaults.enabled}` : "undefined"}`,
  );

  if (policy.history === "inherit") {
    return {
      history: "inherit",
      sessionFile: params.currentSessionFile,
      inheritedSessionFile: true,
      includedNodeOutputs: [],
      compressionStats: undefined,
      workflowContext: undefined,
    };
  }

  if (policy.history === "tail") {
    return buildTailSessionFile({
      currentSessionFile: params.currentSessionFile,
      workflowId: params.workflow.id,
      flowId: params.flowId,
      nodeId: params.node.id,
      attempt: params.attempt,
      tailMessages: policy.tailMessages ?? 10,
      excludeInjectMessages: policy.excludeInjectMessages ?? false,
      rootDir: params.rootDir,
    });
  }

  if (policy.history === "compacted") {
    return buildCompactedSessionFile({
      currentSessionFile: params.currentSessionFile,
      workflowId: params.workflow.id,
      flowId: params.flowId,
      nodeId: params.node.id,
      attempt: params.attempt,
      compressionDefaults: params.compressionDefaults,
      sessionCompressionConfig: params.sessionCompressionConfig,
      rootDir: params.rootDir,
    });
  }

  const { workflowContext, includedNodeOutputs, compressionStats } = await buildStructuredWorkflowContext({
    workflow: params.workflow,
    node: params.node,
    flowId: params.flowId,
    templateCtx: params.templateCtx,
    history: policy.history,
    contextPolicy: policy,
    compressionDefaults: params.compressionDefaults,
  });
  const dir = join(
    contextRoot(params.rootDir),
    safePathPart(params.workflow.id),
    safePathPart(params.flowId),
  );
  const sessionFile = join(
    dir,
    `${safePathPart(params.node.id)}-attempt-${params.attempt}.jsonl`,
  );
  const lines = [
    sessionMessage(
      "system",
      "这是 clawmind 为当前节点创建的独立执行会话。只能使用后续消息提供的工作流结构化上下文与节点 prompt，不要假设存在主会话历史。",
    ),
    sessionMessage("user", `节点 Prompt:\n${params.prompt}`),
    sessionMessage("user", `Workflow Context JSON:\n${JSON.stringify(workflowContext, null, 2)}`),
  ];

  await ensureDir(dir);
  try {
    await writeFile(sessionFile, `${lines.join("\n")}\n`, "utf8");
  } catch (err: unknown) {
    // If the directory was deleted externally after caching, retry once
    if (err instanceof Error && "code" in err && (err as NodeJS.ErrnoException).code === "ENOENT") {
      createdDirs.delete(dir); // invalidate stale cache entry
      await mkdir(dir, { recursive: true });
      await writeFile(sessionFile, `${lines.join("\n")}\n`, "utf8");
    } else {
      throw err;
    }
  }

  return {
    history: policy.history,
    sessionFile,
    inheritedSessionFile: false,
    includedNodeOutputs,
    compressionStats,
    workflowContext,
  };
}

// ── Compacted History Mode ──

type CompactedSessionFileParams = {
  currentSessionFile: string;
  workflowId: string;
  flowId: string;
  nodeId: string;
  attempt: number;
  compressionDefaults?: ContextCompressionDefaults;
  sessionCompressionConfig?: import("./context/session-compressor.js").SessionCompressionConfig;
  rootDir?: string;
};

/**
 * Build a compacted session file from the inherited session.
 *
 * Reads the current session file, applies session compression (tool-output-prepass
 * + sliding window), and writes a new per-attempt session file.
 *
 * This is the "compacted" history mode: inherits the full session but
 * compresses verbose tool outputs and evicts old messages to stay within
 * a token budget.
 */
async function buildCompactedSessionFile(
  params: CompactedSessionFileParams,
): Promise<BuiltNodeExecutionContext> {
  const maxSessionTokens = params.sessionCompressionConfig?.maxSessionTokens ?? 50000;

  const dir = join(
    contextRoot(params.rootDir),
    safePathPart(params.workflowId),
    safePathPart(params.flowId),
  );
  const sessionFile = join(
    dir,
    `${safePathPart(params.nodeId)}-attempt-${params.attempt}-compacted.jsonl`,
  );

  const result = await compactSessionFile(
    params.currentSessionFile,
    sessionFile,
    { maxSessionTokens },
  );

  if (result.stats.wasCompressed) {
    console.log(
      `[session-compaction] node=${params.nodeId} flow=${params.flowId}: ` +
      `${result.stats.inputTokens} → ${result.stats.outputTokens} tokens ` +
      `(ratio=${result.stats.compressionRatio?.toFixed(2)}, ` +
      `toolsCompressed=${result.stats.toolResultsCompressed}, ` +
      `messagesEvicted=${result.stats.messagesEvicted}) ` +
      `in ${result.stats.durationMs}ms`,
    );
  }

  return {
    history: "compacted",
    sessionFile,
    inheritedSessionFile: false,
    includedNodeOutputs: [],
    compressionStats: {
      inputTokens: result.stats.inputTokens,
      outputTokens: result.stats.outputTokens,
      ratio: result.stats.compressionRatio,
      stepsApplied: result.stats.rulesApplied,
      evictedNodes: [],
      truncatedNodes: [],
      durationMs: result.stats.durationMs,
      warningTriggered: false,
      wasCompressed: result.stats.wasCompressed,
    },
    workflowContext: undefined,
  };
}
