// 好友名单（公开设置 · 好友名单 section/抽屉）纯逻辑：仅有客户端搜索与
// 分类计数派生。无 I/O、无 React 依赖。

import type {
  BotFriendPage,
  BotIdentityFriendView,
} from '@/services/workspace/botFriendConversationService';
import type { DomainResult } from '@/services/workspace/identityService';

/**
 * 在已完整拉取的好友集合里做大小写不敏感的子串过滤：命中展示名或原始
 * actor id（staff_no / bot uuid)。空关键词原样返回整个集合。
 */
export function filterFriendViewsByKeyword(
  views: BotIdentityFriendView[],
  keyword: string,
): BotIdentityFriendView[] {
  const needle = keyword.trim().toLowerCase();
  if (!needle) return views;
  return views.filter(
    (view) =>
      view.displayName.toLowerCase().includes(needle) ||
      view.actorId.toLowerCase().includes(needle),
  );
}

export interface BotFriendRosterCounts {
  users: number | null;
  bots: number | null;
}

/**
 * 「好友用户 N · 好友 Bot M」的计数派生：成功段取该段 total；失败段独立
 * 降级为 null(section 上展示 `-` 并重试，不影响另一段）。
 */
export function rosterCounts(
  human: DomainResult<BotFriendPage<BotIdentityFriendView>>,
  bot: DomainResult<BotFriendPage<BotIdentityFriendView>>,
): BotFriendRosterCounts {
  return {
    users: human.ok ? human.data.total : null,
    bots: bot.ok ? bot.data.total : null,
  };
}
