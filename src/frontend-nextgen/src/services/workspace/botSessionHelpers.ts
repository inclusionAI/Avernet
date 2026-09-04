import type { BotChatSessionView } from '@/services/workspace/botSessionService';

/** 追加去重：保留已存在 sessionId，仅补入新会话（纯函数，供会话列表分页追加使用）。 */
export function appendUnique(
  current: BotChatSessionView[],
  incoming: BotChatSessionView[],
): BotChatSessionView[] {
  const existingIds = new Set(current.map((session) => session.sessionId));
  return [...current, ...incoming.filter((session) => !existingIds.has(session.sessionId))];
}
