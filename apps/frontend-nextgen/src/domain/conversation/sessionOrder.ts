import type { BotChatSessionView } from './types';

/** 已加载会话按最近更新时间倒序；缺失时间放末尾，同时间保持原顺序。 */
export function sortConversationSessions(items: BotChatSessionView[]): BotChatSessionView[] {
  const time = (item: BotChatSessionView): number => {
    const modified = Date.parse(item.gmtModified);
    if (Number.isFinite(modified)) return modified;
    const created = Date.parse(item.gmtCreate);
    return Number.isFinite(created) ? created : 0;
  };
  return [...items].sort((a, b) => time(b) - time(a));
}
