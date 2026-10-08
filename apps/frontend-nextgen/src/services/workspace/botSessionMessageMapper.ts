import type { BotMessageDto } from '@/services/backendApi/bots/privateBotSessionController';
import type { Block, ChatMessage, MessageRole, TextBlock, ToolExecutionBlock, ToolStep } from '@tc-chat/core';
import { isToolError, stringifyToolValue } from './messageMapperHelpers';

function toRole(role: BotMessageDto['role']): MessageRole | null {
  if (role === 'user' || role === 'assistant' || role === 'system') return role;
  return null;
}

function parseTimestamp(raw: string): number {
  if (!raw) return NaN;
  return Date.parse(raw);
}

interface ToolMessageMetadata {
  tool_call_id?: unknown;
  toolCallId?: unknown;
  tool_name?: unknown;
  toolName?: unknown;
  arguments?: unknown;
  input?: unknown;
  tool_args?: unknown;
  result?: unknown;
  output?: unknown;
  success?: unknown;
  status?: unknown;
  error?: unknown;
  is_error?: unknown;
  history_meta?: { conversationRoundId?: unknown };
}

function getToolMetadata(message: BotMessageDto): ToolMessageMetadata {
  return (message.metadata ?? {}) as ToolMessageMetadata;
}

function getRoundId(message: BotMessageDto): string | null {
  const candidates = [
    message.run_id,
    message.history_meta?.conversationRoundId,
    getToolMetadata(message).history_meta?.conversationRoundId,
  ];
  for (const candidate of candidates) {
    if (typeof candidate === 'string' && candidate.trim()) return candidate.trim();
  }
  return null;
}

function getToolStep(message: BotMessageDto, index: number): ToolStep | null {
  const metadata = getToolMetadata(message);
  const callId = metadata.tool_call_id ?? metadata.toolCallId ?? message.message_id ?? `tool-${index}`;
  const callIdText = String(callId || '');
  const toolName = String(metadata.tool_name ?? metadata.toolName ?? 'tool');
  if (!callIdText || (toolName === 'tool' && message.role === 'tool_use' && !metadata.arguments && !metadata.input)) {
    return null;
  }
  const result = metadata.result ?? metadata.output ?? (message.role === 'tool_result' ? message.content : undefined);
  return {
    id: callIdText,
    tool: toolName,
    title: toolName,
    status: message.role === 'tool_result' && isToolError(metadata) ? 'error' : 'success',
    input: stringifyToolValue(metadata.arguments ?? metadata.input ?? metadata.tool_args),
    output: stringifyToolValue(result ?? metadata.error),
  };
}

function createAssistantMessage(message: BotMessageDto, index: number, roundId: string | null): ChatMessage {
  const createdAt = message.gmt_create ? Date.parse(message.gmt_create) : undefined;
  return {
    id: message.message_id || `bot-history-${index}-${createdAt ?? 0}`,
    role: 'assistant',
    content: '',
    status: 'history',
    createdAt: Number.isFinite(createdAt) ? createdAt : undefined,
    blocks: [],
    ...(roundId
      ? {
          extra: {
            conversationRoundId: roundId,
            runId: roundId,
          },
        }
      : {}),
  };
}

function assignRoundId(message: ChatMessage, roundId: string): void {
  message.extra = {
    ...(message.extra ?? {}),
    conversationRoundId: roundId,
    runId: roundId,
  };
}

function containsOnlyToolBlocks(message: ChatMessage): boolean {
  return (
    !message.content &&
    Boolean(message.blocks?.length) &&
    message.blocks!.every((block) => block.type === 'tool_execution')
  );
}

function appendText(message: ChatMessage, content: string): void {
  if (!content) return;
  const blocks = (message.blocks ?? []) as Block[];
  const lastBlock = blocks[blocks.length - 1];
  if (lastBlock?.type === 'text') {
    (lastBlock as TextBlock).content += content;
  } else {
    blocks.push({ type: 'text', content } as TextBlock);
  }
  message.blocks = blocks;
  message.content = `${message.content ?? ''}${content}`;
}

function mergeToolStep(existing: ToolStep, incoming: ToolStep): ToolStep {
  const existingTerminal = existing.status === 'success' || existing.status === 'error';
  return {
    ...existing,
    ...incoming,
    tool: existing.tool !== 'tool' ? existing.tool : incoming.tool,
    title: existing.title !== 'tool' ? existing.title : incoming.title,
    input: existing.input ?? incoming.input,
    output: incoming.output ?? existing.output,
    status: existingTerminal && incoming.status === 'success' ? existing.status : incoming.status,
  };
}

