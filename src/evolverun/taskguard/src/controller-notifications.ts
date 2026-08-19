/**
 * Controller Notification Builders — verbose chatInject message construction.
 *
 * Builds rich, structured Markdown messages for every workflow execution
 * lifecycle event. Controller.ts calls these builders and passes the result
 * to deps.chatInject(msg, key) unchanged.
 *
 * Inject levels (see inject-level.ts):
 * - "perf"   — only original notifications (node_failed + flow completed/failed)
 * - "simple" — full notifications for node lifecycle events
 * - "full"   — adds skipped/template-resolution details
 *
 * @module controller-notifications
 */

import type { WorkflowNode, WorkflowSpec, FlowState, NodeState, NodeRetrySpec, WorkflowOutputsSpec } from "./types.js";
import {
  readTemplatePathByDescriptor,
  resolveTemplateWithFormatter,
  type TemplateContext,
} from "./runner.js";
import { loadConfig } from "./config/loader.js";
import type { InjectLevel } from "./inject-level.js";

// ── Types ──

/** @deprecated Use {@link InjectLevel}. Kept as alias for migration. */
export type VerbosityLevel = InjectLevel;

/** Sensitive key patterns to redact from notifications. */
const SENSITIVE_PATTERNS = [
  /password/i, /passwd/i, /secret/i, /token/i, /apikey/i, /api_key/i,
  /api-key/i, /auth/i, /credential/i, /private.key/i, /access.key/i,
];

/**
 * Build a clickable clawweb URL for a given flowId.
 * The URL opens the run detail page in a new browser tab.
 */
function buildFlowRunUrl(flowId: string): string {
  try {
    const cfg = loadConfig();
    const base = cfg.app.api.clawwebUrl || cfg.app.api.baseUrl || "https://clawweb.antgroup-inc.cn";
    return `${base.replace(/\/+$/, "")}/runs/${flowId}`;
  } catch {
    return `https://clawweb.antgroup-inc.cn/runs/${flowId}`;
  }
}

const MAX_VALUE_LEN = 200;
const MAX_OUTPUT_VALUE_LEN = 2000;
const MAX_PROMPT_PREVIEW_LEN = 1000;
const PROMPT_PREVIEW_HEAD_RATIO = 0.6;
const PROMPT_OMISSION_PREFIX = "\n… [中间省略 ";
const PROMPT_OMISSION_SUFFIX = " 个字符] …\n";
const MAX_INPUT_FIELDS = 10;
const MAX_OUTPUT_FIELDS = 10;
const MAX_ERROR_LEN = 500;
const MAX_TEMPLATE_PREVIEW_DEPTH = 6;
const MAX_TEMPLATE_PREVIEW_ITEMS = 20;
const DISPLAY_OUTPUT_KEYS = [
  "displayMarkdown",
  "planMarkdown",
  "requirementMarkdown",
  "reportMarkdown",
  "displayText",
] as const;
const MAX_DISPLAY_BYTES = 16 * 1024;
const DISPLAY_TRUNCATION_SUFFIX = "\n\n> 展示内容已截断（上限 16 KB）";

// ── Truncation Helpers ──

/** Truncate a value string to maxLen, appending '…' if truncated. */
export function truncateValue(value: string, maxLen = MAX_VALUE_LEN): string {
  if (value.length <= maxLen) return value;
  return value.slice(0, maxLen) + "…";
}

export function truncatePromptPreview(prompt: string): string {
  const codePoints = Array.from(prompt);
  if (codePoints.length <= MAX_PROMPT_PREVIEW_LEN) return prompt;

  const fixedMarkerLength = Array.from(
    PROMPT_OMISSION_PREFIX + PROMPT_OMISSION_SUFFIX,
  ).length;
  const maxOmittedDigits = String(codePoints.length).length;
  let visibleBudget = 0;
  let omittedCount = 0;

  for (let omittedDigits = 1; omittedDigits <= maxOmittedDigits; omittedDigits += 1) {
    visibleBudget = MAX_PROMPT_PREVIEW_LEN - fixedMarkerLength - omittedDigits;
    omittedCount = codePoints.length - visibleBudget;
    if (String(omittedCount).length === omittedDigits) break;
  }

  const marker = PROMPT_OMISSION_PREFIX
    + omittedCount
    + PROMPT_OMISSION_SUFFIX;
  visibleBudget = MAX_PROMPT_PREVIEW_LEN - Array.from(marker).length;
  const headLength = Math.ceil(visibleBudget * PROMPT_PREVIEW_HEAD_RATIO);
  const tailLength = visibleBudget - headLength;

  return codePoints.slice(0, headLength).join("")
    + marker
    + codePoints.slice(codePoints.length - tailLength).join("");
}

