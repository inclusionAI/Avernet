import { clampView, type WorkspaceView } from '@/domain/collaboration/availableViews';
import type { GroupMembership, IdentityMemo } from './workspaceStoreState';

/** 手风琴互斥：展开新 key 时收起旧 key，再次点击则全部收起。 */
export function toggleRecordExclusive(prev: Record<string, true>, key: string): Record<string, true> {
  if (prev[key]) return {};
  return { [key]: true };
}

/** 记录某身份的当前选中态到 memo（null 值保留上次记忆，不覆盖）。 */
export function rememberLastSession(
  prev: Record<string, IdentityMemo>,
  identityId: string,
  opts: {
    view: WorkspaceView;
    selectedGroupId: string | null;
    selectedSessionId: string | null;
    expandedGroupId: string | null;
    membership: GroupMembership;
    selectedBotSessionId: string | null;
    expandedBotId: string | null;
  },
): typeof prev {
  const last = prev[identityId] ?? {};
  const entry: IdentityMemo = {
    view: opts.view,
    groupId: opts.selectedGroupId ?? last.groupId ?? null,
    groupSessionId: opts.selectedSessionId ?? last.groupSessionId ?? null,
    expandedGroupId: opts.expandedGroupId ?? last.expandedGroupId ?? null,
    membership: opts.membership,
    botId: opts.expandedBotId ?? last.botId ?? null,
    botSessionId: opts.selectedBotSessionId ?? last.botSessionId ?? null,
    expandedBotId: opts.expandedBotId ?? last.expandedBotId ?? null,
  };
  return { ...prev, [identityId]: entry };
}

/** 从 memo 恢复某身份的选中态（view 钳制到可用集，其余字段取记忆值或默认 null）。 */
export function restoreIdentitySelection(
  memo: Record<string, IdentityMemo>,
  id: string | null,
  views: WorkspaceView[],
  fallbackView: WorkspaceView,
) {
  const memoForNext = id ? memo[id] : undefined;
  const restoredView = memoForNext?.view ? clampView(views, memoForNext.view) : clampView(views, fallbackView);
  const selectedGroupId = memoForNext?.groupId ?? null;
  const selectedSessionId = memoForNext?.groupSessionId ?? null;
  const expandedGroupIds = memoForNext?.expandedGroupId ? { [memoForNext.expandedGroupId]: true as const } : {};
  const membership = memoForNext?.membership ?? 'direct';
  const selectedBotSessionId = memoForNext?.botSessionId ?? null;
  const expandedBotIds = memoForNext?.botId ? { [memoForNext.botId]: true as const } : {};
  const expandedBotSectionKey = memoForNext?.botId ? { [memoForNext.botId]: 'mine' } : {};
  return {
    memoForNext,
    restoredView,
    selectedGroupId,
    selectedSessionId,
    expandedGroupIds,
    membership,
    selectedBotSessionId,
    expandedBotIds,
    expandedBotSectionKey,
  };
}

/** 切换某群在侧边栏的折叠态。 */
export const toggleArrayItem = (arr: string[], item: string): string[] =>
  arr.includes(item) ? arr.filter((i) => i !== item) : [...arr, item];
