import type { SessionView } from '@/domain/collaboration';
import { sessionService } from '@/services/workspace/sessionService';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { useCallback } from 'react';
import { toast } from 'sonner';

/** 从 map 中移除会话并校正选中态（删除/退出共用）。 */
function removeSessionAndFixSelection(
  applyMapUpdate: (fn: (cur: Record<string, SessionView[]>) => Record<string, SessionView[]>) => void,
  selectSession: (id: string | null) => void,
  sessionId: string,
): void {
  const store = useWorkspaceStore.getState();
  applyMapUpdate((map) => {
    const removal = sessionService.removeFromMap(map, sessionId);
    if (removal.hitGroupId && store.selectedGroupId === removal.hitGroupId && store.selectedSessionId === sessionId) {
      selectSession(removal.remaining[0]?.sessionId ?? null);
    }
    return removal.next;
  });
}

/** useSessionMutations — 从 useGroupSessions 拆出的退出/删除会话逻辑。 */
export function useSessionMutations(
  applyMapUpdate: (fn: (cur: Record<string, SessionView[]>) => Record<string, SessionView[]>) => void,
  selectSession: (id: string | null) => void,
) {
  const deleteSession = useCallback(
    async (sessionId: string): Promise<boolean> => {
      const res = await sessionService.deleteSession(sessionId);
      if (!res.ok) {
        toast.error(res.error.friendlyMessage);
        return false;
      }
      removeSessionAndFixSelection(applyMapUpdate, selectSession, sessionId);
      toast.success('会话已删除');
      return true;
    },
    [applyMapUpdate, selectSession],
  );

  const leaveSession = useCallback(
    async (sessionId: string, actorId: string): Promise<boolean> => {
      const res = await sessionService.leaveSession(sessionId, actorId);
      if (!res.ok) {
        toast.error(res.error.friendlyMessage);
        return false;
      }
      removeSessionAndFixSelection(applyMapUpdate, selectSession, sessionId);
      toast.success('已退出会话');
      return true;
    },
    [applyMapUpdate, selectSession],
  );

  return { deleteSession, leaveSession };
}
