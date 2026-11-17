import type { MessageViewScope } from '@/domain/collaboration/types';
import { useInvitationHumanIdentity, type InvitationHumanIdentityStatus } from './useInvitationHumanIdentity';
import { invitationService } from '@/services/workspace/invitationService';
import { sessionService } from '@/services/workspace/sessionService';
import { useCallback, useEffect, useRef, useState } from 'react';

export interface InviteAcceptState {
  status: 'loading' | 'confirm' | 'invalid' | 'preview-error' | 'accepting' | 'accepted' | 'error';
  /** invalid/error 时的用户可读文案。 */
  friendlyMessage?: string;
  /** 预检服务暂时失败可重试；认证失败不能退回未校验的确认页。 */
  canRetry?: boolean;
  /** 邀请目标类型（409 alreadyJoined 无 target 信息时为空）。 */
  targetType?: 'group' | 'session' | null;
  /** 邀请目标 ID：group 时为群 ID，session 时为会话 ID。 */
  targetId?: string;
  /** session 邀请解析出的所属群 ID，用于跨群跳转。 */
  groupId?: string;
  /** session 邀请的目标会话 ID。 */
  sessionId?: string;
  /** 是否已加入目标群/会话；409 兼容分支可能没有目标信息。 */
  alreadyJoined?: boolean;
}

/**
 * 邀请落地页 action 层：只读预检 → 已是成员则 accepted，否则 confirm → 显式接受。
 * 预检不调用 acceptInvitation，不通过「能读详情」推断 Human 成员身份。
 * 导航由组件处理；token 变化/卸载后，过期预检和接受结果不能覆盖当前邀请。
 */
export function useInviteAccept(token: string): InviteAcceptState & {
  accept: (scope?: MessageViewScope) => Promise<void>;
  resetToConfirm: () => void;
  retryPreview: () => void;
  currentIdentityId?: string;
  humanIdentityStatus: InvitationHumanIdentityStatus;
  humanIdentityError?: string;
} {
  const human = useInvitationHumanIdentity();
  const humanUserId = human.userId ?? '';
  const requestKey = JSON.stringify([token, humanUserId]);
  const [result, setResult] = useState<{ key: string; state: InviteAcceptState }>({
    key: requestKey,
    state: { status: 'loading' },
  });
  const [previewAttempt, setPreviewAttempt] = useState(0);
  const requestVersion = useRef(0);
  const verifiedTarget = useRef<{ key: string; targetType: 'group' | 'session' } | null>(null);
  const setState = useCallback((state: InviteAcceptState) => setResult({ key: requestKey, state }), [requestKey]);

  useEffect(() => {
    const version = ++requestVersion.current;
    setState({ status: 'loading' });
    void (async () => {
      const res = await invitationService.getAcceptPageState(token);
      if (version !== requestVersion.current) return;
      if (!res.ok) {
        setState({
          status: res.error.code === 'INVITATION_INVALID' ? 'invalid' : 'preview-error',
          friendlyMessage: res.error.friendlyMessage,
          canRetry: res.error.canRetry,
        });
        return;
      }
      const { targetType, targetId, groupId, alreadyJoined } = res.data;
      verifiedTarget.current = { key: requestKey, targetType };
      setState({
        status: alreadyJoined ? 'accepted' : 'confirm',
        targetType,
        targetId,
        groupId,
        sessionId: targetType === 'session' ? targetId : undefined,
        alreadyJoined,
      });
    })();
    return () => {
      requestVersion.current++;
    };
  }, [token, requestKey, previewAttempt, setState]);

  const accept = useCallback(
    async (scope?: MessageViewScope) => {
      const version = ++requestVersion.current;
      setState({ status: 'accepting' });
      const res = await invitationService.acceptInvitation(token, scope);
      if (version !== requestVersion.current) return;
      if (!res.ok) {
        setState({ status: 'error', friendlyMessage: res.error.friendlyMessage });
        return;
      }
      const { targetType, targetId, alreadyJoined } = res.data;
      if (targetType === 'session' && targetId) {
        const session = await sessionService.getSessionDetail(targetId);
        if (version !== requestVersion.current) return;
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
    },
    [token, setState],
  );

  const resetToConfirm = useCallback(() => setState({ status: 'confirm' }), [setState]);
  const retryPreview = useCallback(() => setPreviewAttempt((attempt) => attempt + 1), []);
  // 新 token / Human 的 effect 尚未执行时，也不能用旧成员判断触发导航。
  const state: InviteAcceptState = result.key === requestKey ? result.state : { status: 'loading' };
  return {
    ...state, accept, resetToConfirm, retryPreview,
    targetType: state.targetType === undefined && verifiedTarget.current?.key === requestKey
      ? verifiedTarget.current.targetType : state.targetType,
    // 邀请加入的是登录 Human，与用户在工作区选中的 Bot 无关。
    currentIdentityId: humanUserId ? `human_${humanUserId}` : undefined,
    humanIdentityStatus: human.status,
    humanIdentityError: human.error,
  };
}
