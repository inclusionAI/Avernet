import type { SessionView } from '@/domain/collaboration';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { useEffect, type MutableRefObject } from 'react';
import { isSessionKnownGone } from './useStaleSessionFallback';

/** 普通群自动首选；指定会话入口只接受已验证会话，分页/删除均不能切到其他会话。 */
export function useGroupSessionSelection(
  groupId: string | null,
  sessionViews: SessionView[],
  rawRef: MutableRefObject<Record<string, SessionView[]>>,
  selectSession: (id: string | null) => void,
  updateGroupSessions: (gid: string, update: (list: SessionView[]) => SessionView[]) => void,
  pinnedSession?: SessionView,
) {
  const selectedId = useWorkspaceStore((s) => s.selectedSessionId);
  useEffect(() => {
    if (!groupId) return;
    if (pinnedSession) {
      if (pinnedSession.groupId !== groupId) return;
      if (
        isSessionKnownGone(pinnedSession.sessionId) ||
        useWorkspaceStore.getState().selectedSessionId !== pinnedSession.sessionId
      )
        return;
      const list = rawRef.current[groupId];
      if (list && !list.some((session) => session.sessionId === pinnedSession.sessionId)) {
        updateGroupSessions(groupId, (current) => [pinnedSession, ...current]);
      }
      return;
    }
    if (!sessionViews.length || useWorkspaceStore.getState().selectedSessionId) return;
    // 同 commit 的重拉已同步清除 ref，不能消费闭包中上一页的会话。
    const first = rawRef.current[groupId]?.[0];
    if (first) selectSession(first.sessionId);
  }, [groupId, pinnedSession, rawRef, selectSession, sessionViews, updateGroupSessions]);

  if (
    pinnedSession &&
    (pinnedSession.groupId !== groupId ||
      selectedId !== pinnedSession.sessionId ||
      isSessionKnownGone(pinnedSession.sessionId))
  )
    return null;
  return (
    sessionViews.find((session) => session.sessionId === selectedId && session.groupId === groupId) ??
    pinnedSession ??
    null
  );
}
