import type { ParticipantMode, SessionView } from '@/domain/collaboration';
import type { DomainError } from '@/services/workspace/identityService';
import { sessionService } from '@/services/workspace/sessionService';
import { useCallback, useEffect, useRef } from 'react';
import { toast } from 'sonner';
import { replaceSessionInMap } from './sessionMapPatch';

function notifyError(err: DomainError): void {
  toast.error(err.friendlyMessage);
}

function errOf(res: { ok: false; error: DomainError }): DomainError {
  return res.error;
}

export interface UseSessionMemberSyncResult {
  /** 更新会话成员姿态/发言模式（Bot auto↔muted；Human present↔absent），成功后就地刷新会话参与者。 */
  updateMemberMode: (sessionId: string, actorId: string, mode: ParticipantMode) => Promise<boolean>;
  /** 用后端返回的会话详情就地替换指定会话（成员增删后就地刷新）。 */
  applySessionUpdate: (sessionId: string, session: SessionView) => void;
}

/**
 * useSessionMemberSync —— 从 useGroupSessions 拆出的子 Hook：
 * - selectedSessionId 变化时拉 getSession 详情补齐 participants/mode
 *   （列表接口只返回 participant_count）；
 * - 暴露 updateMemberMode：PATCH 后用响应的就地刷新对应会话。
 *
 * 体积拆分点（TC-G005 Hook ≤250 行），与 useSessionMap 同级，共享 applyMapUpdate。
 */
export function useSessionMemberSync(
  selectedSessionId: string | null,
  applyMapUpdate: (fn: (cur: Record<string, SessionView[]>) => Record<string, SessionView[]>) => void,
  /**
   * 选中会话的 participants 长度。列表接口不返回 participants（空数组），
   * 详情接口才会填充。当身份切换/列表刷新导致 participants 归零时，
   * 此值变化触发 effect 重新拉取详情，避免竞态丢失详情。
   */
  participantsCount: number,
): UseSessionMemberSyncResult {
  const pendingDetailRef = useRef<SessionView | null>(null);

  const applyDetail = useCallback(
    (detail: SessionView) => {
      applyMapUpdate((cur) => {
        const next = { ...cur };
        for (const gid of Object.keys(next)) {
          const idx = next[gid].findIndex((s) => s.sessionId === detail.sessionId);
          if (idx < 0) continue;
          const existing = next[gid][idx];
          if (existing.participants.length > 0) return cur;
          const merged: SessionView = { ...existing, participants: detail.participants };
          const list = [...next[gid]];
          list[idx] = merged;
          next[gid] = list;
          return next;
        }
        return cur;
      });
    },
    [applyMapUpdate],
  );

  // 选中会话变化或 participants 归零时拉详情。
  // participantsCount 作为依赖：身份切换后列表刷新 participants 归零，effect 重新触发。
  useEffect(() => {
    if (!selectedSessionId) {
      pendingDetailRef.current = null;
      return;
    }
    let cancelled = false;
    sessionService.getSessionDetail(selectedSessionId).then((res) => {
      if (cancelled || !res.ok) return;
      pendingDetailRef.current = res.data;
      applyDetail(res.data);
    });
    return () => {
      cancelled = true;
    };
  }, [selectedSessionId, participantsCount, applyDetail]);

  const updateMemberMode = useCallback(
    async (sessionId: string, actorId: string, mode: ParticipantMode): Promise<boolean> => {
      const res = await sessionService.updateMemberMode(sessionId, actorId, mode);
      if (!res.ok) {
        notifyError(errOf(res));
        return false;
      }
      const refreshed = res.data;
      applyMapUpdate((cur) => replaceSessionInMap(cur, sessionId, refreshed));
      toast.success(mode === 'present' ? '已加入当前会话' : mode === 'absent' ? '已退出当前会话' : '协作模式已更新');
      return true;
    },
    [applyMapUpdate],
  );

  const applySessionUpdate = useCallback(
    (sessionId: string, session: SessionView) => {
      applyMapUpdate((current) => replaceSessionInMap(current, sessionId, session));
    },
    [applyMapUpdate],
  );

  return { updateMemberMode, applySessionUpdate };
}
