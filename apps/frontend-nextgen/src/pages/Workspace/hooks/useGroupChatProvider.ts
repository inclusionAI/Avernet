import type { SessionView } from '@/domain/collaboration';
import { isSameHumanIdentity } from '@/domain/userIdentity';
import { createGroupChatProvider } from '@/services/workspace/groupChatProvider';
import { resolveGroupWsOrigin } from '@/services/workspace/groupChatProviderHelpers';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { useLayoutEffect, useMemo } from 'react';

/** 连接权限属于登录 Human，与当前选择的查看身份无关。 */
export function useGroupChatProvider(
  session: SessionView | null,
  identityId: string | null,
  authenticatedUserId?: string | null,
) {
  const storedHumanId = useWorkspaceStore(
    (s) => s.identities.find((identity) => identity.kind === 'user' && !identity.id.startsWith('test-'))?.id,
  );
  const humanId = authenticatedUserId ?? storedHumanId;
  const human = session?.participants.find(
    (participant) => participant.kind === 'human' && isSameHumanIdentity(participant.actorId, humanId, 'human'),
  );
  // 列表投影不含成员详情；详情回来之前只加载 HTTP 历史。
  // 详情中没有该 Human 不等于 absent，保留后端经自有 Bot 授权的语义。
  const liveEnabled = !!humanId && !!session?.participants.length && human?.mode !== 'absent';
  const sessionId = session?.sessionId;
  const groupId = session?.groupId;
  const provider = useMemo(() => {
    if (!sessionId || !groupId || !identityId) return null;
    return createGroupChatProvider({ sessionId, groupId, identityId, liveEnabled, wsOrigin: resolveGroupWsOrigin() });
  }, [sessionId, groupId, identityId, liveEnabled]);

  // 在 passive effects/异步续体之前关闭旧实例；旧引用（包括桥和重连计时器）不能恢复连接。
  useLayoutEffect(() => {
    provider?.setLiveEnabled(liveEnabled);
    return () => provider?.setLiveEnabled(false);
  }, [provider, liveEnabled]);
  return provider;
}
