import type {
  EvidenceMessage,
  EvidenceTask,
  TimelineBlockDetail,
  TimelineBlockSummary,
} from "./contracts.js";

const PREVIEW_LENGTH = 240;

function findJsonObjectEnd(value: string, start: number): number {
  let depth = 0;
  let quoted = false;
  let escaped = false;
  for (let index = start; index < value.length; index += 1) {
    const char = value[index];
    if (quoted) {
      if (escaped) escaped = false;
      else if (char === "\\") escaped = true;
      else if (char === '"') quoted = false;
      continue;
    }
    if (char === '"') quoted = true;
    else if (char === "{") depth += 1;
    else if (char === "}") {
      depth -= 1;
      if (depth === 0) return index + 1;
    }
  }
  return -1;
}

export function stripLeadingSenderMetadata(text: string): string {
  const normalized = text.replaceAll("\r\n", "\n").replaceAll("\r", "\n");
  const prefix = /^\s*Sender\s+\(untrusted metadata\):\s*/i.exec(normalized);
  if (!prefix) return text;
  let offset = prefix[0].length;
  const fence = /^```(?:json)?\s*\n?/i.exec(normalized.slice(offset));
  if (fence) offset += fence[0].length;
  while (/\s/.test(normalized[offset] ?? "")) offset += 1;
  if (normalized[offset] !== "{") return text;
  const end = findJsonObjectEnd(normalized, offset);
  if (end < 0) return text;
  try {
    JSON.parse(normalized.slice(offset, end));
  } catch {
    return text;
  }
  offset = end;
  const closingFence = /^\s*```/.exec(normalized.slice(offset));
  if (closingFence) offset += closingFence[0].length;
  offset += /^\s*/.exec(normalized.slice(offset))?.[0].length ?? 0;
  const timestamp = /^\[[^\n]{1,120}\]\s*/.exec(normalized.slice(offset));
  if (timestamp) offset += timestamp[0].length;
  return normalized.slice(offset).replace(/^\n+/, "");
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function textOf(value: unknown): string {
  if (typeof value === "string") return value;
  try { return JSON.stringify(value, null, 2); } catch { return String(value ?? ""); }
}

function summarize(text: string): string {
  const normalized = text
    .replaceAll("\r\n", "\n")
    .replaceAll("\r", "\n")
    .split("\n")
    .map((line) => line.replace(/[ \t]+/g, " ").trim())
    .filter(Boolean)
    .slice(0, 8)
    .join("\n");
  return normalized.length > PREVIEW_LENGTH
    ? `${normalized.slice(0, PREVIEW_LENGTH)}…`
    : normalized;
}

function contentPreviewText(value: unknown): string {
  if (typeof value === "string") return value;
  if (Array.isArray(value)) {
    return value.map((item) => {
      if (!isRecord(item)) return textOf(item);
      if (typeof item.text === "string") return item.text;
      const name = typeof item.name === "string" ? item.name : null;
      if (name) {
        const args = item.arguments ?? item.partialArgs ?? item;
        return `调用 ${name}: ${textOf(args)}`;
      }
      return textOf(item);
    }).filter(Boolean).join("\n");
  }
  if (isRecord(value)) {
    for (const key of ["text", "content", "message", "result"]) {
      if (typeof value[key] === "string") return value[key];
    }
  }
  return textOf(value);
}

function kindOf(message: EvidenceMessage): TimelineBlockSummary["kind"] {
  const role = message.role.toLowerCase();
  if (role === "user") return "user_message";
  if (role === "assistant") return "assistant_message";
  return "agent_execution";
}

function titleOf(message: EvidenceMessage): string {
  const role = message.role.toLowerCase();
  if (role === "user") return "用户消息";
  if (role === "assistant") return "Agent 回复";
  if (role === "toolresult" || role === "tool_result" || role === "tool") return "工具执行结果";
  if (role === "system") return "系统指令";
  return `Agent 执行轨迹 · ${message.role}`;
}

function messageDetail(message: EvidenceMessage): TimelineBlockDetail {
  const role = message.role.toLowerCase();
  const content = role === "user" && typeof message.content === "string"
    ? stripLeadingSenderMetadata(message.content)
    : message.content;
  const raw = role === "user" && typeof message.raw.content === "string"
    ? { ...message.raw, content: stripLeadingSenderMetadata(message.raw.content) }
    : message.raw;
  // Assistant messages must stay faithful to the original session payload.
  // Their derived text may flatten tool calls or otherwise change structure,
  // so the timeline always previews and counts the raw JSON instead.
  const assistantRawOnly = role === "assistant";
  const contentText = assistantRawOnly ? textOf(raw) : textOf(content);
  return {
    blockId: `message:${message.message_index}`,
    kind: kindOf(message),
    messageIndex: message.message_index,
    role: message.role,
    timestamp: message.timestamp,
    visibility: message.visibility,
    title: titleOf(message),
    preview: assistantRawOnly ? "查看原始消息字段" : summarize(contentPreviewText(content)),
    charCount: contentText.length,
    expandable: contentText.length > PREVIEW_LENGTH || Object.keys(raw).length > 4,
    content,
    raw,
  };
}

function judgeDetail(task: EvidenceTask): TimelineBlockDetail {
  const reasoning = textOf(task.reasoning ?? "");
  const failureClass = String(task.task_failure_class ?? "UNKNOWN");
  return {
    blockId: `judge:${task.task_index}`,
    kind: "judge_result",
    messageIndex: null,
    role: "llm_judge",
    timestamp: null,
    visibility: "internal",
    title: `LLM Judge · ${failureClass}`,
    preview: summarize(reasoning),
    charCount: reasoning.length,
    expandable: reasoning.length > PREVIEW_LENGTH || Object.keys(task).length > 8,
    content: task,
    raw: null,
  };
}

export function buildTimelineBlocks(
  messages: EvidenceMessage[],
  task: EvidenceTask,
): TimelineBlockDetail[] {
  const [rawStart, rawEnd] = task.message_range;
  const start = Math.max(0, rawStart);
  const end = Math.min(messages.length, Math.max(start, rawEnd));
  const selected = messages.filter((message) =>
    message.message_index >= start && message.message_index < end,
  );
  return [...selected.map(messageDetail), judgeDetail(task)];
}

export function toTimelineSummary(block: TimelineBlockDetail): TimelineBlockSummary {
  return {
    blockId: block.blockId,
    kind: block.kind,
    messageIndex: block.messageIndex,
    role: block.role,
    timestamp: block.timestamp,
    visibility: block.visibility,
    title: block.title,
    preview: block.preview,
    charCount: block.charCount,
    expandable: block.expandable,
  };
}
