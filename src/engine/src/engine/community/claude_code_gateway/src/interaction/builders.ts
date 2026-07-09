// Pure builders for InteractionRequestedEvent / InteractionResolvedEvent.
//
// Each builder takes the semantic inputs of a specific kind and produces the
// wire-format event. These are pure functions — no I/O, no ctx dependency.
//
// Wire-format notes:
//   - kind='ask_user'   -> top-level interaction.requested with prompt/questions
//   - kind='exec'       -> top-level interaction.requested with command/cwd flattened
//   - kind='mode_switch' may also be mirrored through the compatibility
//     stream agent.stream='mode_transition' for older clients.

import type {
  AgentContext,
  AgentModeTransitionData,
  InteractionQuestion,
  InteractionRequestedEvent,
  InteractionResolvedEvent,
  InteractionSubject,
  InteractionUiHints,
} from '../types.js';
import type { PendingInteraction, ResolvedInteractionInput } from './types.js';
import { createLogger } from '../debug.js';

const log = createLogger('sdk');

// HITL 审批等待超时（ms）。所有 GATED_TOOLS（AskUserQuestion/ExitPlanMode/Bash/Edit/Write/Read）共享。
// 可通过环境变量 RELAY_INTERACTION_TIMEOUT_MS 配置，默认 5 分钟。
export const DEFAULT_INTERACTION_TIMEOUT_MS = 300_000; // 5min

// 解析 HITL 审批等待超时。非法值（未设置/空串/NaN/非正数）回退默认 5 分钟。
// 导出为纯函数便于单测在不同 env 下直接调用，无需重载模块。
export function resolveInteractionTimeoutMs(): number {
  const raw = process.env.RELAY_INTERACTION_TIMEOUT_MS;
  if (raw === undefined || raw.trim() === '') {
    // 排查日志：未配置，使用默认值
    log.debug('interaction approval timeout: using default', { timeoutMs: DEFAULT_INTERACTION_TIMEOUT_MS });
    return DEFAULT_INTERACTION_TIMEOUT_MS;
  }
  const n = Number(raw);
  if (!Number.isFinite(n) || n <= 0) {
    // 排查日志：非法配置回退默认值
    log.warn('interaction approval timeout: invalid RELAY_INTERACTION_TIMEOUT_MS, fallback to default', {
      raw,
      timeoutMs: DEFAULT_INTERACTION_TIMEOUT_MS,
    });
    return DEFAULT_INTERACTION_TIMEOUT_MS;
  }
  // 排查日志：配置生效
  log.debug('interaction approval timeout: resolved from env', { raw, timeoutMs: n });
  return n;
}

export const EXEC_APPROVAL_TIMEOUT_MS = resolveInteractionTimeoutMs();

export function buildAskUserInteraction(opts: {
  interactionId: string;
  runId: string;
  sessionKey: string;
  toolCallId: string;
  prompt?: string;
  questions?: InteractionQuestion[];
  agentContext?: AgentContext;
}): InteractionRequestedEvent {
  const createdAtMs = Date.now();
  const expiresAtMs = createdAtMs + EXEC_APPROVAL_TIMEOUT_MS;

  return {
    interactionId: opts.interactionId,
    runId: opts.runId,
    sessionKey: opts.sessionKey,
    kind: 'ask_user',
    title: 'Claude needs your input',
    description: 'Please answer the following question(s)',
    prompt: opts.prompt,
    subject: {
      type: 'tool',
      toolName: 'AskUserQuestion',
      toolCallId: opts.toolCallId,
    },
    questions: opts.questions,
    inputSchema: {
      type: opts.questions?.some(q => q.options && q.options.length > 0) ? 'choices' : 'text',
      multiSelect: opts.questions?.some(q => q.multiSelect),
    },
    uiHints: {
      variant: 'question',
      severity: 'info',
    },
    agentContext: opts.agentContext,
    createdAtMs,
    expiresAtMs,
  };
}

/** Build exec-approval event. The frontend ExecApprovalPanel renders `command` + `cwd`
 * directly, so we flatten them onto the event root regardless of underlying tool. */
