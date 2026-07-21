/**
 * useGroupChat - 单个群聊的消息管理
 *
 * 从全局 provider 缓存获取已初始化的 Provider
 * 使用 SDK 的 useChat 管理群聊消息
 */

import { useUserStore } from '@/stores/userStore';
import {
  ensureMessageBlocks,
  getMessageBlocks as getMessageBlocksUtil,
} from '@/utils/messageUtils';
import { Tracert } from '@/utils/tracert';
import type { GroupChatParseResult } from '@aix-chat/adapters';
import { useChat, type DefaultChatMessage } from '@aix-chat/adapters';
import type { Block, ChatMessage } from '@aix-chat/core';
import type { ConnectionStatus } from '@aix-chat/ui';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { GroupInfo, GroupMessage } from '../types';
import { BCS_SYSTEM_MESSAGE_BOT_UUID } from '../types';
import { isSilentAssistantReply } from '../utils/silentReply';
import {
  getProvider,
  listeners,
  useGroupChatProviders,
  type ProviderInfo,
} from './useGroupChatProviders';

/**
 * 将历史消息转换为 SDK 默认消息格式
 */
function toDefaultMessages(messages: GroupMessage[]): DefaultChatMessage[] {
  return messages.map((msg) => ({
    role: msg.role,
    content: msg.content,
    extra: {
      botUuid: msg.botUuid,
      botName: msg.botName,
      senderName: msg.sender.name,
      senderAvatar: msg.sender.avatar,
      timestamp: msg.timestamp,
    },
  }));
}

const STATE_MACHINE_SENDER = 'bcs_state_machine';
const STATE_MACHINE_BOT_NAME = 'BCS State Machine';

function getStateMachinePayload(message: ChatMessage): Record<string, any> {
  const extra = (message as any).extra || {};
  return extra.payload || extra;
}

function getStateMachineMetadata(
  message: ChatMessage,
): Record<string, any> | undefined {
  const extra = (message as any).extra || {};
  const payload = getStateMachinePayload(message);
  return payload.metadata || extra.metadata || (message as any).metadata;
}

function isStateMachineTaskMessage(message: ChatMessage): boolean {
  const extra = (message as any).extra || {};
  const payload = getStateMachinePayload(message);
  const metadata = getStateMachineMetadata(message);
  const stateMachine = metadata?.state_machine;
  const isStateMachineSender =
    extra.botUuid === STATE_MACHINE_SENDER ||
    payload.sender === STATE_MACHINE_SENDER;
  const isTaskPayload =
    stateMachine?.event === 'task' || payload.role === 'user';

  return isStateMachineSender && isTaskPayload;
}

function getStateMachineTaskId(message: ChatMessage): string | undefined {
  const payload = getStateMachinePayload(message);
  const metadata = getStateMachineMetadata(message);
  const stateMachine = metadata?.state_machine || {};
  const runId = stateMachine.run_id || payload.run_id || payload.runId;
  const nodeId = stateMachine.node_id || payload.node_id || payload.nodeId;
  const attempt = stateMachine.attempt ?? payload.attempt;

  if (!runId || !nodeId || attempt === undefined || attempt === null) {
    return undefined;
  }

  return `${runId}:${nodeId}:${attempt}:0-task`;
}

function normalizeStateMachineTaskMessage(message: ChatMessage) {
  if (!isStateMachineTaskMessage(message)) return;

  const payload = getStateMachinePayload(message);
  const metadata = getStateMachineMetadata(message);
  const stateMachine = metadata?.state_machine;
  const content =
    typeof payload.content === 'string' ? payload.content : message.content;
  const stableId = getStateMachineTaskId(message);

  if (stableId) {
    message.id = stableId;
  }
  message.role = 'user';
  message.status = 'done';
  message.content = content;
  message.blocks = [{ type: 'text', content } as Block];

  const extra = ((message as any).extra ||= {});
  extra.botUuid = STATE_MACHINE_SENDER;
  extra.botName = STATE_MACHINE_BOT_NAME;
  extra.stateMachine = stateMachine || {
    run_id: payload.run_id || payload.runId,
    node_id: payload.node_id || payload.nodeId,
    attempt: payload.attempt,
    event: payload.event,
  };
}

