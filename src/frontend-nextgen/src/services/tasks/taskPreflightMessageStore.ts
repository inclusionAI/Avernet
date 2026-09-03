import type { ChatMessage } from '@tc-chat/core';

const STORAGE_KEY = 'teamclaw.task-preflight-messages.v1';

interface PersistedTaskPreflightMessage {
  id: string;
  sessionKey: string;
  content: string;
  createdAt: number;
}

function readAll(): PersistedTaskPreflightMessage[] {
  if (typeof window === 'undefined') return [];
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(
      (item): item is PersistedTaskPreflightMessage =>
        !!item &&
        typeof item === 'object' &&
        typeof (item as PersistedTaskPreflightMessage).id === 'string' &&
        typeof (item as PersistedTaskPreflightMessage).sessionKey === 'string' &&
        typeof (item as PersistedTaskPreflightMessage).content === 'string' &&
        typeof (item as PersistedTaskPreflightMessage).createdAt === 'number',
    );
  } catch {
    return [];
  }
}

function writeAll(messages: PersistedTaskPreflightMessage[]): void {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(messages));
  } catch {
    // 本地存储不可用时仍保留当前会话内的内存消息，不阻断任务执行。
  }
}

export function upsertTaskPreflightMessage(
  sessionKey: string,
  message: Pick<ChatMessage, 'id' | 'content' | 'createdAt'>,
): void {
  if (!sessionKey || !message.id) return;
  const next = readAll().filter((item) => !(item.sessionKey === sessionKey && item.id === message.id));
  next.push({
    id: message.id,
    sessionKey,
    content: message.content,
    createdAt: message.createdAt ?? Date.now(),
  });
  writeAll(next);
}

export function listTaskPreflightMessages(sessionKey: string): ChatMessage[] {
  if (!sessionKey) return [];
  return readAll()
    .filter((item) => item.sessionKey === sessionKey)
    .sort((a, b) => a.createdAt - b.createdAt)
    .map((item) => ({
      id: item.id,
      role: 'assistant' as const,
      status: 'history' as const,
      content: item.content,
      createdAt: item.createdAt,
    }));
}

export function mergeTaskPreflightMessages(messages: ChatMessage[], persisted: ChatMessage[]): ChatMessage[] {
  if (persisted.length === 0) return messages;
  const existingIds = new Set(messages.map((message) => message.id));
  const fresh = persisted.filter((message) => !existingIds.has(message.id));
  if (fresh.length === 0) return messages;
  return [...messages, ...fresh].sort((a, b) => (a.createdAt ?? 0) - (b.createdAt ?? 0));
}
