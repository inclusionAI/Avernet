/**
 * useSessionMembers - 会话成员增删管理 Hook
 *
 * 与 useGroupSessions.joinSession/leaveSession 的区别：
 * - join/leave 走 PATCH mode 切换，语义是"当前用户进/出会话"
 * - 本 hook 是"管理他人进/出会话"，由会话设置抽屉使用
 *
 * 后端接口：
 * - 添加 = POST /bcnproxy/sessions/{session_id}/members  body: { bot_uuid, role? }
 * - 移除 = DELETE /bcnproxy/sessions/{session_id}/members/{bot_uuid}
 *
 * 两个接口都返回完整 Session 对象，前端用 participants 重建本地 members。
 */

import type { ActorKind, GroupSession } from '@/pages/GroupChat/types';
import * as BcnController from '@/services/backend-api/BcnController';
import { useGroupSessionStore } from '@/stores/groupSessionStore';
import { extractErrorMessage } from '@/utils/requestErrorHandler';
import { useCallback, useState } from 'react';
import { toast } from 'sonner';

export interface AddSessionMemberItem {
  /** Bot 的 bot_uuid 或 Human 的 actor_id（human_{staff_no}） */
  actorId: string;
  /** 仅作选择来源标识；当前后端 POST 接口只需传 actorId 作为 bot_uuid */
  actorKind?: ActorKind;
}

export interface AddBatchResult {
  success: number;
  failed: number;
}

/**
 * 把 API 返回的 participants 转为本地 GroupSession.members 结构
 * （与 useGroupSessions.transformSessionData 中的映射保持一致）
 */
function membersFromParticipants(
  participants: BcnController.SessionInfoResponse['participants'] | undefined,
): GroupSession['members'] {
  return (participants || []).map((p) => ({
    actorId: p.actor_id || p.bot_uuid || '',
    actorKind: (p.actor_kind ||
      (p.type === 'bot' ? 'bot' : 'human')) as ActorKind,
    name: p.bot_name,
    mode: p.mode,
    role: p.role,
    type: p.type,
  }));
}

/** 用接口返回的完整 Session 同步 store */
function applyResponseToStore(
  sessionId: string,
  resp: BcnController.SessionInfoResponse,
) {
  const { updateSession } = useGroupSessionStore.getState();
  updateSession(sessionId, {
    members: membersFromParticipants(resp.participants),
    updatedAt: resp.updated_at,
  });
}

export function useSessionMembers() {
  const [isAdding, setIsAdding] = useState(false);
  const [isRemoving, setIsRemoving] = useState(false);

  /** 添加单个成员（不弹 toast，由调用方汇总） */
  const addOne = useCallback(
    async (sessionId: string, actorId: string): Promise<boolean> => {
      const resp = await BcnController.addSessionMember({
        session_id: sessionId,
        bot_uuid: actorId,
      });
      applyResponseToStore(sessionId, resp);
      return true;
    },
    [],
  );

  /** 添加单个成员（带 toast） */
  const addSessionMember = useCallback(
    async (sessionId: string, actorId: string): Promise<boolean> => {
      try {
        setIsAdding(true);
        await addOne(sessionId, actorId);
        toast.success('已添加成员');
        return true;
      } catch (error: any) {
        console.error('[useSessionMembers] addSessionMember failed:', error);
        toast.error(extractErrorMessage(error, '添加成员失败'));
        return false;
      } finally {
        setIsAdding(false);
      }
    },
    [addOne],
  );

  /** 批量添加（串行执行，汇总 toast） */
  const addSessionMembersBatch = useCallback(
    async (
      sessionId: string,
      items: AddSessionMemberItem[],
    ): Promise<AddBatchResult> => {
      if (items.length === 0) return { success: 0, failed: 0 };

      let success = 0;
      let failed = 0;

      try {
        setIsAdding(true);
        for (const item of items) {
          try {
            await addOne(sessionId, item.actorId);
            success += 1;
          } catch (error: any) {
            failed += 1;
            console.error(
              '[useSessionMembers] addSessionMembersBatch one failed:',
              item,
              error,
            );
          }
        }

        if (failed === 0) {
          toast.success(`已添加 ${success} 位成员`);
        } else if (success === 0) {
          toast.error(`添加失败 (${failed} 位)`);
        } else {
          toast.error(`部分添加失败：成功 ${success} 位,失败 ${failed} 位`);
        }
      } finally {
        setIsAdding(false);
      }

      return { success, failed };
    },
    [addOne],
  );

  /** 移除会话成员（DELETE） */
  const removeSessionMember = useCallback(
    async (sessionId: string, actorId: string): Promise<boolean> => {
      try {
        setIsRemoving(true);
        const resp = await BcnController.deleteSessionMember({
          session_id: sessionId,
          bot_uuid: actorId,
        });
        applyResponseToStore(sessionId, resp);
        toast.success('已移除成员');
        return true;
      } catch (error: any) {
        console.error('[useSessionMembers] removeSessionMember failed:', error);
        toast.error(extractErrorMessage(error, '移除成员失败'));
        return false;
      } finally {
        setIsRemoving(false);
      }
    },
    [],
  );

  return {
    addSessionMember,
    addSessionMembersBatch,
    removeSessionMember,
    isAdding,
    isRemoving,
  };
}