/** Check if a key name looks sensitive and should be redacted. */
function isSensitiveKey(key: string): boolean {
  return SENSITIVE_PATTERNS.some((p) => p.test(key));
}

function isSensitivePath(path: string): boolean {
  const normalized = path.replace(/\[(\d+)\]/g, ".$1");
  return isSensitiveKey(normalized)
    || normalized.split(".").some((segment) => isSensitiveKey(segment));
}

function sanitizeTemplatePreviewValue(
  value: unknown,
  depth: number,
  seen: WeakSet<object>,
  path: string,
): unknown {
  if (typeof value === "function") return "[Function]";
  if (value === null || typeof value !== "object") return value;
  if (depth >= MAX_TEMPLATE_PREVIEW_DEPTH) return "[MaxDepth]";
  if (seen.has(value)) return "[Circular]";

  seen.add(value);
  try {
    if (Array.isArray(value)) {
      const visible: unknown[] = [];
      const visibleLength = Math.min(
        value.length,
        MAX_TEMPLATE_PREVIEW_ITEMS,
      );
      for (let index = 0; index < visibleLength; index += 1) {
        const descriptor = Object.getOwnPropertyDescriptor(value, String(index));
        if (!descriptor) {
          visible.push("[Unavailable]");
          continue;
        }
        if (!("value" in descriptor)) {
          visible.push("[Accessor]");
          continue;
        }
        visible.push(sanitizeTemplatePreviewValue(
          descriptor.value,
          depth + 1,
          seen,
          path + "[" + index + "]",
        ));
      }
      if (value.length > MAX_TEMPLATE_PREVIEW_ITEMS) {
        visible.push(
          "[+" + (value.length - MAX_TEMPLATE_PREVIEW_ITEMS) + " more]",
        );
      }
      Object.setPrototypeOf(visible, null);
      return visible;
    }

    const keys = Object.keys(value);
    const visible = Object.create(null) as Record<string, unknown>;
    for (const key of keys.slice(0, MAX_TEMPLATE_PREVIEW_ITEMS)) {
      const childPath = path ? path + "." + key : key;
      if (isSensitivePath(childPath)) {
        visible[key] = "***";
        continue;
      }

      const descriptor = Object.getOwnPropertyDescriptor(value, key);
      if (!descriptor) {
        visible[key] = "[Unavailable]";
      } else if (!("value" in descriptor)) {
        visible[key] = "[Accessor]";
      } else {
        visible[key] = sanitizeTemplatePreviewValue(
          descriptor.value,
          depth + 1,
          seen,
          childPath,
        );
      }
    }
    if (keys.length > MAX_TEMPLATE_PREVIEW_ITEMS) {
      visible["..."] =
        "[+" + (keys.length - MAX_TEMPLATE_PREVIEW_ITEMS) + " more]";
    }
    return visible;
  } finally {
    seen.delete(value);
  }
}

function formatTemplatePreviewValue(path: string, value: unknown): string {
  if (isSensitivePath(path)) return "***";
  const safeValue = sanitizeTemplatePreviewValue(
    value,
    0,
    new WeakSet<object>(),
    path,
  );
  if (safeValue == null) return "";
  if (typeof safeValue === "object") {
    return JSON.stringify(safeValue) ?? String(safeValue);
  }
  return String(safeValue);
}

function readOwnPreviewValue(
  record: object,
  key: string,
): unknown {
  const descriptor = Object.getOwnPropertyDescriptor(record, key);
  if (!descriptor) return undefined;
  if (!("value" in descriptor)) return "[Accessor]";
  return descriptor.value;
}

