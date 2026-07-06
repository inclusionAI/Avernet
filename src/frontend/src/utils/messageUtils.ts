/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 *
 * 消息处理工具函数
 *
 * 将后端返回的原始消息转换为前端 ChatMessage 格式，
 * 支持工具调用等复杂消息的渲染。
 */

import type {
  Block,
  ChatMessage,
  TextBlock,
  ToolExecutionBlock,
  ToolStep,
} from '@aix-chat/core';

/**
 * 原始消息格式（后端返回）
 */
export interface RawMessage {
  id: string;
  role:
    | 'user'
    | 'assistant'
    | 'tool_result'
    | 'tool_use'
    | 'thinking'
    | 'system';
  content?: string;
  metadata?: {
    tool_name?: string;
    tool_call_id?: string;
    result?: any;
    arguments?: any;
    success?: boolean;
    status?: string;
    error?: string;
    /** AICoding: 标识所属 run，用于 chat.resume replay 时和 history message 去重配对 */
    runId?: string;
    [key: string]: any;
  };
  gmt_created?: string;
  timestamp?: number;
  /** 消息块（结构化内容，如工具调用） */
  blocks?: Block[];
  [key: string]: any;
}

/**
 * 消息转换选项
 */
export interface TransformOptions {
  /** 是否反转角色（用于"他人会话"场景） */
  reverseRole?: boolean;
  /** 是否过滤无效消息 */
  filterInvalid?: boolean;
  /** 消息状态 */
  status?: 'local' | 'success' | 'error';
}

/**
 * 过滤无效消息
 * - assistant 消息没有 content 且没有 metadata 视为无效
 * - 消息没有 content 且 metadata 为空视为无效
 */
export function filterInvalidMessages(messages: RawMessage[]): RawMessage[] {
  return messages.filter((msg) => {
    // assistant 消息没有 content 视为无效
    if (msg.role === 'assistant' && !msg.content) return false;
    // 消息没有 content 且 metadata 为空视为无效
    return msg.content || Object.keys(msg.metadata || {}).length > 0;
  });
}

/**
 * 从消息组中提取文本内容
 */
function extractTextContent(group: RawMessage[]): string {
  const textParts: string[] = [];

  for (const item of group) {
    // 跳过 tool_result 消息的文本（工具结果不作为消息文本）
    if (item.role === 'tool_result') continue;
    if (item.content) {
      textParts.push(item.content);
    }
  }

  return textParts.join('');
}

/**
 * 从消息组构建 Block 数组
 * 按原始顺序构建 blocks，保留工具与文字的穿插顺序
 */
function buildMessageBlocks(group: RawMessage[]): Block[] {
  const blocks: Block[] = [];
  let currentToolBlock: ToolExecutionBlock | null = null;
  let currentTextBlock: TextBlock | null = null;
  const seenToolIds = new Set<string>();
  let textContent = '';

  for (const item of group) {
    const metadata = item.metadata || {};

    if (item.role === 'tool_result' && metadata.tool_name) {
      const toolCallId = metadata.tool_call_id || `step-${blocks.length + 1}`;
      if (seenToolIds.has(toolCallId)) continue;
      seenToolIds.add(toolCallId);

      // 文字块切断
      currentTextBlock = null;

      let resultData: any = null;
      if (typeof metadata.result === 'string') {
        try {
          resultData = JSON.parse(metadata.result);
        } catch {
          /* ignore */
        }
      } else if (metadata.result && typeof metadata.result === 'object') {
        resultData = metadata.result;
      }

      const isToolError =
        metadata.success === false ||
        metadata.status === 'error' ||
        !!metadata.error ||
        resultData?.status === 'error' ||
        !!resultData?.error;

      const step: ToolStep = {
        id: toolCallId,
        tool: metadata.tool_name,
        title: metadata.tool_name,
        status: isToolError ? 'error' : 'success',
        input: metadata.arguments
          ? JSON.stringify(metadata.arguments, null, 2)
          : undefined,
        output:
          metadata.result !== null
            ? typeof metadata.result === 'string'
              ? metadata.result
              : JSON.stringify(metadata.result, null, 2)
            : undefined,
      };

      if (!currentToolBlock) {
        currentToolBlock = {
          type: 'tool_execution',
          steps: [step],
        } as ToolExecutionBlock;
        blocks.push(currentToolBlock);
      } else {
        currentToolBlock.steps.push(step);
      }
    } else if (item.content) {
      // 工具块切断
      currentToolBlock = null;
      // eslint-disable-next-line @typescript-eslint/no-unused-vars
      textContent += item.content;

      if (!currentTextBlock) {
        currentTextBlock = {
          type: 'text',
          content: item.content,
        } as TextBlock;
        blocks.push(currentTextBlock);
      } else {
        currentTextBlock.content += item.content;
      }
    }
  }

  return blocks;
}