function upsertToolStep(message: ChatMessage, incoming: ToolStep): void {
  const blocks = (message.blocks ?? []) as Block[];
  for (const block of blocks) {
    if (block.type !== 'tool_execution') continue;
    const toolBlock = block as ToolExecutionBlock;
    const stepIndex = toolBlock.steps.findIndex((step) => step.id === incoming.id);
    if (stepIndex >= 0) {
      toolBlock.steps[stepIndex] = mergeToolStep(toolBlock.steps[stepIndex], incoming);
      message.blocks = blocks;
      return;
    }
  }

  const lastBlock = blocks[blocks.length - 1];
  if (lastBlock?.type === 'tool_execution') {
    (lastBlock as ToolExecutionBlock).steps.push(incoming);
  } else {
    blocks.push({ type: 'tool_execution', steps: [incoming] } as ToolExecutionBlock);
  }
  message.blocks = blocks;
}

/**
 * BotMessageDto[] → ChatMessage[]。
 *
 * 后端消息先按时间升序恢复为旧→新，再将同一 assistant 轮次的文本与工具消息聚合：
 * - 有 run_id / conversationRoundId 时按轮次聚合；
 * - 无轮次字段时，连续 tool_use / tool_result 仍聚合为一个 tool_execution 消息；
 * - user / system 或无轮次 assistant 文本会结束当前隐式聚合；
 * - 同 tool_call_id 的 tool_use / tool_result 合并为一个 ToolStep。
 */
export function mapBotSessionMessages(items: BotMessageDto[]): ChatMessage[] {
  const indexed = items.map((message, index) => ({ message, index }));
  indexed.sort((a, b) => {
    const aTime = parseTimestamp(a.message.gmt_create);
    const bTime = parseTimestamp(b.message.gmt_create);
    const aValid = Number.isFinite(aTime);
    const bValid = Number.isFinite(bTime);
    if (aValid && bValid) return aTime - bTime;
    if (aValid !== bValid) return aValid ? -1 : 1;
    return a.index - b.index;
  });

  const result: ChatMessage[] = [];
  const toolOwnerByCallId = new Map<string, { message: ChatMessage; roundId: string | null }>();
  let activeAssistant: ChatMessage | null = null;
  let activeRoundId: string | null = null;

  for (const { message, index } of indexed) {
    const isToolMessage = message.role === 'tool_use' || message.role === 'tool_result';
    const roundId = getRoundId(message);

    if (isToolMessage) {
      const step = getToolStep(message, index);
      if (!step) continue;

      const existingOwner = toolOwnerByCallId.get(step.id);
      const belongsToExistingOwner =
        existingOwner && (roundId === null || existingOwner.roundId === null || existingOwner.roundId === roundId);
      if (belongsToExistingOwner) {
        upsertToolStep(existingOwner.message, step);
        continue;
      }

      const canReuseActive =
        activeAssistant !== null &&
        ((roundId !== null && activeRoundId === roundId) ||
          (roundId === null && (activeRoundId !== null || activeAssistant.blocks?.at(-1)?.type === 'tool_execution')));
      let targetMessage: ChatMessage | null = activeAssistant;
      if (!canReuseActive || !targetMessage) {
        targetMessage = createAssistantMessage(message, index, roundId);
        activeAssistant = targetMessage;
        activeRoundId = roundId;
        result.push(targetMessage);
      }
      upsertToolStep(targetMessage, step);
      toolOwnerByCallId.set(step.id, { message: targetMessage, roundId });
      continue;
    }

    const role = toRole(message.role);
    if (!role) continue;
    const content = message.content ?? '';

    if (role === 'user' || role === 'system') {
      activeAssistant = null;
      activeRoundId = null;
      toolOwnerByCallId.clear();
      if (!content) continue;
      const createdAt = message.gmt_create ? Date.parse(message.gmt_create) : undefined;
      result.push({
        id: message.message_id || `bot-history-${index}-${createdAt ?? 0}`,
        role,
        content,
        status: 'history',
        createdAt: Number.isFinite(createdAt) ? createdAt : undefined,
        blocks: [{ type: 'text', content }] as TextBlock[],
      });
      continue;
    }

    if (!content) continue;
    if (roundId) {
      if (activeAssistant && activeRoundId === null && containsOnlyToolBlocks(activeAssistant)) {
        activeRoundId = roundId;
        assignRoundId(activeAssistant, roundId);
      }
      if (!activeAssistant || activeRoundId !== roundId) {
        activeAssistant = createAssistantMessage(message, index, roundId);
        activeRoundId = roundId;
        result.push(activeAssistant);
      }
      appendText(activeAssistant, content);
      continue;
    }

    const createdAt = message.gmt_create ? Date.parse(message.gmt_create) : undefined;
    result.push({
      id: message.message_id || `bot-history-${index}-${createdAt ?? 0}`,
      role: 'assistant',
      content,
      status: 'history',
      createdAt: Number.isFinite(createdAt) ? createdAt : undefined,
      blocks: [{ type: 'text', content }] as TextBlock[],
    });
    activeAssistant = null;
    activeRoundId = null;
  }

  return result;
}