export function buildExecInteraction(opts: {
  interactionId: string;
  runId: string;
  sessionKey: string;
  tool: {
    id: string;
    name: string;
    input: Record<string, unknown>;
  };
  cwd?: string;
  agentContext?: AgentContext;
}): InteractionRequestedEvent {
  const createdAtMs = Date.now();
  const expiresAtMs = createdAtMs + EXEC_APPROVAL_TIMEOUT_MS;
  const { tool } = opts;

  let subject: InteractionSubject;
  let title: string;
  let description: string;
  let severity: InteractionUiHints['severity'] = 'warning';
  let command = '';

  if (tool.name === 'Bash') {
    command = String((tool.input as { command?: string })?.command ?? '');
    subject = {
      type: 'command',
      toolName: 'Bash',
      toolCallId: tool.id,
      command,
      cwd: opts.cwd,
    };
    title = 'Command approval required';
    description = 'Claude wants to execute a shell command.';
    const highRiskPattern = /\b(rm\s+-rf|dd\s+if=|mkfs\.|>:|curl\s+.*\|\s*sh|wget\s+.*\|\s*sh)\b/;
    if (highRiskPattern.test(command)) {
      severity = 'danger';
    }
  } else if (tool.name === 'Edit' || tool.name === 'Write') {
    const filePath = String((tool.input as { file_path?: string })?.file_path ?? '');
    command = `${tool.name} ${filePath}`;

    if (tool.name === 'Edit') {
      const oldStr = String((tool.input as { old_string?: string })?.old_string ?? '');
      const newStr = String((tool.input as { new_string?: string })?.new_string ?? '');
      subject = {
        type: 'file',
        toolName: 'Edit',
        toolCallId: tool.id,
        filePath,
        old_string: oldStr,
        new_string: newStr,
        operation: 'edit',
        description: oldStr ? `Replace "${oldStr.slice(0, 50)}..."` : 'Edit file content',
      };
      title = 'File edit approval required';
      description = 'Claude wants to modify a file.';
    } else {
      const content = (tool.input as { content?: string })?.content ?? '';
      subject = {
        type: 'file',
        toolName: 'Write',
        toolCallId: tool.id,
        filePath,
        operation: 'create',
        description: content.slice(0, 100) + (content.length > 100 ? '...' : ''),
      };
      title = 'File write approval required';
      description = 'Claude wants to write to a file.';
    }
  } else if (tool.name === 'Read') {
    const filePath = String((tool.input as { file_path?: string })?.file_path ?? '');
    command = `Read ${filePath}`;
    subject = {
      type: 'file',
      toolName: 'Read',
      toolCallId: tool.id,
      filePath,
      operation: 'read',
      description: `Read file: ${filePath}`,
    };
    title = 'File read approval required';
    description = 'Claude wants to read a file.';
    severity = 'info';
  } else {
    command = tool.name;
    subject = {
      type: 'tool',
      toolName: tool.name,
      toolCallId: tool.id,
    };
    title = 'Action approval required';
    description = `Claude wants to use the ${tool.name} tool.`;
  }

  return {
    interactionId: opts.interactionId,
    runId: opts.runId,
    sessionKey: opts.sessionKey,
    kind: 'exec',
    title,
    description,
    subject,
    command,
    cwd: opts.cwd,
    inputSchema: { type: 'none' },
    uiHints: {
      variant: 'warning',
      severity,
    },
    agentContext: opts.agentContext,
    createdAtMs,
    expiresAtMs,
  };
}

/** Build compatibility stream data for a mode-switch request.
 * Used when mirroring `mode_switch` through `agent.stream='mode_transition'`. */
export function buildModeTransitionRequested(opts: {
  transitionId: string;
  fromMode: string;
  toMode: string;
  summary?: string;
}): AgentModeTransitionData {
  const createdAtMs = Date.now();
  return {
    phase: 'requested',
    transitionId: opts.transitionId,
    kind: 'exit_plan_mode',
    fromMode: opts.fromMode,
    toMode: opts.toMode,
    summary: opts.summary,
    createdAtMs,
    expiresAtMs: createdAtMs + EXEC_APPROVAL_TIMEOUT_MS,
  };
}

export function buildModeTransitionResolved(opts: {
  transitionId: string;
  fromMode: string;
  toMode: string;
  decision: 'proceed' | 'stay';
}): AgentModeTransitionData {
  return {
    phase: 'resolved',
    transitionId: opts.transitionId,
    kind: 'exit_plan_mode',
    fromMode: opts.fromMode,
    toMode: opts.toMode,
    decision: opts.decision,
    resolvedAtMs: Date.now(),
  };
}

export function buildInteractionResolvedEvent(
  pending: PendingInteraction,
  params: ResolvedInteractionInput,
): InteractionResolvedEvent {
  return {
    interactionId: pending.interactionId,
    runId: pending.runId,
    sessionKey: pending.sessionKey,
    kind: pending.kind,
    phase: params.phase,
    decision: params.decision,
    answer: params.answer,
    answers: params.answers,
    values: params.values,
    selectedOptions: params.selectedOptions,
    resolvedBy: 'operator',
    resolvedAtMs: Date.now(),
  };
}
