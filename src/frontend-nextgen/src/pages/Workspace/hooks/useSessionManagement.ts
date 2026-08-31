import type { SessionView } from '@/domain/collaboration';
import type { DomainResult } from '@/services/workspace/identityService';
import { invitationService } from '@/services/workspace/invitationService';
import { sessionService } from '@/services/workspace/sessionService';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { useCallback } from 'react';
import { toast } from 'sonner';

export interface UseSessionManagementResult {
  addMember: (actorId: string) => Promise<boolean>;
  removeMember: (actorId: string) => Promise<boolean>;
  leaveSession: (actorId: string) => Promise<boolean>;
  createShare: () => Promise<DomainResult<{ invitationUrl: string }>>;
}

function notifyError(res: { ok: false; error: { friendlyMessage: string } }): void {
  toast.error(res.error.friendlyMessage);
}

/**
 * 会话成员管理写操作：新增/移除/退出成员，以及生成会话邀请链接。
 */
export function useSessionManagement(
  session: SessionView | null,
  applySessionUpdate: (sessionId: string, session: SessionView) => void,
): UseSessionManagementResult {
  const addMember = useCallback(
    async (actorId: string) => {
      if (!session) return false;
      const res = await sessionService.addMember(session.sessionId, actorId);
      if (!res.ok) {
        notifyError(res);
        return false;
      }
      applySessionUpdate(session.sessionId, res.data);
      const store = useWorkspaceStore.getState();
      if (store.selectedGroupId !== session.groupId) store.selectGroup(session.groupId);
      store.selectSession(session.sessionId);
      return true;
    },
    [applySessionUpdate, session],
  );

  const removeMember = useCallback(
    async (actorId: string) => {
      if (!session) return false;
      const res = await sessionService.removeMember(session.sessionId, actorId);
      if (!res.ok) {
        notifyError(res);
        return false;
      }
      applySessionUpdate(session.sessionId, res.data);
      const store = useWorkspaceStore.getState();
      if (store.selectedGroupId !== session.groupId) store.selectGroup(session.groupId);
      store.selectSession(session.sessionId);
      toast.success('已移除会话成员');
      return true;
    },
    [applySessionUpdate, session],
  );

  const leaveSession = useCallback(
    async (actorId: string) => {
      if (!session) return false;
      const res = await sessionService.leaveSession(session.sessionId, actorId);
      if (!res.ok) {
        notifyError(res);
        return false;
      }
      applySessionUpdate(session.sessionId, res.data);
      toast.success('已退出会话');
      return true;
    },
    [applySessionUpdate, session],
  );

  const createShare = useCallback(async () => {
    if (!session) {
      return {
        ok: false as const,
        error: { code: 'SESSION_MISSING', friendlyMessage: '未选择会话', canRetry: false },
      };
    }
    const res = await invitationService.createSessionShare(session.sessionId);
    if (!res.ok) notifyError(res);
    return res;
  }, [session]);

  return { addMember, removeMember, leaveSession, createShare };
}
