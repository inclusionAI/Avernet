import { invitationService } from '@/services/workspace/invitationService';
import { sessionService } from '@/services/workspace/sessionService';
import { useCallback, useEffect, useState } from 'react';

export interface InviteAcceptState {
  status: 'loading' | 'confirm' | 'invalid' | 'accepting' | 'accepted' | 'error';
  /** invalid/error 时的用户可读文案。 */
  friendlyMessage?: string;
  /** 邀请目标类型（409 alreadyJoined 无 target 信息时为空）。 */
  targetType?: 'group' | 'session' | null;
  /** 邀请目标 ID：group 时为群 ID，session 时为会话 ID。 */
  targetId?: string;
  /** session 邀请解析出的所属群 ID，用于跨群跳转。 */
  groupId?: string;
  /** session 邀请的目标会话 ID。 */
  sessionId?: string;
  /** 是否「已加入」状态（409 走成功路径，但 groupId 为空）。 */
  alreadyJoined?: boolean;
}

/**
 * useInviteAccept —— 邀请接受落地页的 action 层。
 *
 * 组件只做 view（解析状态、展示 join/invalid）；本 Hook 拥有 `invitationService` 调用，
 * 把"预检 → 确认 → 接受 → 跳转"状态推进收敛到 Hook，组件不直接 import Service。
 *
 * 状态机：
 * - mount → loading（调 getAcceptPageState 校验 token）
 * - 校验通过 → confirm
 * - 校验失败 → invalid（friendlyMessage 来自 service error）
 * - accept() → accepting → accepted（携带 targetType/targetId/groupId/sessionId） 或 error
 *
 * 不含导航副作用——把 `accepted` 状态与 groupId/alreadyJoined 交给组件决定跳转目标，
 * 让组件保持纯 view。
 */
export function useInviteAccept(token: string): InviteAcceptState & {
  accept: () => Promise<void>;
  resetToConfirm: () => void;
} {
  const [state, setState] = useState<InviteAcceptState>({ status: 'loading' });

  useEffect(() => {
    let cancelled = false;
    setState({ status: 'loading' });
    void (async () => {
      const res = await invitationService.getAcceptPageState(token);
      if (cancelled) return;
      if (!res.ok) {
        setState({ status: 'invalid', friendlyMessage: res.error.friendlyMessage });
        return;
      }
      if (!res.data?.isValid) {
        setState({ status: 'invalid', friendlyMessage: '该邀请已失效，请让群主重新生成。' });
        return;
      }
      setState({ status: 'confirm' });
    })();
    return () => {
      cancelled = true;
    };
  }, [token]);

  const accept = useCallback(async () => {
    setState({ status: 'accepting' });
    const res = await invitationService.acceptInvitation(token);
    if (!res.ok) {
      setState({ status: 'error', friendlyMessage: res.error.friendlyMessage });
      return;
    }
    const { targetType, targetId, alreadyJoined } = res.data;
    if (targetType === 'session' && targetId) {
      const session = await sessionService.getSessionDetail(targetId);
      if (session.ok) {
        setState({
          status: 'accepted',
          targetType,
          targetId,
          groupId: session.data.groupId,
          sessionId: targetId,
          alreadyJoined,
        });
        return;
      }
    }
    setState({
      status: 'accepted',
      targetType,
      targetId,
      groupId: targetType === 'group' ? targetId : undefined,
      sessionId: targetType === 'session' ? targetId : undefined,
      alreadyJoined,
    });
  }, [token]);

  const resetToConfirm = useCallback(() => {
    setState({ status: 'confirm' });
  }, []);

  return { ...state, accept, resetToConfirm };
}
