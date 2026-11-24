import type { BotChatSessionView } from './botSessionService';

export const FIRST_MESSAGE_SESSION_TITLE_LIMIT = 50;

/**
 * 「未被用户命名」的默认标题 marker 集合。
 *
 * 前端建会话有两处入口写入不同字面量（历史对齐老前端链路，不改上游）：
 * - Bot 单聊（`botSessionService.createSession`）写 `新会话`；
 * - CodingBot（`useConversationSessionCreation`）写 `新对话`。
 *
 * 「首条消息自动命名」的守卫必须接受**全部**自身建会话写入的默认 marker，
 * 否则 CodingBot 会话（title `新对话`）与守卫判据（`新会话`）错配 → 永不自动命名。
 */
export const DEFAULT_SESSION_TITLE_MARKERS = ['新会话', '新对话'] as const;

const isDefaultSessionTitle = (title: string): boolean =>
  (DEFAULT_SESSION_TITLE_MARKERS as readonly string[]).includes(title);

/**
 * 根据用户发送的首条非空消息生成单聊会话标题。
 * 仅标题仍为默认 marker（未被用户命名）且 `messageCount === 0` 时介入，
 * 避免覆盖手动命名或已有消息的会话。
 */
export function buildFirstMessageSessionTitle(
  session: BotChatSessionView,
  content: string,
): string | null {
  if (!isDefaultSessionTitle(session.title) || session.messageCount !== 0) {
    return null;
  }
  const normalized = content.trim();
  if (!normalized) return null;
  return normalized.length > FIRST_MESSAGE_SESSION_TITLE_LIMIT
    ? `${normalized.slice(0, FIRST_MESSAGE_SESSION_TITLE_LIMIT)}...`
    : normalized;
}