function summarizeWorkflowInputFiles(value: unknown): unknown[] | undefined {
  if (!Array.isArray(value) || value.length === 0) return undefined;

  const visibleLength = Math.min(value.length, MAX_TEMPLATE_PREVIEW_ITEMS);
  const summaries: unknown[] = [];
  for (let index = 0; index < visibleLength; index += 1) {
    const descriptor = Object.getOwnPropertyDescriptor(value, String(index));
    if (!descriptor) {
      summaries.push("[Unavailable]");
      continue;
    }
    if (!("value" in descriptor)) {
      summaries.push("[Accessor]");
      continue;
    }

    const file = descriptor.value;
    if (file === null || typeof file !== "object") {
      summaries.push("[Unavailable]");
      continue;
    }

    const summary = Object.create(null) as Record<string, unknown>;
    for (const key of ["name", "mimeType", "size"] as const) {
      const fieldValue = readOwnPreviewValue(file, key);
      if (fieldValue === undefined) continue;
      summary[key] = sanitizeTemplatePreviewValue(
        fieldValue,
        0,
        new WeakSet<object>(),
        "input.files[" + index + "]." + key,
      );
    }
    summaries.push(summary);
  }

  if (value.length > MAX_TEMPLATE_PREVIEW_ITEMS) {
    summaries.push("[+" + (value.length - MAX_TEMPLATE_PREVIEW_ITEMS) + " more]");
  }
  Object.setPrototypeOf(summaries, null);
  return summaries;
}

/** Redact sensitive values, truncate the rest. */
function safeValue(key: string, value: unknown, maxLen = MAX_VALUE_LEN): string {
  if (isSensitiveKey(key)) return "***";
  const s = typeof value === "string" ? value : JSON.stringify(value) ?? String(value);
  return truncateValue(s, maxLen);
}

/** Build a key: value block from a record, with redaction and truncation. */
function formatRecord(
  record: Record<string, unknown>,
  maxFields = MAX_INPUT_FIELDS,
  indentContinuations = false,
  maxValueLen = MAX_VALUE_LEN,
): string[] {
  const entries = Object.entries(record).slice(0, maxFields);
  const lines = entries.map(([k, v]) => {
    const formattedValue = safeValue(k, v, maxValueLen);
    const displayValue = indentContinuations
      ? formattedValue.replaceAll("\n", "\n  ")
      : formattedValue;
    return `  ${k}: ${displayValue}`;
  });
  if (Object.keys(record).length > maxFields) {
    lines.push(`  ... (+${Object.keys(record).length - maxFields} more fields)`);
  }
  return lines;
}

function formatPromptPreview(value: unknown): string[] {
  const prompt = typeof value === "string"
    ? value
    : JSON.stringify(value) ?? String(value);
  return truncatePromptPreview(prompt)
    .split("\n")
    .map((line) => "  " + line);
}

/** Format executor label from type string. */
function executorLabel(executor: WorkflowNode["executor"]): string {
  return `[${executor.type}]`;
}

/** Compute node duration from NodeState timestamps (seconds → ms). */
function nodeDurationMs(ns: NodeState): number | null {
  if (!ns.startedAt || !ns.completedAt) return null;
  return (ns.completedAt - ns.startedAt) * 1000;
}

