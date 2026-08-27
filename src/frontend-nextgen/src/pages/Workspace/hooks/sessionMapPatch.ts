import type { SessionView } from '@/domain/collaboration';

/** 在多群会话 map 中就地替换指定会话数据（用于成员增删后刷新）。 */
export function replaceSessionInMap(
  current: Record<string, SessionView[]>,
  sessionId: string,
  session: SessionView,
): Record<string, SessionView[]> {
  const next: Record<string, SessionView[]> = {};
  for (const [groupId, list] of Object.entries(current)) {
    next[groupId] = list.some((item) => item.sessionId === sessionId)
      ? list.map((item) =>
          item.sessionId === sessionId
            ? {
                ...item,
                ...session,
                // 成员增删/模式更新接口并不总是返回完整会话字段，这里锁定
                // sessionId/groupId/收藏状态，避免刷新后选中态和侧栏定位被意外清除。
                sessionId: item.sessionId,
                groupId: item.groupId,
                favorite: item.favorite,
              }
            : item,
        )
      : list;
  }
  return next;
}