/**
 * 将原始消息列表转换为 ChatMessage 列表
 *
 * 核心逻辑：
 * 1. 过滤无效消息
 * 2. 合并连续的 assistant/tool_result 消息
 * 3. 构建结构化的 blocks（包括 tool_execution 和 text）
 *
 * @param rawMessages 原始消息列表
 * @param options 转换选项
 * @returns ChatMessage 列表
 */
/**
 * 生成一个不会碰撞的 fallback id
 * 优先使用 crypto.randomUUID（现代浏览器 + 安全上下文），降级用随机字符串。
 * 用于极端情况下服务端 id 缺失时，避免 Date.now() 同毫秒冲突。
 */
function generateFallbackId(prefix = 'msg'): string {
  if (
    typeof crypto !== 'undefined' &&
    typeof crypto.randomUUID === 'function'
  ) {
    return `${prefix}-${crypto.randomUUID()}`;
  }
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

export function transformMessagesToChatMessages(
  rawMessages: RawMessage[],
  options: TransformOptions = {},
): ChatMessage[] {
  const {
    reverseRole = false,
    filterInvalid = true,
    status = 'success',
  } = options;

  // 过滤无效消息
  const messages = filterInvalid
    ? filterInvalidMessages(rawMessages)
    : rawMessages;

  const result: ChatMessage[] = [];
  let i = 0;
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  let groupIndex = 0;

  while (i < messages.length) {
    const msg = messages[i];

    // user 消息直接添加
    if (msg.role === 'user') {
      // 优先使用已有的 blocks，否则创建 text block
      const hasExistingBlocks = msg.blocks && msg.blocks.length > 0;
      result.push({
        // id 优先级：服务端原生 id > conversationRoundId > 随机兜底
        // 不要用 conversationRoundId 覆盖原生 id（同 round 内 user + assistant 共享 roundId，但服务端 id 是唯一的）
        id:
          msg.id ||
          msg?.history_meta?.conversationRoundId ||
          generateFallbackId('user'),
        conversationRoundId: msg?.history_meta?.conversationRoundId,
        role: reverseRole ? 'assistant' : 'user',
        content: msg.content || '',
        status,
        blocks: hasExistingBlocks
          ? msg.blocks
          : [{ type: 'text', content: msg.content || '' }],
        timestamp: msg.gmt_created
          ? new Date(msg.gmt_created).getTime()
          : msg.timestamp || Date.now(),
      });
      i++;
      continue;
    }

    // assistant / tool_result：按 historyMeta 分组聚合
    // - 有 historyMeta 的消息参与聚合（连续的同组消息合并为一条 ChatMessage）
    // - 无 historyMeta 的消息不聚合（例如inject消息），作为独立 ChatMessage 保留
    while (i < messages.length && messages[i].role !== 'user') {
      const current = messages[i];

      // 无 historyMeta 的 assistant 消息不聚合，独立成一条
      if (current.role === 'assistant' && !current.history_meta) {
        result.push({
          id: current.id || generateFallbackId('assistant'),
          role: reverseRole ? 'user' : 'assistant',
          content: current.content || '',
          status,
          blocks:
            current.blocks && current.blocks.length > 0
              ? current.blocks
              : current.content
              ? [{ type: 'text', content: current.content }]
              : undefined,
          timestamp: current.gmt_created
            ? new Date(current.gmt_created).getTime()
            : current.timestamp || Date.now(),
        });
        i++;
        continue;
      }

      // 有 historyMeta 的消息参与聚合：收集连续的可聚合消息
      const group: RawMessage[] = [];
      while (i < messages.length && messages[i].role !== 'user') {
        const next = messages[i];
        // 无 historyMeta 的 assistant 消息中断聚合
        if (next.role === 'assistant' && !next.history_meta) break;
        group.push(next);
        i++;
      }

      if (group.length === 0) continue;

      // 优先使用已有的 blocks，否则从 group 中构建
      const firstMsg = group[0];
      const hasExistingBlocks = firstMsg?.blocks && firstMsg.blocks.length > 0;
      const blocks = hasExistingBlocks
        ? firstMsg.blocks
        : buildMessageBlocks(group);
      const textContent = extractTextContent(group);

      result.push({
        id:
          firstMsg?.id ||
          firstMsg?.history_meta?.conversationRoundId ||
          generateFallbackId('assistant'),
        conversationRoundId: firstMsg?.history_meta?.conversationRoundId,
        role: reverseRole ? 'user' : 'assistant',
        content: textContent,
        status,
        blocks,
        timestamp: firstMsg?.gmt_created
          ? new Date(firstMsg.gmt_created).getTime()
          : firstMsg?.timestamp || Date.now(),
      });
    }
  }

  return result;
}

/**
 * 按 id 去重（保留后一条，覆盖前一条）
 *
 * 用于 setMessages 入口兜底：即使上游存在历史加载 + SDK 流式推送的重叠，
 * 或极端情况下多条消息命中同一 fallback id，最终数组也不会出现重复 key。
 */
export function dedupeById<T extends { id?: string | number | null }>(
  messages: T[],
): T[] {
  const seen = new Map<string, number>();
  const result: T[] = [];

  for (const msg of messages) {
    const id = msg?.id !== null ? String(msg.id) : '';
    if (!id) {
      // 没有 id 的消息直接保留（理论上不该出现，fallback 后都应该有）
      result.push(msg);
      continue;
    }
    const prevIndex = seen.get(id);
    if (prevIndex === undefined) {
      seen.set(id, result.length);
      result.push(msg);
    } else {
      // 覆盖为最新一条（流式更新场景）
      result[prevIndex] = msg;
    }
  }

  return result;
}

/**
 * 获取消息的 blocks（用于渲染）
 * 如果消息没有 blocks，则根据 content 创建一个 text block
 */
export function getMessageBlocks(msg: ChatMessage): Block[] {
  if (msg.blocks && msg.blocks.length > 0) {
    return msg.blocks;
  }
  return [{ type: 'text', content: msg.content || '' }];
}

/**
 * 确保消息列表有 blocks（仅渲染处理，不合并）
 *
 * 用于群聊等场景：消息已经按正确格式从后端返回，不需要合并连续消息，
 * 只需要确保每条消息都有 blocks 以便渲染。
 *
 * @param messages 消息列表
 * @returns 确保 blocks 存在的消息列表
 */
export function ensureMessageBlocks(messages: ChatMessage[]): ChatMessage[] {
  return messages.map((msg) => {
    // 如果已有 blocks，直接返回
    if (msg.blocks && msg.blocks.length > 0) {
      return msg;
    }
    // 否则根据 content 创建 text block
    return {
      ...msg,
      blocks: [{ type: 'text', content: msg.content || '' }],
    };
  });
}