/** Format duration for display. */
function formatDuration(ms: number | null): string {
  if (ms === null) return "-";
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

/** Node status emoji. */
function statusEmoji(status: string): string {
  switch (status) {
    case "succeeded": return "✅";
    case "failed": return "❌";
    case "skipped": return "⏭️";
    case "waiting": return "⏳";
    case "running": return "▶️";
    default: return "⏸️";
  }
}

// ── Workflow Started ──

export interface WorkflowStartedParams {
  workflow: WorkflowSpec;
  flowId: string;
  input?: Record<string, string>;
  level: InjectLevel;
}

export function buildWorkflowStartedMessage(params: WorkflowStartedParams): string {
  const { workflow, flowId, input, level } = params;
  if (level === "perf") {
    // minimal: keep it short, just flow id
    return `🔄 工作流启动: ${workflow.title ?? workflow.id} (flowId: ${flowId})`;
  }

  const lines: string[] = [];
  lines.push(`🔄 工作流启动: ${workflow.title ?? workflow.id}`);
  lines.push("━━━━━━━━━━━━━━━━━━━━━━━━━");
  lines.push(`📋 工作流: ${workflow.title ?? workflow.id} (${workflow.id})`);
  lines.push(`🔑 FlowId: ${flowId}`);

  // Input parameters
  if (input && Object.keys(input).length > 0) {
    lines.push("📝 输入:");
    for (const [k, v] of Object.entries(input).slice(0, MAX_INPUT_FIELDS)) {
      lines.push(`  ${k}: ${safeValue(k, v)}`);
    }
    if (Object.keys(input).length > MAX_INPUT_FIELDS) {
      lines.push(`  ... (+${Object.keys(input).length - MAX_INPUT_FIELDS} more)`);
    }
  }

  // DAG topology
  if (workflow.nodes && workflow.nodes.length > 0) {
    lines.push("📊 DAG 拓扑:");
    workflow.nodes.forEach((node, i) => {
      const deps = node.dependsOn && node.dependsOn.length > 0
        ? ` ← dependsOn: ${node.dependsOn.join(", ")}`
        : "";
      const trigger = node.triggerRule && node.triggerRule !== "all_success"
        ? ` (${node.triggerRule})`
        : "";
      lines.push(`  ${i + 1}. ${node.id} → ${executorLabel(node.executor)}${deps}${trigger}`);
    });
  }

  lines.push("⚡ 异步执行中，进度将通过 Channel 推送");
  return lines.join("\n");
}

// ── Node Started ──

export interface NodeStartedParams {
  node: WorkflowNode;
  nodeIndex: number;
  executorType: string;
  triggerRule?: string;
  workflowInput?: Record<string, unknown>;
  resolvedInput?: Record<string, unknown>;
  level: InjectLevel;
}

export function buildNodeStartedMessage(params: NodeStartedParams): string {
  const {
    node,
    nodeIndex,
    executorType,
    triggerRule,
    workflowInput,
    resolvedInput,
    level,
  } = params;
  if (level === "perf") return "";

  const lines: string[] = [];
  lines.push(`▶️ 节点开始: #${nodeIndex} ${node.id}`);
  lines.push("━━━━━━━━━━━━━━━━━━━━━━━━━");
  lines.push(`🔧 Executor: ${executorType}`);

  const isAgentExecutor = executorType === "embedded-agent"
    || executorType === "subagent";

  if (isAgentExecutor && workflowInput && Object.keys(workflowInput).length > 0) {
    lines.push("");
    lines.push("📥 Workflow Input:");
    lines.push(...formatRecord(workflowInput, MAX_INPUT_FIELDS, true));
  }

  const prompt = isAgentExecutor ? resolvedInput?.prompt : undefined;
  if (prompt !== undefined && prompt !== "") {
    lines.push("");
    lines.push("🧠 Prompt Preview:");
    lines.push(...formatPromptPreview(prompt));
  }

  const executorInput = resolvedInput
    ? Object.fromEntries(
      Object.entries(resolvedInput).filter(
        ([key]) => !isAgentExecutor || key !== "prompt",
      ),
    )
    : undefined;
  if (executorInput && Object.keys(executorInput).length > 0) {
    lines.push("📥 Input:");
    lines.push(...formatRecord(executorInput));
  }

  // Trigger rule (if non-default)
  if (triggerRule && triggerRule !== "all_success") {
    lines.push(`⏱️ 触发规则: ${triggerRule}`);
  }

  return lines.join("\n");
}

// ── Node Succeeded ──

export interface NodeSucceededParams {
  node: WorkflowNode;
  nodeIndex: number;
  output?: Record<string, unknown>;
  displayMarkdown?: string;
  durationMs: number | null;
  outputContractResult?: "pass" | "fail" | "none";
  level: InjectLevel;
}

export function buildNodeSucceededMessage(params: NodeSucceededParams): string {
  const {
    node,
    nodeIndex,
    output,
    displayMarkdown,
    durationMs,
    outputContractResult,
    level,
  } = params;
  if (level === "perf") return "";

  const lines: string[] = [];
  lines.push(`✅ 节点完成: #${nodeIndex} ${node.id} (耗时 ${formatDuration(durationMs)})`);
  lines.push("━━━━━━━━━━━━━━━━━━━━━━━━━");

  if (displayMarkdown) {
    lines.push(displayMarkdown);
  }

  // Output summary
  if (output && Object.keys(output).length > 0) {
    lines.push("📤 Output:");
    lines.push(...formatRecord(output, MAX_OUTPUT_FIELDS, false, MAX_OUTPUT_VALUE_LEN));
  }

  // OutputContract
  if (outputContractResult && outputContractResult !== "none") {
    lines.push(`📌 OutputContract: ${outputContractResult === "pass" ? "✅ 通过" : "❌ 未通过"}`);
  }

  return lines.join("\n");
}

function normalizeDisplayText(value: string): string {
  if (!value.includes("\n") && value.includes("\\n")) {
    return value.trim().replaceAll("\\n", "\n");
  }
  return value;
}

function truncateUtf8(value: string, maxBytes: number): string {
  if (Buffer.byteLength(value, "utf8") <= maxBytes) return value;

  const suffixBytes = Buffer.byteLength(DISPLAY_TRUNCATION_SUFFIX, "utf8");
  const codePoints = Array.from(value);
  let low = 0;
  let high = codePoints.length;
  while (low < high) {
    const middle = Math.ceil((low + high) / 2);
    const bytes = Buffer.byteLength(codePoints.slice(0, middle).join(""), "utf8");
    if (bytes <= maxBytes - suffixBytes) low = middle;
    else high = middle - 1;
  }
  return codePoints.slice(0, low).join("") + DISPLAY_TRUNCATION_SUFFIX;
}

export function splitNodeDisplayResult(
  result: Record<string, unknown> | undefined,
): { displayMarkdown?: string; machineOutput?: Record<string, unknown> } {
  if (!result) return {};

  const displayValue = DISPLAY_OUTPUT_KEYS
    .map((key) => result[key])
    .find((value): value is string => typeof value === "string" && value.trim().length > 0);
  const displayMarkdown = displayValue
    ? truncateUtf8(normalizeDisplayText(displayValue), MAX_DISPLAY_BYTES)
    : undefined;

  const machineOutput: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(result)) {
    if ((DISPLAY_OUTPUT_KEYS as readonly string[]).includes(key)) continue;
    machineOutput[key] = value;
  }
  return {
    ...(displayMarkdown ? { displayMarkdown } : {}),
    ...(Object.keys(machineOutput).length > 0 ? { machineOutput } : {}),
  };
}