function preserveMessageExtra(
  message: ChatMessage,
  extraMap: Map<string, any>,
) {
  const msgExtra = (message as any).extra;
  if (msgExtra) {
    extraMap.set(message.id, msgExtra);
    return;
  }

  const savedExtra = extraMap.get(message.id);
  if (savedExtra) {
    (message as any).extra = savedExtra;
  }
}

/**
 * 将 GroupMessage 转换为 ChatMessage 格式，按 conversationRoundId 聚合
 *
 * 根据 historyMeta.conversationRoundId 聚合消息：
 * - 相同 conversationRoundId 的消息聚合为一个气泡
 * - 用户消息 + 助手回复 + 工具调用 合并显示
 * - toolResult 消息不作为独立消息显示，工具调用信息通过 blocks 显示
 */
function transformGroupMessagesToChatMessages(
  groupMessages: GroupMessage[],
): ChatMessage[] {
  console.log(
    '[transformGroupMessagesToChatMessages] input:',
    groupMessages.length,
    groupMessages,
  );

  const chatMessages: ChatMessage[] = [];

  // 先抽出系统消息: 不参与 round 聚合,直接 push,后续 timestamp 排序会把它穿插到正确位置
  const systemMsgs = groupMessages.filter((m) => m.role === 'system');
  systemMsgs.forEach((msg) => {
    chatMessages.push({
      id: msg.id,
      role: 'system',
      content: msg.content,
      status: 'success' as const,
      timestamp: msg.timestamp,
      extra: {
        botUuid: BCS_SYSTEM_MESSAGE_BOT_UUID,
        timestamp: msg.timestamp,
      },
    });
  });

  // 剩余消息按 conversationRoundId 分组
  const remainingMessages = groupMessages.filter((m) => m.role !== 'system');
  const roundMap = new Map<string, GroupMessage[]>();
  const noRoundMessages: GroupMessage[] = [];

  remainingMessages.forEach((msg) => {
    if (msg.conversationRoundId) {
      const existing = roundMap.get(msg.conversationRoundId) || [];
      existing.push(msg);
      roundMap.set(msg.conversationRoundId, existing);
    } else {
      noRoundMessages.push(msg);
    }
  });

  console.log(
    '[transformGroupMessagesToChatMessages] roundMap size:',
    roundMap.size,
    'noRoundMessages:',
    noRoundMessages.length,
  );

  // 处理有 conversationRoundId 的消息组
  // 用户消息按 roundId 聚合，助手消息按 roundId + sender 分别聚合
  roundMap.forEach((messages, roundId) => {
    // 按 timestamp 排序
    messages.sort((a, b) => a.timestamp - b.timestamp);

    // 调试：打印每条消息的关键信息
    console.log(`[transformGroupMessagesToChatMessages] roundId: ${roundId}`);
    messages.forEach((m) => {
      console.log(
        `  - id: ${m.id}, role: ${m.role}, senderId: ${
          m.sender.id
        }, senderName: ${m.sender.name}, botUuid: ${m.botUuid}, isToolResult: ${
          m.isToolResult
        }, content: ${m.content?.substring(
          0,
          50,
        )}..., hasBlocks: ${!!m.blocks}`,
      );
    });

    // 收集用户消息（同一 roundId 下聚合为一个）
    const userMsgs = messages.filter(
      (m) => m.role === 'user' && !m.isToolResult,
    );

    // 收集助手消息和工具调用结果
    const assistantMsgs = messages.filter(
      (m) => m.role === 'assistant' && !m.isToolResult,
    );
    const toolResultMsgs = messages.filter((m) => m.isToolResult);

    // 如果有用户消息，先添加用户消息
    if (userMsgs.length > 0) {
      const userMsg = userMsgs[0];
      chatMessages.push({
        id: userMsg.id,
        role: 'user',
        content: userMsg.content,
        status: 'success' as const,
        timestamp: userMsg.timestamp,
        blocks: userMsg.blocks,
        extra: {
          botUuid: userMsg.botUuid,
          botName: userMsg.sender.name,
          senderName: userMsg.sender.name,
          senderAvatar: userMsg.sender.avatar,
          timestamp: userMsg.timestamp,
          conversationRoundId: roundId,
          metadata: userMsg.metadata,
        },
      });
    }

    // 按 sender 分组助手消息（群聊中可能有多个不同的 Bot 回复）
    // 使用 botUuid 作为分组键，如果没有 botUuid 则使用 sender.id
    const assistantBySender = new Map<
      string,
      { msgs: GroupMessage[]; botUuid?: string; senderName: string }
    >();

    assistantMsgs.forEach((msg) => {
      const senderKey = msg.botUuid || msg.sender.id;
      const existing = assistantBySender.get(senderKey);
      if (existing) {
        existing.msgs.push(msg);
      } else {
        assistantBySender.set(senderKey, {
          msgs: [msg],
          botUuid: msg.botUuid,
          senderName: msg.sender.name,
        });
      }
    });

    // 同样按 sender 分组 toolResult 消息
    const toolResultBySender = new Map<string, GroupMessage[]>();
    toolResultMsgs.forEach((msg) => {
      const senderKey = msg.botUuid || msg.sender.id;
      const existing = toolResultBySender.get(senderKey);
      if (existing) {
        existing.push(msg);
      } else {
        toolResultBySender.set(senderKey, [msg]);
      }
    });

    // 为每个 sender 生成独立的助手消息气泡
    assistantBySender.forEach((senderData, senderKey) => {
      const { msgs: senderAssistantMsgs, botUuid, senderName } = senderData;
      const senderToolResultMsgs = toolResultBySender.get(senderKey) || [];

      // 合并该 sender 的助手消息内容
      const assistantContents: string[] = [];
      let lastAssistantTimestamp = 0;

      // 收集 tool_result 消息的 blocks（工具调用结果）
      const toolResultBlocksMap = new Map<string, any>();
      senderToolResultMsgs.forEach((msg) => {
        if (msg.blocks) {
          msg.blocks.forEach((block: any) => {
            if (block.type === 'tool_execution' && block.steps) {
              block.steps.forEach((step: any) => {
                const existingStep = toolResultBlocksMap.get(step.id);
                if (!existingStep || step.output !== undefined) {
                  toolResultBlocksMap.set(step.id, step);
                }
              });
            }
          });
        }
      });

      // 收集该 sender 的 assistant 消息
      senderAssistantMsgs.forEach((msg) => {
        if (msg.content) {
          assistantContents.push(msg.content);
        }
        if (msg.blocks) {
          msg.blocks.forEach((block: any) => {
            if (block.type === 'tool_execution' && block.steps) {
              block.steps.forEach((step: any) => {
                if (!toolResultBlocksMap.has(step.id)) {
                  toolResultBlocksMap.set(step.id, step);
                }
              });
            }
          });
        }
        if (msg.timestamp > lastAssistantTimestamp) {
          lastAssistantTimestamp = msg.timestamp;
        }
      });

      // 构建 blocks
      const finalBlocks: any[] = [];
      const mergedContent = assistantContents.join('\n\n');
      if (mergedContent) {
        finalBlocks.push({ type: 'text', content: mergedContent });
      }
      if (toolResultBlocksMap.size > 0) {
        finalBlocks.push({
          type: 'tool_execution',
          steps: Array.from(toolResultBlocksMap.values()),
        });
      }

      // 添加该 sender 的助手消息气泡
      if (finalBlocks.length > 0) {
        chatMessages.push({
          id: `assistant-${roundId}-${senderKey}`,
          role: 'assistant',
          content: mergedContent,
          status: 'success' as const,
          timestamp: lastAssistantTimestamp,
          blocks: finalBlocks,
          extra: {
            botUuid,
            botName: senderName,
            senderName,
            senderAvatar: senderData.msgs[0]?.sender.avatar,
            timestamp: lastAssistantTimestamp,
            conversationRoundId: roundId,
            metadata: senderData.msgs[0]?.metadata,
          },
        });
      }
    });
  });

  // 处理没有 conversationRoundId 的消息（排除 toolResult）
  noRoundMessages.forEach((msg) => {
    // toolResult 消息不作为独立消息显示
    if (msg.isToolResult) return;

    chatMessages.push({
      id: msg.id,
      role: msg.role || (msg.sender.type === 'bot' ? 'assistant' : 'user'),
      content: msg.content,
      status: 'success' as const,
      timestamp: msg.timestamp,
      blocks: msg.blocks,
      extra: {
        botUuid: msg.botUuid,
        botName: msg.sender.name,
        senderName: msg.sender.name,
        senderAvatar: msg.sender.avatar,
        timestamp: msg.timestamp,
        metadata: msg.metadata,
      },
    });
  });

  chatMessages.forEach(normalizeStateMachineTaskMessage);

  // 按 timestamp 排序
  chatMessages.sort((a, b) => (a.timestamp || 0) - (b.timestamp || 0));

  console.log(
    '[transformGroupMessagesToChatMessages] output:',
    chatMessages.length,
    chatMessages,
  );

  // 使用 ensureMessageBlocks 确保 blocks 存在
  return ensureMessageBlocks(chatMessages);
}

