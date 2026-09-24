import type { BotChatSessionView } from './botSessionService';

export const FIRST_MESSAGE_SESSION_TITLE_LIMIT = 50;

/**
 * 根据用户发送的首条非空消息生成单聊会话标题。
 * 仅标题仍为「新会话」且 `messageCount === 0` 时介入，避免覆盖手动命名或已有消息的会话。
 */
export function buildFirstMessageSessionTitle(session: BotChatSessionView, content: string): string | null {
  if (session.title !== '新会话' || session.messageCount !== 0) return null;
  const normalized = content.trim();
  if (!normalized) return null;
  return normalized.length > FIRST_MESSAGE_SESSION_TITLE_LIMIT
    ? `${normalized.slice(0, FIRST_MESSAGE_SESSION_TITLE_LIMIT)}...`
    : normalized;
}