export function buildNodeSucceededNotification(params: {
  node: WorkflowNode;
  nodeIndex: number;
  result?: Record<string, unknown>;
  durationMs: number | null;
  outputContractResult?: "pass" | "fail" | "none";
  level: InjectLevel;
}): { message: string; droppable: boolean } {
  const { displayMarkdown, machineOutput } = splitNodeDisplayResult(params.result);
  return {
    message: buildNodeSucceededMessage({
      node: params.node,
      nodeIndex: params.nodeIndex,
      output: summarizeOutput(machineOutput),
      displayMarkdown,
      durationMs: params.durationMs,
      outputContractResult: params.outputContractResult,
      level: params.level,
    }),
    droppable: !displayMarkdown,
  };
}

// ── Node Skipped ──

export interface NodeSkippedParams {
  node: WorkflowNode;
  nodeIndex: number;
  skipReason: string;
  triggerRuleActual?: string;
  level: InjectLevel;
}

export function buildNodeSkippedMessage(params: NodeSkippedParams): string {
  const { node, nodeIndex, skipReason, triggerRuleActual, level } = params;
  // node-skipped minLevel is "full" (inject-level.ts): only full injects it.
  if (level !== "full") return "";

  const lines: string[] = [];
  lines.push(`⏭️ 节点跳过: #${nodeIndex} ${node.id}`);
  lines.push("━━━━━━━━━━━━━━━━━━━━━━━━━");
  lines.push(`原因: ${skipReason}`);
  if (triggerRuleActual) {
    lines.push(`  实际: ${triggerRuleActual}`);
  }

  return lines.join("\n");
}

