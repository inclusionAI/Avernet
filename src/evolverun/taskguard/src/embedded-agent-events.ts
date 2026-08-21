import type { EmbeddedAgentLoopEvent } from "./executors/embedded-agent.js";

export type EmbeddedAgentLogLevel = "compact" | "verbose";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function truncate(value: string, max = 500): string {
  return value.length > max ? `${value.slice(0, max)}...` : value;
}

function truncateFinalOutput(value: string, max = 8_000): string {
  return value.length > max ? `${value.slice(0, max)}...` : value;
}

function previewValue(value: unknown, max = 500): string {
  if (typeof value === "string") return truncate(value.trim(), max);
  if (value === undefined || value === null) return "";
  try {
    return truncate(JSON.stringify(value), max);
  } catch {
    return truncate(String(value), max);
  }
}

function stringField(data: Record<string, unknown>, key: string): string | undefined {
  const value = data[key];
  return typeof value === "string" && value.trim() ? value.trim() : undefined;
}

function eventPhase(data: Record<string, unknown>): string | undefined {
  const phase = data.phase;
  return typeof phase === "string" ? phase : undefined;
}

function booleanField(data: Record<string, unknown>, key: string): boolean | undefined {
  const value = data[key];
  return typeof value === "boolean" ? value : undefined;
}

function trimPrefix(value: string, prefix: string): string {
  return value.startsWith(prefix) ? value.slice(prefix.length).trim() : value;
}

function summarizeTool(data: Record<string, unknown>): string {
  const name = stringField(data, "name") ?? stringField(data, "kind") ?? "tool";
  const command = stringField(data, "command");
  const meta = stringField(data, "meta");
  const title = stringField(data, "title");
  const detail = command ?? meta ?? title;
  if (!detail) return name;

  const normalized = trimPrefix(trimPrefix(detail, `command ${name}`), name);
  if (!normalized || normalized === name) return name;
  return truncate(`${name} ${normalized}`, 180);
}

function parseJsonObject(text: string): Record<string, unknown> | undefined {
  const candidate = text.trim().replace(/^```json\s*/i, "").replace(/```\s*$/i, "").trim();
  if (!candidate.startsWith("{") || !candidate.endsWith("}")) return undefined;
  try {
    const parsed = JSON.parse(candidate);
    return isRecord(parsed) ? parsed : undefined;
  } catch {
    return undefined;
  }
}

function summarizeJsonOutput(parsed: Record<string, unknown>): string {
  const parts: string[] = ["已收到结构化结果"];
  const passed = booleanField(parsed, "passed");
  const needsHuman = booleanField(parsed, "needsHuman");
  const reason = stringField(parsed, "reason") ?? stringField(parsed, "message") ?? stringField(parsed, "summary");

  if (passed !== undefined) parts.push(`passed=${passed}`);
  if (needsHuman !== undefined) parts.push(`needsHuman=${needsHuman}`);
  if (reason) parts.push(`reason=${truncate(reason, 160)}`);
  if (parts.length === 1) {
    const keys = Object.keys(parsed).slice(0, 8);
    if (keys.length > 0) {
      parts.push(`字段=${keys.join(", ")}`);
    }
  }

  return parts.join("，");
}

const displayFieldNames = ["displayMarkdown", "planMarkdown", "requirementMarkdown", "reportMarkdown", "displayText", "_suppressJsonDump"];

