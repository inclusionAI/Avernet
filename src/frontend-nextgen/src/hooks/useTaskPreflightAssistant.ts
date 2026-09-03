import {
  listTaskPreflightMessages,
  mergeTaskPreflightMessages,
  upsertTaskPreflightMessage,
} from '@/services/tasks/taskPreflightMessageStore';
import { streamMockMessage } from '@/services/tasks/taskPreflightMock';
import type { ChatMessage } from '@tc-chat/core';
import { useCallback, useEffect } from 'react';

/**
 * 演示用：任务前置预检(大促 OKR)命中后，以本地 assistant 身份追加 Bot 的需求分析/委派回复。
 * 不经过 provider、不触发真实 Bot 请求，与真实 executeTaskService 解耦。
 * 抽自 useWorkspace / useGroupChat 中重复的 preflight 接线(appendAssistantMessage/streamAssistantMessage)，以控制文件体积。
 */
interface TaskPreflightChatLike {
  messages?: ChatMessage[];
  setMessages: (updater: (current: ChatMessage[]) => ChatMessage[]) => void;
  setMessage: (id: string, patch: Partial<ChatMessage>) => void;
}

export interface UseTaskPreflightAssistantOptions {
  /** 当前活跃会话的 chat adapter；合并历史时需 messages。 */
  chat: TaskPreflightChatLike;
  /** 会话 key：用于本地持久化 preflight 消息(localStorage)，缺省则不持久化。 */
  sessionKey: string | undefined;
  /** 重新进入会话时，把本地持久化的 preflight 消息合并回历史消息(默认 false)。 */
  mergePersistedHistory?: boolean;
}

export function useTaskPreflightAssistant({
  chat,
  sessionKey,
  mergePersistedHistory,
}: UseTaskPreflightAssistantOptions) {
  // 重新进入会话时，把演示用的前置 assistant 消息从本地持久化存储合并回历史消息。
  useEffect(() => {
    if (!mergePersistedHistory || !sessionKey) return;
    const persisted = listTaskPreflightMessages(sessionKey);
    if (persisted.length === 0) return;
    chat.setMessages((current) => mergeTaskPreflightMessages(current, persisted));
  }, [chat.messages, chat.setMessages, sessionKey, mergePersistedHistory]);

  // 演示用：本地追加一条 assistant 消息，不经过 provider、不触发真实 Bot 请求。
  const appendAssistantMessage = useCallback(
    (content: string) => {
      const message: ChatMessage = {
        id: `task-preflight-${Date.now()}`,
        role: 'assistant',
        status: 'done',
        content,
        createdAt: Date.now(),
      };
      chat.setMessages((current) => [...current, message]);
      if (sessionKey) upsertTaskPreflightMessage(sessionKey, message);
    },
    [chat, sessionKey],
  );

  // 演示用：本地流式追加 assistant 回复，不经过 provider、不触发真实 Bot 请求。
  const streamAssistantMessage = useCallback(
    async (content: string) => {
      const messageId = `task-preflight-${Date.now()}`;
      const createdAt = Date.now();
      chat.setMessages((current) => [
        ...current,
        { id: messageId, role: 'assistant', status: 'streaming', content: '', createdAt },
      ]);
      await streamMockMessage(content, (partial, done) => {
        chat.setMessage(messageId, { content: partial, status: done ? 'done' : 'streaming' });
        if (sessionKey) {
          upsertTaskPreflightMessage(sessionKey, { id: messageId, content: partial, createdAt });
        }
      });
    },
    [chat, sessionKey],
  );

  return { appendAssistantMessage, streamAssistantMessage };
}