// ── Node Retry ──

export interface NodeRetryParams {
  node: WorkflowNode;
  nodeIndex: number;
  attempt: number;
  maxAttempts: number;
  lastError: string;
  retrySpec?: NodeRetrySpec;
  level: InjectLevel;
}

export function buildNodeRetryMessage(_params: NodeRetryParams): string {
  // node-retry is minLevel=never — never injected in any level. Retain the
  // function so call sites compile during migration; retry count still shows
  // in the final failure bookend (see buildFlowCompletedMessage / buildNodeFailedMessage).
  return "";
}

// ── Node Failed (Enhanced) ──

export interface NodeFailedParams {
  node: WorkflowNode;
  nodeIndex: number;
  executorType: string;
  error: string;
  resolvedInput?: Record<string, unknown>;
  retryStatus?: { attempt: number; maxAttempts: number };
  downstreamNodes?: string[];
  level: InjectLevel;
}

export function buildNodeFailedMessage(params: NodeFailedParams): string {
  const { node, nodeIndex, executorType, error, resolvedInput, retryStatus, downstreamNodes, level } = params;

  const lines: string[] = [];
  if (level === "perf") {
    // minimal: keep existing format
    lines.push(`⚠️ 节点失败: ${node.title ?? node.id}`);
    lines.push(`错误: ${truncateValue(error, MAX_ERROR_LEN)}`);
    return lines.join("\n");
  }

  lines.push(`❌ 节点失败: #${nodeIndex} ${node.id}`);
  lines.push("━━━━━━━━━━━━━━━━━━━━━━━━━");
  lines.push(`🔧 Executor: ${executorType}`);

  // Input at time of failure
  if (resolvedInput && Object.keys(resolvedInput).length > 0) {
    lines.push("📥 Input:");
    lines.push(...formatRecord(resolvedInput));
  }

  lines.push(`❗ 错误: ${truncateValue(error, MAX_ERROR_LEN)}`);

  // Retry status
  if (retryStatus) {
    const exhausted = retryStatus.attempt >= retryStatus.maxAttempts;
    lines.push(`🔁 重试: ${exhausted ? `已耗尽 (${retryStatus.attempt}/${retryStatus.maxAttempts} 次)` : `${retryStatus.attempt}/${retryStatus.maxAttempts} 次`}`);
  }

  // Downstream impact
  if (downstreamNodes && downstreamNodes.length > 0) {
    lines.push(`📌 依赖此节点的后续节点: ${downstreamNodes.slice(0, 5).join(", ")}${downstreamNodes.length > 5 ? ` ... (+${downstreamNodes.length - 5})` : ""}`);
  }

  return lines.join("\n");
}

// ── Flow Completed (Enhanced) ──

export interface FlowCompletedParams {
  flowId: string;
  workflowTitle: string;
  workflowId: string;
  status: "succeeded" | "failed" | "cancelled";
  currentPhase: string;
  totalDurationMs: number | null;
  nodeStates: Record<string, NodeState>;
  nodes: WorkflowNode[];
  failedNode?: { nodeId: string; error: string; input?: Record<string, unknown>; retryStatus?: { attempt: number; maxAttempts: number } };
  level: InjectLevel;
  /** Resolved workflow outputs values (from state.workflowData.outputs) */
  workflowOutputs?: Record<string, unknown>;
  /** Workflow outputs spec (from WorkflowSpec.outputs) — provides descriptions */
  workflowOutputsSpec?: WorkflowOutputsSpec;
}

/** Format a workflow output value for display in the completion message. */
function formatOutputValue(value: unknown): string {
  if (value == null) return "null";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (typeof value === "object") return JSON.stringify(value, null, 2);
  return String(value);
}