function displayTextFromJson(parsed: Record<string, unknown>): string | undefined {
  for (const field of displayFieldNames) {
    const value = parsed[field];
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return undefined;
}

function structuredJsonWithoutDisplayFields(parsed: Record<string, unknown>): Record<string, unknown> {
  const structured = { ...parsed };
  for (const field of displayFieldNames) delete structured[field];
  return structured;
}

function formatJsonOutput(parsed: Record<string, unknown>): string {
  const displayText = displayTextFromJson(parsed) ?? summarizeJsonOutput(parsed);
  const structuredJson = structuredJsonWithoutDisplayFields(parsed);
  // 如果输出中包含 _suppressJsonDump=true，则只展示可读摘要，不 dump JSON code block
  if (parsed["_suppressJsonDump"] === true) {
    return displayText;
  }
  // 如果存在 displayMarkdown/displayText 等展示字段，且剩余结构化数据为空对象，则只展示可读摘要，不再 dump 空 JSON
  if (displayTextFromJson(parsed) !== undefined && Object.keys(structuredJson).length === 0) {
    return displayText;
  }
  return `${displayText}\n\n最终结果：\n\`\`\`json\n${JSON.stringify(structuredJson, null, 2)}\n\`\`\``;
}

export function resolveEmbeddedAgentLogLevel(env = process.env.WORKFLOW_ENGINE_EMBEDDED_LOG_LEVEL): EmbeddedAgentLogLevel {
  return env === "verbose" ? "verbose" : "compact";
}

export function shouldRecordEmbeddedAgentLoopEvent(
  event: EmbeddedAgentLoopEvent,
  level: EmbeddedAgentLogLevel = resolveEmbeddedAgentLogLevel(),
): boolean {
  if (level === "verbose") return true;
  if (event.event === "reasoning_stream") return false;
  if (event.event === "tool_result") return true;
  if (event.event === "started" || event.event === "assistant_started" || event.event === "error") {
    return true;
  }
  if (event.event !== "agent_event") return false;

  const stream = event.stream ?? "";
  const data = isRecord(event.data) ? event.data : {};
  const phase = eventPhase(data);

  if (stream === "approval") return phase === undefined || phase === "requested";
  if (stream === "command_output") return phase === "end";
  if (stream === "tool" || stream === "command") return phase === "end";
  if (stream === "lifecycle") return phase === "end" || phase === "error";
  if (stream === "error") return true;
  return false;
}

export function formatEmbeddedAgentLoopProgress(
  nodeTitle: string,
  event: EmbeddedAgentLoopEvent,
): string | undefined {
  const stream = event.stream ?? "unknown";
  const data = isRecord(event.data) ? event.data : {};

  if (event.event !== "agent_event") {
    if (event.event === "assistant_started") return `${nodeTitle} 模型已开始分析`;
    if (event.event === "error") {
      const error = previewValue(data.error ?? data.message ?? event.message);
      return error ? `${nodeTitle} 错误：${error}` : `${nodeTitle} 错误`;
    }
    return undefined;
  }

  if (stream === "lifecycle") {
    const phase = stringField(data, "phase");
    if (phase === "error") {
      const error = previewValue(data.error ?? data.message);
      return error ? `${nodeTitle} agent loop 错误：${error}` : `${nodeTitle} agent loop 错误`;
    }
    return undefined;
  }

  if (stream === "tool") {
    const phase = stringField(data, "phase") ?? "event";
    const summary = summarizeTool(data);
    const failed = data.isError === true;
    const error = previewValue(data.error ?? data.message);
    if (phase === "result" || phase === "end") {
      if (failed || error) {
        return error ? `${nodeTitle} 工具调用失败：${summary}：${error}` : `${nodeTitle} 工具调用失败：${summary}`;
      }
      return `${nodeTitle} 工具调用完成：${summary}`;
    }
    return undefined;
  }

  if (stream === "approval") {
    const phase = stringField(data, "phase");
    if (phase && phase !== "requested") return undefined;
    const summary = summarizeTool(data);
    const approvalSlug = stringField(data, "approvalSlug");
    return approvalSlug
      ? `${nodeTitle} 等待审批：${summary}，审批码 ${approvalSlug}`
      : `${nodeTitle} 等待审批：${summary}`;
  }

  if (stream === "error") {
    const error = previewValue(data.error ?? data.message ?? data);
    return error ? `${nodeTitle} agent loop 错误：${error}` : `${nodeTitle} agent loop 错误`;
  }

  return undefined;
}

export function formatEmbeddedAgentFinalOutput(
  nodeTitle: string,
  output: string,
): string | undefined {
  const text = output.trim();
  if (!text) return undefined;

  const json = parseJsonObject(text);
  const display = json ? formatJsonOutput(json) : truncateFinalOutput(text);
  return `${nodeTitle} 模型输出：\n${display}`;
}
