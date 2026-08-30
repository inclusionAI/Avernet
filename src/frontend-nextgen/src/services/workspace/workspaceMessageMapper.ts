import type { PrivateSessionRawMessage } from '@/services/backendApi/privateChat/privateSessionController';
import type { Block, ChatMessage, MessageRole, TextBlock, ToolExecutionBlock, ToolStep } from '@tc-chat/core';
import {
  extractMessageContent,
  isToolError,
  normalizeTimestamp,
  readConversationRoundId,
  stringifyToolValue,
  type ToolResultMeta,
} from './messageMapperHelpers';

function toTimestamp(message: PrivateSessionRawMessage): number | undefined {
  const raw = message.gmt_created ?? message.created_at ?? message.createdAt ?? message.timestamp;
  return normalizeTimestamp(raw);
}

function normalizeRole(role?: string): MessageRole | null {
  if (role === 'user' || role === 'assistant' || role === 'system') return role;
  if (role === 'tool_result' || role === 'tool_use' || role === 'thinking') return 'assistant';
  return null;
}

function buildHistoryBlocks(group: PrivateSessionRawMessage[]): Block[] {
  const blocks: Block[] = [];
  let currentToolBlock: ToolExecutionBlock | null = null;
  let currentTextBlock: { type: 'text'; content: string } | null = null;
  const seenToolIds = new Set<string>();

  group.forEach((message, index) => {
    const metadata = (message.metadata ?? {}) as ToolResultMeta & {
      tool_name?: unknown;
      tool_call_id?: unknown;
      arguments?: unknown;
      result?: unknown;
      success?: unknown;
      status?: unknown;
      error?: unknown;
    };
    if (message.role === 'tool_result' && typeof metadata.tool_name === 'string') {
      const toolCallId = typeof metadata.tool_call_id === 'string' ? metadata.tool_call_id : `history-tool-${index}`;
      if (seenToolIds.has(toolCallId)) return;
      seenToolIds.add(toolCallId);
      currentTextBlock = null;

      const result = metadata.result;
      let parsedResult: unknown = result;
      if (typeof result === 'string') {
        try {
          parsedResult = JSON.parse(result);
        } catch {
          parsedResult = result;
        }
      }
      const isError = isToolError({ ...metadata, result: parsedResult });
      const step: ToolStep = {
        id: toolCallId,
        tool: metadata.tool_name as string,
        title: metadata.tool_name as string,
        status: isError ? 'error' : 'success',
        input: stringifyToolValue(metadata.arguments),
        output: stringifyToolValue(result ?? metadata.error),
      };
      if (!currentToolBlock) {
        currentToolBlock = { type: 'tool_execution', steps: [step] };
        blocks.push(currentToolBlock);
      } else {
        currentToolBlock.steps.push(step);
      }
      return;
    }

    const content = extractMessageContent(message.content);
    if (!content) return;
    currentToolBlock = null;
    if (!currentTextBlock) {
      currentTextBlock = { type: 'text', content };
      blocks.push(currentTextBlock);
    } else {
      currentTextBlock.content += content;
    }
  });
  return blocks;
}

function getHistoryGroupKey(message: PrivateSessionRawMessage): string | undefined {
  // PrivateSessionRawMessage 顶层可能挂 history_meta（与 demo §8.4 字段路径一致）。
  const meta = (message as unknown as { history_meta?: { conversationRoundId?: string } }).history_meta;
  return readConversationRoundId(meta ? { history_meta: meta } : undefined);
}

/** 与 open-claw 的 transformMessagesToChatMessages 保持一致：聚合 assistant/tool_result 并构建 tool_execution blocks。 */
export function mapPrivateHistoryMessages(rawMessages: PrivateSessionRawMessage[]): ChatMessage[] {
  const messages = rawMessages.filter((message) => {
    const content = extractMessageContent(message.content);
    return Boolean(content || message.blocks?.length || Object.keys(message.metadata ?? {}).length);
  });
  const result: ChatMessage[] = [];
  let index = 0;

  while (index < messages.length) {
    const message = messages[index];
    const role = normalizeRole(message.role);
    if (!role) {
      index += 1;
      continue;
    }
    if (role === 'user' || role === 'system') {
      const content = extractMessageContent(message.content);
      const createdAt = toTimestamp(message);
      result.push({
        id: message.id || `history-${index}-${createdAt || 0}`,
        role,
        content,
        status: 'history',
        createdAt,
        blocks: message.blocks?.length
          ? (message.blocks as unknown as Block[])
          : ([{ type: 'text', content }] as TextBlock[]),
      });
      index += 1;
      continue;
    }

    const groupKey = getHistoryGroupKey(message);
    if (message.role === 'assistant' && !groupKey) {
      const content = extractMessageContent(message.content);
      const createdAt = toTimestamp(message);
      result.push({
        id: message.id || `history-${index}-${createdAt || 0}`,
        role: 'assistant',
        content,
        status: 'history',
        createdAt,
        blocks: message.blocks?.length
          ? (message.blocks as unknown as Block[])
          : content
          ? ([{ type: 'text', content }] as TextBlock[])
          : undefined,
      });
      index += 1;
      continue;
    }

    const group: PrivateSessionRawMessage[] = [];
    while (index < messages.length) {
      const next = messages[index];
      if (next.role === 'user' || next.role === 'system') break;
      const nextGroupKey = getHistoryGroupKey(next);
      if (next.role === 'assistant' && !nextGroupKey) break;
      if (groupKey && nextGroupKey && nextGroupKey !== groupKey) break;
      group.push(next);
      index += 1;
    }
    if (!group.length) continue;

    const first = group[0];
    const content = group
      .filter((item) => item.role !== 'tool_result')
      .map((item) => extractMessageContent(item.content))
      .join('');
    const createdAt = toTimestamp(first);
    result.push({
      id: first.id || `history-${index}-${createdAt || 0}`,
      role: 'assistant',
      content,
      status: 'history',
      createdAt,
      blocks: first.blocks?.length ? (first.blocks as unknown as Block[]) : buildHistoryBlocks(group),
    });
  }

  return result;
}