export function buildFlowCompletedMessage(params: FlowCompletedParams): string {
  const { flowId, workflowTitle, status, currentPhase, totalDurationMs, nodeStates, nodes, failedNode, level, workflowOutputs, workflowOutputsSpec } = params;

  // Build a clickable clawweb URL for the flow run detail page.
  const flowRunUrl = buildFlowRunUrl(flowId);
  // Markdown link that opens in a new tab (rendered by OpenClaw's chat stream).
  const flowIdLink = `[${flowId}](${flowRunUrl})`;

  if (level === "perf") {
    // minimal: keep existing format but with clickable FlowId
    if (status === "failed") {
      const detail = failedNode ? `${failedNode.nodeId}: ${failedNode.error}` : "Unknown error";
      return `[clawmind] ❌ 工作流失败: ${workflowTitle}\nFlowId: ${flowIdLink}\n阶段: ${currentPhase}\n失败原因: ${truncateValue(detail, 200)}`;
    }
    if (status === "cancelled") {
      return `[clawmind] ⏹️ 工作流已取消: ${workflowTitle}\nFlowId: ${flowIdLink}`;
    }
    return `[clawmind] ✅ 工作流完成: ${workflowTitle}\nFlowId: ${flowIdLink}\n阶段: ${currentPhase}\n耗时: ${totalDurationMs != null ? `${Math.round(totalDurationMs / 1000)}s` : "n/a"}`;
  }

  const lines: string[] = [];
  const isSuccess = status === "succeeded";
  const isFailed = status === "failed";

  if (isSuccess) {
    lines.push(`🏁 工作流完成: ${workflowTitle}`);
  } else if (isFailed) {
    lines.push(`🚨 工作流失败: ${workflowTitle}`);
  } else {
    lines.push(`⏹️ 工作流已取消: ${workflowTitle}`);
  }
  lines.push("━━━━━━━━━━━━━━━━━━━━━━━━━");
  lines.push(`📋 FlowId: ${flowIdLink}`);
  lines.push(`⏱️ 耗时: ${totalDurationMs != null ? `${Math.round(totalDurationMs / 1000)}s` : "n/a"}`);

  // ── Workflow outputs summary ──
  // Display resolved workflow output variables (defined in YAML `outputs:`)
  // before the clawweb link, so users see key results inline without clicking.
  if (workflowOutputs && typeof workflowOutputs === "object" && Object.keys(workflowOutputs).length > 0) {
    lines.push("");
    lines.push("📤 输出变量:");
    for (const [name, value] of Object.entries(workflowOutputs)) {
      const desc = workflowOutputsSpec?.[name]?.description;
      const label = desc ? `${name} (${desc})` : name;
      const formatted = truncateValue(formatOutputValue(value), 2000);
      lines.push(`   ${label}: ${formatted}`);
    }
  }

  // Link to the full run detail page on clawweb for node-by-node execution
  // results, output payloads, and timeline. The completion message stays
  // concise — detailed information is available behind the link.
  lines.push("");
  lines.push(`👉 [查看完整执行详情](${flowRunUrl})`);

  // Failure detail — keep this inline so the user sees the error immediately
  // without needing to click through to the detail page.
  if (isFailed && failedNode) {
    lines.push("");
    lines.push(`❗ 失败节点: ${failedNode.nodeId}`);
    lines.push(`   错误: ${truncateValue(failedNode.error, MAX_ERROR_LEN)}`);
    if (failedNode.retryStatus) {
      lines.push(`   重试: ${failedNode.retryStatus.attempt}/${failedNode.retryStatus.maxAttempts} 次`);
    }
  }

  return lines.join("\n");
}

// ── DAG Topology Helper ──

/** Compute node index map (1-based) from workflow node list order. */
export function buildNodeIndexMap(nodes: WorkflowNode[]): Map<string, number> {
  const map = new Map<string, number>();
  nodes.forEach((n, i) => map.set(n.id, i + 1));
  return map;
}

/** Find downstream nodes that depend on a given node. */
export function findDownstreamNodes(nodes: WorkflowNode[], nodeId: string): string[] {
  return nodes.filter((n) => n.dependsOn?.includes(nodeId)).map((n) => n.id);
}

/** Summarize output for notification (truncate and limit fields). */
export function summarizeOutput(result: Record<string, unknown> | undefined): Record<string, unknown> | undefined {
  if (!result || Object.keys(result).length === 0) return undefined;
  const summary: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(result).slice(0, MAX_OUTPUT_FIELDS)) {
    if (isSensitiveKey(k)) {
      summary[k] = "***";
    } else {
      summary[k] = v;
    }
  }
  return summary;
}