/**
 * 获取消息的 blocks
 */
export function getMessageBlocks(msg: ChatMessage): Block[] {
  return getMessageBlocksUtil(msg);
}

export interface UseGroupChatConfig {
  /** 群组信息（必须先加载完成） */
  group: GroupInfo | null;
  /** 历史消息 */
  historyMessages: GroupMessage[];
  /** 当前活跃的会话 ID（可选，会话模式时传入） */
  activeSessionId?: string;
  /**
   * 重新拉取会话消息（可选）。
   * 手动重连时先在 WS 仍断开的窗口内调用它,用后端最新历史覆盖本地快照,
   * 补回断线期间/断线前的实时消息。缺省则 reconnect 只重建连接(退化为旧行为)。
   */
  refetchMessages?: (sessionId: string) => void | Promise<void>;
}

export interface UseGroupChatResult {
  /** SDK 管理的消息列表 */
  messages: ChatMessage[];
  /** 群聊解析结果（用于追踪多个 Bot 的响应状态） */
  groupResult: GroupChatParseResult | null;
  /** 是否正在请求 */
  isRequesting: boolean;
  /** Provider 实例 */
  provider: ProviderInfo['provider'] | null;
  /** 是否已连接 */
  isConnected: boolean;
  /** WebSocket 连接状态（用于 ConnectionBanner） */
  wsStatus: ConnectionStatus;
  /** 重连次数 */
  retryCount: number;
  /** 发送消息 */
  sendMessage: (
    content: string,
    mentions?: string[],
    botUuid?: string,
    sessionId?: string,
  ) => void;
  /** 中止当前请求 */
  abort: () => void;
  /** 手动重连（断开旧 provider 后重新创建连接） */
  reconnect: () => void;
}

