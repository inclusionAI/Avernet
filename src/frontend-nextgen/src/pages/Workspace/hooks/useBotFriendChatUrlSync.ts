import { serializeWorkspaceRoute } from '@/domain/workspaceRoute';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { history } from '@umijs/max';
import { useEffect } from 'react';

/** Bot→Human 只读会话 Store → URL 投影；human= 使用不带 human_ 前缀的好友用户 ID。 */
export function useBotFriendChatUrlSync(
  enabled: boolean,
  currentIdentityId: string | null,
  friendUserId: string | null,
  sessionId: string | null,
): void {
  useEffect(() => {
    if (!enabled) return;
    if (!sessionId && useWorkspaceStore.getState().selectedFriendUserSessionId) return;
    const next = serializeWorkspaceRoute({
      view: 'chat',
      currentIdentityId,
      targetHumanId: friendUserId,
      sessionId,
    });
    const current = window.location.search.replace(/^\?/, '');
    if (next !== current) history.replace(`${window.location.pathname}?${next}${window.location.hash}`);
  }, [currentIdentityId, enabled, friendUserId, sessionId]);
}
