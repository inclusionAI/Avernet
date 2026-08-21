import { readFileSync } from "node:fs";
import type { NodeState, TokenUsage, WorkflowUsage } from "./types.js";

const USAGE_KEYS = [
  "input",
  "output",
  "cacheRead",
  "cacheWrite",
  "totalTokens",
  "toolCalls",
] as const;

type UsageNumericKey = typeof USAGE_KEYS[number];

const TOKEN_KEYS = ["input", "output", "cacheRead", "cacheWrite", "totalTokens"] as const;
const ESTIMATE_METHOD = "cjk/ascii char heuristic";

const USAGE_ALIASES: Record<UsageNumericKey, string[]> = {
  input: ["input", "inputTokens", "promptTokens", "prompt_tokens"],
  output: ["output", "outputTokens", "completionTokens", "completion_tokens"],
  cacheRead: ["cacheRead", "cache_read", "cacheReadTokens", "cache_read_tokens"],
  cacheWrite: ["cacheWrite", "cache_write", "cacheWriteTokens", "cache_write_tokens"],
  totalTokens: ["totalTokens", "total_tokens", "total"],
  toolCalls: ["toolCalls", "toolCallCount", "tool_call_count"],
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function numericValue(value: unknown): number | undefined {
  if (typeof value === "number" && Number.isFinite(value)) return Math.max(0, Math.trunc(value));
  if (typeof value !== "string" || !value.trim()) return undefined;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? Math.max(0, Math.trunc(parsed)) : undefined;
}

export function normalizeTokenUsage(value: unknown): TokenUsage | undefined {
  if (!isRecord(value)) return undefined;

  const usage: TokenUsage = {};
  for (const key of USAGE_KEYS) {
    for (const alias of USAGE_ALIASES[key]) {
      const found = numericValue(value[alias]);
      if (found !== undefined) {
        usage[key] = found;
        break;
      }
    }
  }

  const hasAny = USAGE_KEYS.some((key) => usage[key] !== undefined);
  if (!hasAny) return undefined;

  if (usage.totalTokens === undefined) {
    const tokenParts = [usage.input, usage.output, usage.cacheRead, usage.cacheWrite]
      .filter((item): item is number => item !== undefined);
    if (tokenParts.length > 0) {
      usage.totalTokens = tokenParts.reduce((sum, item) => sum + item, 0);
    }
  }

  return compactTokenUsage(usage);
}

export function addTokenUsage(left: TokenUsage | undefined, right: TokenUsage | undefined): TokenUsage | undefined {
  if (!left && !right) return undefined;
  const merged: TokenUsage = {};
  for (const key of USAGE_KEYS) {
    const total = (left?.[key] ?? 0) + (right?.[key] ?? 0);
    if (total > 0 || left?.[key] !== undefined || right?.[key] !== undefined) {
      merged[key] = total;
    }
  }
  if (left?.estimated || right?.estimated) {
    merged.estimated = true;
    merged.source = left?.estimated && right?.estimated
      ? (left.source === right.source ? left.source : "mixed")
      : "mixed";
    merged.method = [left?.method, right?.method].filter(Boolean).join(" + ") || ESTIMATE_METHOD;
    merged.confidence = [left?.confidence, right?.confidence].includes("low") ? "low" : "medium";
  }
  return compactTokenUsage(merged);
}

export function hasPositiveTokenCounts(usage: TokenUsage | undefined): boolean {
  return TOKEN_KEYS.some((key) => (usage?.[key] ?? 0) > 0);
}

export function mergeTokenUsageWithFallback(
  primary: TokenUsage | undefined,
  fallback: TokenUsage | undefined,
): TokenUsage | undefined {
  if (!primary) return fallback;
  if (!fallback) return primary;
  if (hasPositiveTokenCounts(primary)) return primary;

  const merged: TokenUsage = {};
  for (const key of TOKEN_KEYS) {
    if (fallback[key] !== undefined) {
      merged[key] = fallback[key];
    }
  }
  const toolCalls = Math.max(primary.toolCalls ?? 0, fallback.toolCalls ?? 0);
  if (toolCalls > 0) {
    merged.toolCalls = toolCalls;
  }
  return compactTokenUsage(merged);
}

export function mergeTokenUsageWithEstimate(
  primary: TokenUsage | undefined,
  estimate: TokenUsage | undefined,
): TokenUsage | undefined {
  if (hasPositiveTokenCounts(primary)) return primary;
  if (!estimate) return primary;
  const merged: TokenUsage = { ...estimate };
  const toolCalls = Math.max(primary?.toolCalls ?? 0, estimate.toolCalls ?? 0);
  if (toolCalls > 0) {
    merged.toolCalls = toolCalls;
  }
  return compactTokenUsage(merged);
}

function compactTokenUsage(usage: TokenUsage): TokenUsage | undefined {
  if (hasPositiveTokenCounts(usage)) {
    return Object.keys(usage).length > 0 ? usage : undefined;
  }
  return (usage.toolCalls ?? 0) > 0 ? { toolCalls: usage.toolCalls } : undefined;
}

export function collectTokenUsageFromMessages(messages: unknown[]): TokenUsage | undefined {
  let total: TokenUsage | undefined;
  for (const message of messages) {
    total = addTokenUsage(total, extractTokenUsageFromMessage(message));
  }
  return total;
}

export function collectTokenUsageFromSessionFile(sessionFile: string | undefined): TokenUsage | undefined {
  if (!sessionFile) return undefined;
  try {
    const lines = readFileSync(sessionFile, "utf8")
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter(Boolean);
    const records = lines
      .map((line) => {
        try {
          return JSON.parse(line) as unknown;
        } catch {
          return undefined;
        }
      })
      .filter((record): record is unknown => record !== undefined);
    return collectTokenUsageFromMessages(records);
  } catch {
    return undefined;
  }
}

export function estimateTokenUsageFromSessionFile(sessionFile: string | undefined): TokenUsage | undefined {
  if (!sessionFile) return undefined;
  try {
    const records = readFileSync(sessionFile, "utf8")
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter(Boolean)
      .map((line) => {
        try {
          return JSON.parse(line) as unknown;
        } catch {
          return undefined;
        }
      })
      .filter((record): record is unknown => record !== undefined);
    return estimateTokenUsageFromMessages(records);
  } catch {
    return undefined;
  }
}

export function estimateTokenUsageFromMessages(messages: unknown[]): TokenUsage | undefined {
  let input = 0;
  let output = 0;
  let toolCalls = 0;

  for (const entry of messages) {
    const estimated = estimateTokenUsageFromMessage(entry);
    input += estimated.input;
    output += estimated.output;
    toolCalls += estimated.toolCalls;
  }

  const totalTokens = input + output;
  if (totalTokens <= 0 && toolCalls <= 0) return undefined;

  return compactTokenUsage({
    ...(input > 0 ? { input } : {}),
    ...(output > 0 ? { output } : {}),
    ...(totalTokens > 0 ? { totalTokens } : {}),
    ...(toolCalls > 0 ? { toolCalls } : {}),
    estimated: true,
    source: "estimated",
    method: ESTIMATE_METHOD,
    confidence: "low",
  });
}

export function extractTokenUsageFromMetadata(metadata: unknown): TokenUsage | undefined {
  if (!isRecord(metadata)) return undefined;
  const agentMeta = isRecord(metadata.agentMeta) ? metadata.agentMeta : undefined;
  let usage = addTokenUsage(
    normalizeTokenUsage(metadata.usage ?? metadata),
    extractToolCallUsage(metadata),
  );
  if (agentMeta) {
    usage = addTokenUsage(usage, normalizeTokenUsage(agentMeta.usage ?? agentMeta));
    usage = addTokenUsage(usage, extractToolCallUsage(agentMeta));
  }
  return usage;
}

function extractTokenUsageFromMessage(message: unknown): TokenUsage | undefined {
  if (!isRecord(message)) return undefined;
  const nestedMessage = message.message;
  if (isRecord(nestedMessage)) {
    let usage = normalizeTokenUsage(message.usage);
    usage = addTokenUsage(usage, extractToolCallUsage(message, { includeNestedContent: false }));
    usage = addTokenUsage(usage, normalizeTokenUsage(nestedMessage.usage));
    usage = addTokenUsage(usage, extractToolCallUsage(nestedMessage));
    return usage;
  }

  let usage = normalizeTokenUsage(message.usage);
  usage = addTokenUsage(usage, extractToolCallUsage(message));
  return usage;
}

function extractToolCallUsage(
  record: Record<string, unknown>,
  options: { includeNestedContent?: boolean } = {},
): TokenUsage | undefined {
  const explicit = normalizeTokenUsage(record);
  const historyMeta = isRecord(record.historyMeta) ? record.historyMeta : undefined;
  const aggregation = isRecord(historyMeta?.assistantAggregation) ? historyMeta.assistantAggregation : undefined;
  const aggregationCount = numericValue(aggregation?.toolCallCount);
  const contentCount = countToolCallBlocks(record.content);
  const nestedContentCount = options.includeNestedContent === false || !isRecord(record.message)
    ? 0
    : countToolCallBlocks(record.message.content);
  const toolSummary = isRecord(record.toolSummary) ? record.toolSummary : undefined;
  const toolSummaryCount = numericValue(toolSummary?.calls);
  const toolCalls = (explicit?.toolCalls ?? 0)
    + (aggregationCount ?? 0)
    + contentCount
    + nestedContentCount
    + (toolSummaryCount ?? 0);
  return toolCalls > 0 ? { toolCalls } : undefined;
}

function estimateTokenUsageFromMessage(entry: unknown): { input: number; output: number; toolCalls: number } {
  if (!isRecord(entry)) return { input: 0, output: 0, toolCalls: 0 };
  const message = isRecord(entry.message) ? entry.message : entry;
  const role = typeof message.role === "string" ? message.role : undefined;
  const contentInputText = extractToolCallPayloadText(message.content);
  const toolCalls = countToolCallBlocks(message.content);
  const contentText = extractContentText(message.content, { excludeToolCalls: true });
  const textTokens = estimateTextTokens(contentText);
  const toolPayloadTokens = estimateTextTokens(contentInputText);

  if (role === "assistant") {
    return {
      input: toolPayloadTokens,
      output: textTokens,
      toolCalls,
    };
  }
  return {
    input: textTokens + toolPayloadTokens,
    output: 0,
    toolCalls,
  };
}

function extractContentText(content: unknown, options: { excludeToolCalls?: boolean } = {}): string {
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  return content
    .map((block) => {
      if (!isRecord(block)) return "";
      if (options.excludeToolCalls && (block.type === "toolCall" || block.type === "tool_use")) return "";
      return typeof block.text === "string" ? block.text : "";
    })
    .filter(Boolean)
    .join("\n");
}

function extractToolCallPayloadText(content: unknown): string {
  if (!Array.isArray(content)) return "";
  return content
    .map((block) => {
      if (!isRecord(block) || (block.type !== "toolCall" && block.type !== "tool_use")) return "";
      const parts = [
        typeof block.name === "string" ? block.name : "",
        typeof block.arguments === "string" ? block.arguments : "",
        typeof block.input === "string" ? block.input : "",
        isRecord(block.arguments) ? JSON.stringify(block.arguments) : "",
        isRecord(block.input) ? JSON.stringify(block.input) : "",
      ].filter(Boolean);
      return parts.join("\n");
    })
    .filter(Boolean)
    .join("\n");
}

function estimateTextTokens(text: string): number {
  if (!text) return 0;
  let cjk = 0;
  let ascii = 0;
  let other = 0;
  for (const char of text) {
    if (/[\u3400-\u9fff\uf900-\ufaff]/u.test(char)) {
      cjk += 1;
    } else if (/[\x00-\x7f]/u.test(char)) {
      ascii += 1;
    } else {
      other += 1;
    }
  }
  return Math.ceil(cjk / 1.5 + ascii / 4 + other / 2);
}

function countToolCallBlocks(content: unknown): number {
  if (!Array.isArray(content)) return 0;
  return content.filter((block) => isRecord(block) && (block.type === "toolCall" || block.type === "tool_use")).length;
}

export function recomputeWorkflowUsage(nodeStates: Record<string, NodeState>): WorkflowUsage | undefined {
  const byNode: Record<string, TokenUsage> = {};
  let total: TokenUsage | undefined;

  for (const [nodeId, nodeState] of Object.entries(nodeStates)) {
    if (!nodeState.usage) continue;
    byNode[nodeId] = nodeState.usage;
    total = addTokenUsage(total, nodeState.usage);
  }

  return total ? { total, byNode } : undefined;
}

export function formatTokenUsage(usage: TokenUsage | undefined): string | undefined {
  const displayUsage = usage ? compactTokenUsage(usage) : undefined;
  if (!displayUsage) return undefined;
  if (displayUsage.estimated) {
    const parts = [
      displayUsage.source === "mixed" ? "部分估算" : "未上报",
      displayUsage.totalTokens !== undefined ? `估算≈${displayUsage.totalTokens.toLocaleString("en-US")}` : undefined,
      displayUsage.input !== undefined ? `input≈${displayUsage.input.toLocaleString("en-US")}` : undefined,
      displayUsage.output !== undefined ? `output≈${displayUsage.output.toLocaleString("en-US")}` : undefined,
      displayUsage.toolCalls !== undefined ? `toolCalls=${displayUsage.toolCalls.toLocaleString("en-US")}` : undefined,
    ].filter(Boolean);
    return parts.length > 0 ? parts.join(" | ") : undefined;
  }
  const parts = [
    !hasPositiveTokenCounts(displayUsage) ? "未上报" : undefined,
    displayUsage.totalTokens !== undefined ? `total=${displayUsage.totalTokens.toLocaleString("en-US")}` : undefined,
    displayUsage.input !== undefined ? `input=${displayUsage.input.toLocaleString("en-US")}` : undefined,
    displayUsage.output !== undefined ? `output=${displayUsage.output.toLocaleString("en-US")}` : undefined,
    displayUsage.cacheRead !== undefined ? `cacheRead=${displayUsage.cacheRead.toLocaleString("en-US")}` : undefined,
    displayUsage.cacheWrite !== undefined ? `cacheWrite=${displayUsage.cacheWrite.toLocaleString("en-US")}` : undefined,
    displayUsage.toolCalls !== undefined ? `toolCalls=${displayUsage.toolCalls.toLocaleString("en-US")}` : undefined,
  ].filter(Boolean);
  return parts.length > 0 ? parts.join(" | ") : undefined;
}