/**
 * useGroupChat Hook
 * 从全局缓存获取已连接的 provider，管理群聊消息
 */
export function useGroupChat(config: UseGroupChatConfig): UseGroupChatResult {
  const { group, historyMessages, activeSessionId, refetchMessages } = config;
  const userId = useUserStore((state) => state.userId);

  // 用 ref 持有最新的 refetchMessages,避免 reconnect 因它变化而重建闭包
  const refetchMessagesRef = useRef(refetchMessages);
  useEffect(() => {
    refetchMessagesRef.current = refetchMessages;
  }, [refetchMessages]);

  // 连接状态（用于 ConnectionBanner）
  const [wsStatus, setWsStatus] = useState<ConnectionStatus>('disconnected');
  const [retryCount, setRetryCount] = useState(0);

  // 强制组件更新（订阅 provider 缓存变化）
  const [, forceUpdate] = useState(0);

  const { connectGroup, disconnectGroup } = useGroupChatProviders();

  // 订阅 provider 变化，当 initProviders 创建 provider 后触发重渲染
  useEffect(() => {
    const listener = () => forceUpdate((n) => n + 1);
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  }, []);

  // 建立 WebSocket 连接（仅会话维度）
  useEffect(() => {
    if (!group?.id || !activeSessionId) return;

    // 按需建立会话级连接
    connectGroup(group.id, activeSessionId);

    // 清理函数：组件卸载或 group/session 变化时断开旧连接
    return () => {
      // 注意：这里不需要立即断开，因为切换时会创建新的 key
      // 如果需要立即断开，可以取消注释下面这行
      // disconnectGroup(group.id, activeSessionId);
    };
  }, [group?.id, activeSessionId, connectGroup]);

  // 从全局缓存获取 provider（仅会话维度）
  const providerInfo =
    group && activeSessionId
      ? getProvider(group.id, activeSessionId)
      : undefined;
  const provider = providerInfo?.provider ?? null;
  const isConnected = providerInfo?.isConnected ?? false;

  // 群聊结果状态
  const [groupResult, setGroupResult] = useState<GroupChatParseResult | null>(
    null,
  );

  // 保存每条消息的 extra 信息，用于在消息更新时恢复
  const messageExtraMapRef = useRef<Map<string, any>>(new Map());

  // 订阅 provider 的连接状态和群聊结果
  useEffect(() => {
    if (!provider) {
      setGroupResult(null);
      setWsStatus('disconnected');
      return;
    }

    // 初始状态
    setWsStatus(provider.isConnected ? 'connected' : 'disconnected');

    // 订阅连接状态变化
    // 断连时不销毁 provider，保留在缓存中以便 UI 展示断连状态（ConnectionBanner）
    // 用户刷新页面或切换群组时会重新创建 provider
    const unsubscribeConnection = provider.subscribeToConnectionStatus(
      (event: { status: string; retryCount: number }) => {
        console.log('[useGroupChat] Connection status changed:', event);
        // ProviderConnectionStatus 有 5 个值，UI 的 ConnectionStatus 只有 4 个值（无 'connecting'）
        // 'connecting' 映射为 'disconnected'，ConnectionBanner 会根据是否曾连接过来显示"正在连接…"
        const uiStatus: ConnectionStatus =
          event.status === 'connecting'
            ? 'disconnected'
            : (event.status as ConnectionStatus);
        setWsStatus(uiStatus);
        setRetryCount(event.retryCount);
      },
    );

    // 订阅群聊结果更新
    const unsubscribeGroupResult = provider.subscribeToGroupResult(
      (result: GroupChatParseResult) => {
        // SDK GroupChatParser 按 bot_uuid 分桶, bcs-system-message 会占据一个独立的 botState 进入 botBlocks,
        // 此处过滤,防止未来基于 botBlocks 的"哪些 Bot 在响应"等指示器把它当真 Bot 显示
        const filteredResult: GroupChatParseResult = {
          ...result,
          botBlocks:
            result.botBlocks?.filter(
              (b) => b.botUuid !== BCS_SYSTEM_MESSAGE_BOT_UUID,
            ) || [],
        };
        setGroupResult(filteredResult);
      },
    );

    // 拦截 provider 的 onMessage,保留 extra 字段
    const originalOnMessage = provider.onMessage;
    provider.onMessage = (message: ChatMessage) => {
      const extra = (message as any).extra;

      // 时间戳兜底: SDK resultToMessage 只写 createdAt, 不写 timestamp,
      // 而 MessageList 的 formatTime(msg.timestamp) 取的是 timestamp 字段,
      // 导致 WS 实时进来的消息(含系统通知)时间显示为空。
      // 优先用 BCS server 时间戳,再兜底 SDK 本地 createdAt。
      if (!(message as any).timestamp) {
        const serverTs = extra?.payload?.message?.timestamp;
        const sdkTs = (message as any).createdAt ?? extra?.createdAt;
        (message as any).timestamp = serverTs || sdkTs || Date.now();
      }

      // 保存新消息的 extra
      const msgExtra = (message as any).extra;
      if (msgExtra) {
        messageExtraMapRef.current.set(message.id, msgExtra);
      } else {
        // 尝试恢复 extra
        const savedExtra = messageExtraMapRef.current.get(message.id);
        if (savedExtra) {
          (message as any).extra = savedExtra;
        }
      }

      if (originalOnMessage) {
        originalOnMessage(message);
      }
    };

    return () => {
      unsubscribeConnection();
      unsubscribeGroupResult();
    };
  }, [provider]);

  // 转换历史消息
  const defaultMessages = useMemo<DefaultChatMessage[]>(() => {
    return toDefaultMessages(historyMessages);
  }, [historyMessages]);

  // 使用 group.id + sessionId 作为 conversationKey，确保切换群聊/会话时创建独立的聊天状态
  const conversationKey = useMemo(() => {
    if (!group?.id) return undefined;
    return activeSessionId
      ? `group-chat-${group.id}-session-${activeSessionId}`
      : `group-chat-${group.id}`;
  }, [group?.id, activeSessionId]);

  // 使用 useChat hook
  // 始终返回对象，避免 useChat 内部解构 undefined 报错
  // useChat 内部已有 if (!provider) return 的保护，provider 为空时不会执行聊天逻辑
  const chatConfig = useMemo(() => {
    return {
      provider: (provider ?? undefined) as any,
      conversationKey,
      defaultMessages,
      placeholderMessage: '',
    };
  }, [provider, conversationKey, defaultMessages]);

  const chatResult = useChat(chatConfig);

  // useChat 会接管 provider 回调，状态机 task 消息需要在 useChat 之后归一化
  useEffect(() => {
    if (!provider) return;

    const originalOnMessage = provider.onMessage;
    const originalOnComplete = provider.onComplete;

    provider.onMessage = (message: ChatMessage) => {
      normalizeStateMachineTaskMessage(message);
      preserveMessageExtra(message, messageExtraMapRef.current);
      originalOnMessage?.(message);
    };

    provider.onComplete = (messages: ChatMessage[]) => {
      messages.forEach((message) => {
        normalizeStateMachineTaskMessage(message);
        preserveMessageExtra(message, messageExtraMapRef.current);
      });
      originalOnComplete?.(messages);
    };

    return () => {
      provider.onMessage = originalOnMessage;
      provider.onComplete = originalOnComplete;
    };
  }, [provider]);

  // 使用 ref 保存 onRequest 和 setMessages，避免闭包问题
  const onRequestRef = useRef(chatResult?.onRequest);
  const setMessagesRef = useRef(chatResult?.setMessages);
  const messagesRef = useRef(chatResult?.messages);
  useEffect(() => {
    if (chatResult) {
      onRequestRef.current = chatResult.onRequest;
      setMessagesRef.current = chatResult.setMessages;
      messagesRef.current = chatResult.messages;
    }
  }, [chatResult?.onRequest, chatResult?.setMessages, chatResult?.messages]);

  // 跟踪上一次的 group.id、sessionId 和 historyMessages 长度，用于判断是否需要更新消息
  const prevGroupIdRef = useRef<string | undefined>(undefined);
  const prevSessionIdRef = useRef<string | undefined>(undefined);
  const prevHistoryLengthRef = useRef<number>(-1); // 初始值 -1，确保首次加载时触发更新
  const prevProviderRef = useRef<ProviderInfo['provider'] | null>(null);

  // 当群组变化、会话变化、历史消息变化或 provider 变化时，更新消息列表
  useEffect(() => {
    if (!provider || !group?.id) {
      return;
    }

    const currentGroupId = group.id;
    const currentSessionId = activeSessionId;
    const isGroupChanged = currentGroupId !== prevGroupIdRef.current;
    const isSessionChanged = currentSessionId !== prevSessionIdRef.current;
    const isHistoryChanged =
      historyMessages.length !== prevHistoryLengthRef.current;
    const isProviderChanged = provider !== prevProviderRef.current;

    // 如果切换了群聊/会话、历史消息变化或 provider 变化，则更新消息列表
    if (
      isGroupChanged ||
      isSessionChanged ||
      isHistoryChanged ||
      isProviderChanged
    ) {
      // 清空 extra map
      messageExtraMapRef.current.clear();

      // 使用公共函数转换历史消息
      const newMessages = transformGroupMessagesToChatMessages(historyMessages);

      // 保存 extra 到 map
      newMessages.forEach((msg) => {
        const extra = (msg as any).extra;
        if (extra) {
          messageExtraMapRef.current.set(msg.id, extra);
        }
      });

      // 当群组或会话切换时，SDK 的 conversationKey effect 会通过
      // Promise.resolve().then() 异步调用 chat.setMessages(defaultMessages)，
      // 如果我们同步设置消息，SDK 的异步操作会在之后覆盖掉正确消息。
      // 使用微任务延迟设置，确保在 SDK 异步初始化之后执行。
      const shouldDefer =
        isGroupChanged || isSessionChanged || isProviderChanged;
      const applyMessages = () => {
        if (setMessagesRef.current) {
          setMessagesRef.current(newMessages);
        }
      };

      if (shouldDefer) {
        queueMicrotask(applyMessages);
      } else {
        applyMessages();
      }

      // 更新 refs
      prevGroupIdRef.current = currentGroupId;
      prevSessionIdRef.current = currentSessionId;
      prevHistoryLengthRef.current = historyMessages.length;
      prevProviderRef.current = provider;
    }
  }, [group?.id, activeSessionId, historyMessages, provider]);

  // 发送消息
  const sendMessage = useCallback(
    (
      content: string,
      mentions?: string[],
      botUuid?: string,
      targetSessionId?: string,
    ) => {
      console.log('[useGroupChat] sendMessage called:', {
        hasGroup: !!group,
        hasContent: !!content.trim(),
        hasProvider: !!provider,
        hasChatResult: !!chatResult,
        hasOnRequest: !!onRequestRef.current,
        targetSessionId,
        activeSessionId,
      });

      if (!group || !content.trim() || !provider) {
        console.warn(
          '[useGroupChat] Cannot send message: group or provider not ready',
        );
        return;
      }

      // 埋点：群聊发送消息
      Tracert.click('c468610.d693571', {
        group_id: group.id,
        mention_count: mentions?.length || 0,
        has_bot_uuid: !!botUuid,
      }); /* spm: 我的协作-群聊操作-发送消息 */

      // 使用传入的 sessionId 或当前活跃的 sessionId
      const effectiveSessionId = targetSessionId || activeSessionId;

      // 当前用户的 actor uuid
      // SDK useChat.onRequest 用 params.userMessage.extra 作为本地 user 消息的 extra
      // 写入后 getSenderInfo 能正确识别为"当前用户自己"，消息右对齐
      const senderBotUuid = userId ? `human_${userId}` : undefined;

      const params = {
        query: content.trim(),
        groupId: group.id,
        senderId: userId || 'anonymous',
        userMessage: {
          content: content.trim(),
          extra: {
            botUuid: senderBotUuid,
          },
        },
        botUuid: botUuid,
        mentions: mentions && mentions.length > 0 ? mentions : undefined,
        mentionAll: mentions?.includes('ALL'),
        sessionId: effectiveSessionId || undefined,
      };

      console.log('[useGroupChat] Sending message with params:', params);

      if (onRequestRef.current) {
        onRequestRef.current(params);
      } else {
        console.warn('[useGroupChat] onRequest not ready');
      }
    },
    [group, provider, userId, activeSessionId, chatResult],
  );

  const groupChatAabort = useCallback(() => {
    if (chatResult && group?.id) {
      // @ts-ignore
      chatResult.abort(group.id, activeSessionId);
    }
  }, [chatResult, group, activeSessionId]);

  // 手动重连：先重新拉取历史，再重建 provider
  // 顺序很关键:此刻 WS 仍断开(reconnectAttempts=0 不会自动重连),
  // 先拉历史能用后端最新快照补回丢失的实时消息,且不会有 live 消息在拉取窗口内被整体替换覆盖;
  // 待历史落地后再重建 provider 恢复实时,后续新消息 append 在最新历史之上。
  const reconnect = useCallback(async () => {
    if (!group?.id || !activeSessionId) return;
    console.log(
      '[useGroupChat] Manual reconnect for session:',
      group.id,
      activeSessionId,
    );
    // 1. WS 仍断开时重新拉取最新历史(失败不阻断重连)
    if (refetchMessagesRef.current) {
      try {
        await refetchMessagesRef.current(activeSessionId);
      } catch (err) {
        console.error('[useGroupChat] reconnect refetch messages failed:', err);
      }
    }
    // 2. 重建 provider 恢复实时连接
    disconnectGroup(group.id, activeSessionId);
    // 下一帧重新连接，确保旧 provider 清理完成
    requestAnimationFrame(() => {
      connectGroup(group.id, activeSessionId);
    });
  }, [group?.id, activeSessionId, disconnectGroup, connectGroup]);

  // 时间戳兜底:
  // SDK resultToMessage 只设了 createdAt,而 MessageList 读 msg.timestamp,
  // 优先用 BCS server 给的 payload.message.timestamp,再兜底 createdAt。
  // role 由 SDK 按 payload.message.role 还原,这里不再二次纠正。
  // 仅对 bot_uuid === BCS_SYSTEM_MESSAGE_BOT_UUID 但 payload.role 缺失的兜底场景保留 system 修正。
  const messages = useMemo(() => {
    const raw = chatResult?.messages;
    if (!raw?.length) return raw ?? [];
    const visible = raw.filter((msg) => !isSilentAssistantReply(msg));
    let mutated = visible.length !== raw.length;
    const fixed = visible.map((msg) => {
      const extra = (msg as any).extra;
      const isSystemByBotUuid =
        extra?.bot_uuid === BCS_SYSTEM_MESSAGE_BOT_UUID ||
        extra?.botUuid === BCS_SYSTEM_MESSAGE_BOT_UUID;
      const needRoleFix = isSystemByBotUuid && msg.role !== 'system';
      const needTsFix = !(msg as any).timestamp;
      if (!needRoleFix && !needTsFix) return msg;

      const next: any = { ...msg };
      if (needRoleFix) {
        next.role = 'system';
      }
      if (needTsFix) {
        const serverTs = extra?.payload?.message?.timestamp;
        const sdkTs = (msg as any).createdAt ?? extra?.createdAt;
        next.timestamp = serverTs || sdkTs;
      }
      mutated = true;
      return next;
    });
    return mutated ? fixed : raw;
  }, [chatResult?.messages]);

  // 如果没有 provider 或 chatResult，返回空状态
  if (!provider || !chatResult) {
    return {
      messages: [],
      groupResult: null,
      isRequesting: false,
      provider: null,
      isConnected: false,
      wsStatus: 'disconnected',
      retryCount: 0,
      sendMessage: () => {
        console.warn('[useGroupChat] Provider not initialized');
      },
      abort: groupChatAabort,
      reconnect,
    };
  }

  return {
    messages,
    groupResult,
    isRequesting: chatResult.isRequesting,
    provider,
    isConnected,
    wsStatus,
    retryCount,
    sendMessage,
    abort: groupChatAabort,
    reconnect,
  };
}