/** Capture node executor input config (raw values, no template resolution). */
export function captureRawInput(node: WorkflowNode): Record<string, unknown> {
  const input: Record<string, unknown> = {};
  const executor = node.executor as Record<string, unknown>;
  // Common fields per executor type
  if (executor.type === "embedded-agent" || executor.type === "subagent") {
    if (executor.prompt) input["prompt"] = (executor.prompt as string).slice(0, MAX_VALUE_LEN);
    if (executor.input) input["input"] = executor.input;
  } else if (executor.type === "cli-script") {
    if (executor.script) input["script"] = (executor.script as string).slice(0, MAX_VALUE_LEN);
  } else if (executor.type === "mcp-call") {
    if (executor.tool) input["tool"] = executor.tool;
    if (executor.args) input["args"] = executor.args;
  } else if (executor.type === "baas-call") {
    if (executor.apiPath) input["apiPath"] = executor.apiPath;
  } else if (executor.type === "bcs-route") {
    if (executor.selector) input["selector"] = executor.selector;
  } else if (executor.type === "bcs-approval-batch") {
    if (executor.approvalCode) input["approvalCode"] = executor.approvalCode;
  } else if (executor.type === "action") {
    if (executor.action) input["action"] = executor.action;
  }
  return input;
}

/** Capture a display-safe, whitelist-only preview of workflow input. */
export function captureWorkflowInputPreview(
  templateCtx: TemplateContext,
): Record<string, unknown> | undefined {
  try {
    const inputDescriptor = Object.getOwnPropertyDescriptor(templateCtx, "input");
    if (!inputDescriptor) return undefined;
    if (!("value" in inputDescriptor)) {
      return { preview: "[预览生成失败]" };
    }

    const rawInput = inputDescriptor.value;
    if (rawInput === null || typeof rawInput !== "object") return undefined;

    const preview = Object.create(null) as Record<string, unknown>;
    const message = readOwnPreviewValue(rawInput, "message");
    if (message !== undefined) preview.message = message;

    const params = readOwnPreviewValue(rawInput, "params");
    preview.params = sanitizeTemplatePreviewValue(
      params ?? Object.create(null),
      0,
      new WeakSet<object>(),
      "input.params",
    );

    const files = summarizeWorkflowInputFiles(
      readOwnPreviewValue(rawInput, "files"),
    );
    if (files) preview.files = files;
    return preview;
  } catch (error) {
    const errorType = typeof error;
    try {
      console.warn(
        "[controller-notifications] workflow input preview failed: errorType="
          + errorType,
      );
    } catch {
      // Preview capture must remain fail-safe even if logging is unavailable.
    }
    return { preview: "[预览生成失败]" };
  }
}

/** Capture display-safe executor input, resolving agent prompt templates. */
export function captureDisplayInput(
  node: WorkflowNode,
  templateCtx: TemplateContext,
): Record<string, unknown> {
  let nodeId = "[unavailable]";
  try {
    const candidateNodeId = node.id;
    if (typeof candidateNodeId === "string") nodeId = candidateNodeId;
  } catch {
    // Keep the fallback identifier for failure logging and prompt preview.
  }

  try {
    const input = captureRawInput(node);
    const executor = node.executor as Record<string, unknown>;
    if (executor.type !== "embedded-agent" && executor.type !== "subagent") {
      return input;
    }

    const rawPrompt = typeof executor.prompt === "string"
      ? executor.prompt
      : nodeId;
    input.prompt = resolveTemplateWithFormatter(
      rawPrompt,
      templateCtx,
      formatTemplatePreviewValue,
      readTemplatePathByDescriptor,
    );
    return input;
  } catch (error) {
    const errorType = typeof error;
    try {
      console.warn(
        "[controller-notifications] prompt preview failed: node="
          + nodeId
          + " errorType="
          + errorType,
      );
    } catch {
      // Preview capture must remain fail-safe even if logging is unavailable.
    }
    return { prompt: "[预览生成失败]" };
  }
}
