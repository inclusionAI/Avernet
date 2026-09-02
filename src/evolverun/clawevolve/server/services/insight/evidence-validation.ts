import type {
  CompletionState,
  EvidenceMessage,
  EvidenceTask,
  SessionEvidence,
} from "./contracts.js";
import { EVIDENCE_SCHEMA_VERSION } from "./contracts.js";

function invalid(path: string, reason: string): never {
  throw new Error(`${path} ${reason}`);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function recordAt(value: unknown, path: string): Record<string, unknown> {
  if (!isRecord(value)) invalid(path, "必须是对象");
  return value;
}

function arrayAt(value: unknown, path: string): unknown[] {
  if (!Array.isArray(value)) invalid(path, "必须是数组");
  return value;
}

function stringAt(value: unknown, path: string, allowEmpty = false): string {
  if (typeof value !== "string" || (!allowEmpty && value.trim().length === 0)) {
    invalid(path, allowEmpty ? "必须是字符串" : "必须是非空字符串");
  }
  return value;
}

function integerAt(value: unknown, path: string): number {
  if (!Number.isInteger(value) || Number(value) < 0) invalid(path, "必须是非负整数");
  return Number(value);
}

function hasOwn(record: Record<string, unknown>, key: string): boolean {
  return Object.prototype.hasOwnProperty.call(record, key);
}

function validateCompactDate(value: unknown, path: string): string {
  const compact = stringAt(value, path);
  if (!/^\d{8}$/.test(compact)) invalid(path, "必须是 yyyyMMdd");
  const year = Number(compact.slice(0, 4));
  const month = Number(compact.slice(4, 6));
  const day = Number(compact.slice(6, 8));
  const date = new Date(Date.UTC(year, month - 1, day));
  if (
    date.getUTCFullYear() !== year
    || date.getUTCMonth() + 1 !== month
    || date.getUTCDate() !== day
  ) {
    invalid(path, "不是有效日期");
  }
  return compact;
}

function validateMessage(value: unknown, arrayIndex: number): EvidenceMessage {
  const path = `messages[${arrayIndex}]`;
  const message = recordAt(value, path);
  const messageIndex = integerAt(message.message_index, `${path}.message_index`);
  if (messageIndex !== arrayIndex) {
    invalid(`${path}.message_index`, `必须连续且等于数组位置 ${arrayIndex}`);
  }
  const role = stringAt(message.role, `${path}.role`);
  if (!hasOwn(message, "timestamp")) invalid(`${path}.timestamp`, "为必填字段");
  const timestamp = message.timestamp;
  if (
    timestamp !== null
    && typeof timestamp !== "string"
    && !(typeof timestamp === "number" && Number.isFinite(timestamp))
  ) {
    invalid(`${path}.timestamp`, "必须是字符串、有限数字或 null");
  }
  if (message.visibility !== "visible" && message.visibility !== "internal") {
    invalid(`${path}.visibility`, "只能是 visible 或 internal");
  }
  if (!hasOwn(message, "content")) invalid(`${path}.content`, "为必填字段");
  const raw = recordAt(message.raw, `${path}.raw`);
  return {
    ...message,
    message_index: messageIndex,
    role,
    timestamp,
    visibility: message.visibility,
    content: message.content,
    raw,
  };
}

function validateCompletionState(value: unknown, path: string): CompletionState {
  if (value !== 0 && value !== 1 && value !== 2 && value !== 3) {
    invalid(path, "只能是 0、1、2、3");
  }
  return value;
}

function validateTask(value: unknown, arrayIndex: number, messageCount: number): EvidenceTask {
  const path = `tasks[${arrayIndex}]`;
  const task = recordAt(value, path);
  const taskIndex = integerAt(task.task_index, `${path}.task_index`);
  const taskDescription = stringAt(task.task_description, `${path}.task_description`, true);
  const range = arrayAt(task.message_range, `${path}.message_range`);
  if (range.length !== 2) invalid(`${path}.message_range`, "必须恰好包含 start 和 end");
  const start = integerAt(range[0], `${path}.message_range[0]`);
  const end = integerAt(range[1], `${path}.message_range[1]`);
  if (end < start) invalid(`${path}.message_range`, "必须满足 end >= start");
  if (end > messageCount) {
    invalid(`${path}.message_range[1]`, `不能超过 messages.length (${messageCount})`);
  }
  const isComplete = validateCompletionState(task.is_complete, `${path}.is_complete`);
  if (task.reasoning !== undefined && typeof task.reasoning !== "string") {
    invalid(`${path}.reasoning`, "必须是字符串");
  }
  if (task.task_failure_class !== undefined && typeof task.task_failure_class !== "string") {
    invalid(`${path}.task_failure_class`, "必须是字符串");
  }
  return {
    ...task,
    task_index: taskIndex,
    task_description: taskDescription,
    message_range: [start, end],
    is_complete: isComplete,
  };
}

/**
 * Evidence 是离线产出、在线消费的版本化契约。这里做 fail-closed 校验，
 * 避免格式损坏或字段漂移的大 Payload 进入详情展示与改进项证据冻结链路。
 */
export function validateSessionEvidence(value: unknown): SessionEvidence {
  const evidence = recordAt(value, "Evidence");
  const schemaVersion = stringAt(evidence.schema_version, "schema_version");
  if (schemaVersion !== EVIDENCE_SCHEMA_VERSION) {
    invalid("schema_version", `不支持: ${schemaVersion}`);
  }

  stringAt(evidence.batch_id, "batch_id");
  validateCompactDate(evidence.dt, "dt");
  stringAt(evidence.user_id, "user_id");
  stringAt(evidence.bot_id, "bot_id");
  stringAt(evidence.session_id, "session_id");
  recordAt(evidence.session, "session");
  recordAt(evidence.judge_meta, "judge_meta");
  stringAt(evidence.generated_at, "generated_at");

  const messages = arrayAt(evidence.messages, "messages").map(validateMessage);
  const taskIndices = new Set<number>();
  const tasks = arrayAt(evidence.tasks, "tasks").map((task, index) => {
    const validated = validateTask(task, index, messages.length);
    if (taskIndices.has(validated.task_index)) {
      invalid(`tasks[${index}].task_index`, `重复: ${validated.task_index}`);
    }
    taskIndices.add(validated.task_index);
    return validated;
  });

  return {
    ...evidence,
    schema_version: EVIDENCE_SCHEMA_VERSION,
    batch_id: evidence.batch_id as string,
    dt: evidence.dt as string,
    user_id: evidence.user_id as string,
    bot_id: evidence.bot_id as string,
    session_id: evidence.session_id as string,
    session: evidence.session as Record<string, unknown>,
    messages,
    tasks,
    judge_meta: evidence.judge_meta as Record<string, unknown>,
    generated_at: evidence.generated_at as string,
  };
}
