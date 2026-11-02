import type { ParticipantView } from '@/domain/collaboration';
import type { ChatMessage, ToolExecutionBlock } from '@tc-chat/core';
import type { GroupTask } from './projection';

function object(value: unknown): Record<string, unknown> | undefined {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : undefined;
}

/** Extract the marked JSON object from a shell tool's diagnostic wrapper. */
function markedReceipt(text: string): unknown {
  const marker = text.indexOf('"__bcs_coordination__"');
  if (marker < 0) return undefined;
  const start = text.lastIndexOf('{', marker);
  if (start < 0) return undefined;
  let depth = 0;
  let quoted = false;
  let escaped = false;
  for (let index = start; index < text.length; index++) {
    const char = text[index];
    if (quoted) {
      if (escaped) escaped = false;
      else if (char === '\\') escaped = true;
      else if (char === '"') quoted = false;
    } else if (char === '"') quoted = true;
    else if (char === '{') depth++;
    else if (char === '}' && --depth === 0) {
      try {
        return JSON.parse(text.slice(start, index + 1));
      } catch {
        return undefined;
      }
    }
  }
  return undefined;
}

/** Only inspect real tool outputs, including MCP text and shell stdout wrappers. */
function receipts(value: unknown, depth = 0): Record<string, unknown>[] {
  if (depth > 5) return [];
  if (typeof value === 'string') {
    const text = value.trim().replace(/^```(?:json)?\s*\n([\s\S]*?)\n```$/, '$1');
    try {
      return receipts(JSON.parse(text), depth + 1);
    } catch {
      const marked = markedReceipt(text);
      if (marked) return receipts(marked, depth + 1);
      // mcporter stdout may contain a one-line receipt alongside command diagnostics.
      return text.split('\n').flatMap((line) => {
        try {
          return receipts(JSON.parse(line), depth + 1);
        } catch {
          return [];
        }
      });
    }
  }
  if (Array.isArray(value)) return value.flatMap((item) => receipts(item, depth + 1));
  const record = object(value);
  if (!record) return [];
  if (record.__bcs_coordination__ === true && record.v === 2 && record.tool === 'bcs_assign_task') return [record];
  return ['stdout', 'output', 'result', 'content', 'text'].flatMap((key) => receipts(record[key], depth + 1));
}

function assignmentInput(raw: string | undefined): { assignment: boolean; target?: string; summary?: string } {
  if (!raw) return { assignment: false };
  let value: unknown;
  try {
    value = JSON.parse(raw);
  } catch {
    value = raw;
  }
  const input = object(value);
  if (input && typeof input.target_bot === 'string')
    return {
      assignment: true,
      target: input.target_bot,
      summary: typeof input.message === 'string' ? input.message : undefined,
    };
  const command =
    typeof value === 'string'
      ? value
      : typeof input?.command === 'string'
      ? input.command
      : typeof input?.cmd === 'string'
      ? input.cmd
      : undefined;
  if (!command || !/\bmcporter\s+call\s+\S*\bbcs_assign_task\b/.test(command)) return { assignment: false };
  const jsonArgs = command.match(/(?:^|\s)--args\s+('([^']*)'|"((?:\\.|[^"\\])*)")/);
  if (jsonArgs) {
    try {
      const decoded = jsonArgs[2] !== undefined ? jsonArgs[2] : JSON.parse(jsonArgs[1]);
      const args = object(JSON.parse(decoded));
      if (args)
        return {
          assignment: true,
          target: typeof args.target_bot === 'string' ? args.target_bot : undefined,
          summary: typeof args.message === 'string' ? args.message : undefined,
        };
    } catch {
      /* Keep the authenticated receipt even when shell arguments cannot be decoded. */
    }
  }
  const args: Record<string, string> = {};
  const fields = /(?:^|\s)(target_bot|message)=("(?:\\.|[^"\\])*"|'[^']*'|[^\s]+)/g;
  for (const match of command.matchAll(fields)) {
    const field = match[2];
    if (field.startsWith('"')) {
      try {
        args[match[1]] = JSON.parse(field);
      } catch {
        args[match[1]] = field.slice(1, -1).replace(/\\(.)/g, '$1');
      }
    } else args[match[1]] = field.startsWith("'") ? field.slice(1, -1) : field;
  }
  return { assignment: true, target: args.target_bot, summary: args.message };
}

/** Legacy stored acknowledgements establish identity, never delivery or completion. */
export function projectTaskReceipts(messages: ChatMessage[], participants: ParticipantView[]): GroupTask[] {
  const tasks = new Map<string, GroupTask>();
  for (const message of messages) {
    if (message.role !== 'assistant') continue;
    for (const block of message.blocks ?? []) {
      if (block.type !== 'tool_execution') continue;
      for (const step of (block as ToolExecutionBlock).steps) {
        const input = assignmentInput(step.input);
        if (!input.assignment && !/(?:^|[./_])bcs_assign_task$/.test(step.tool)) continue;
        for (const receipt of receipts(step.output)) {
          const intentId = typeof receipt.intent_id === 'string' && /^bcs_intent_[a-f0-9]{32}$/i.test(receipt.intent_id)
            ? receipt.intent_id.toLowerCase() : undefined;
          const ref = intentId
            ? `T-${intentId.slice('bcs_intent_'.length, 'bcs_intent_'.length + 8)}`
            : typeof receipt.task_ref === 'string' && /^T-[a-f0-9]{8}$/i.test(receipt.task_ref)
            ? receipt.task_ref.toLowerCase().replace(/^t-/, 'T-')
            : undefined;
          const identity = intentId ?? `receipt:${message.id}:${ref}`;
          if (!ref || tasks.has(identity)) continue;
          const worker = participants.find(
            (p) => p.kind === 'bot' && (p.actorId === input.target || p.name === input.target),
          );
          tasks.set(identity, {
            taskRef: ref,
            intentId,
            source: 'receipt',
            summary: input.summary || '已提交的协作任务',
            workerId: worker?.actorId,
            workerName: worker?.name || input.target || '待确认',
            status: 'unknown',
            queuedAt: message.createdAt,
            lastActivityAt: message.createdAt ?? 0,
            sourceMessageId: message.id,
            version: 0,
            statusNote: '已收到派发回执，等待服务端确认任务状态。',
          });
        }
      }
    }
  }
  return [...tasks.values()];
}
