import type {
  BotCatalogViewer,
  FriendRequestActor,
  HumanBotActionContext,
  SquareResource,
} from '@/domain/collaborationSquare/types';
import { useHumanIdentity, type HumanIdentityStatus } from '@/hooks/useHumanIdentity';
import { useSquareIdentity } from '@/hooks/useSquareIdentity';
import { useEffect, useMemo, useRef } from 'react';

interface CollaborationSquareActorContext {
  humanIdentityStatus: HumanIdentityStatus;
  humanBotContext: HumanBotActionContext | null;
  viewer: BotCatalogViewer | null;
  activeActor: FriendRequestActor | null;
}

/**
 * 发现菜单三 Tab 的操作身份来源（docs/specs/2026-09-23-discovery-tab-identity/spec.md）：
 * - bot：模块级工作身份（useSquareIdentity，默认登录用户身份，可选 Bot 身份）；
 * - group / task：固定登录用户身份，不以 Bot 身份操作；
 * 不读取全局 workspaceStore.activeIdentityId——发现菜单已与左上角全局身份脱钩。
 */
export function useCollaborationSquareActorContext(
  resource: SquareResource,
  resetSquare: () => void,
): CollaborationSquareActorContext {
  const { identity: humanIdentity, status: humanIdentityStatus } = useHumanIdentity();
  const { selectedIdentity, userIdentity } = useSquareIdentity();
  const identity = resource === 'bot' ? selectedIdentity : userIdentity;
  const humanBotContext = useMemo<HumanBotActionContext | null>(
    () => (identity && humanIdentity?.userId ? { actorId: identity.id, userId: humanIdentity.userId } : null),
    [identity, humanIdentity?.userId],
  );
  const viewer = useMemo<BotCatalogViewer | null>(
    () =>
      identity
        ? {
            viewerActorType: identity.kind === 'user' ? 'human' : 'bot',
            viewerActorId: identity.kind === 'user' ? humanIdentity?.userId ?? identity.id : identity.id,
          }
        : null,
    [identity, humanIdentity?.userId],
  );
  const activeActor = useMemo<FriendRequestActor | null>(
    () => (viewer ? { type: viewer.viewerActorType, id: viewer.viewerActorId } : null),
    [viewer],
  );
  const actorKey = activeActor ? `${activeActor.type}:${activeActor.id}` : 'none';
  const previousActorKey = useRef(actorKey);

  useEffect(() => {
    if (previousActorKey.current === actorKey) return;
    previousActorKey.current = actorKey;
    resetSquare();
  }, [actorKey, resetSquare]);

  return { humanIdentityStatus, humanBotContext, viewer, activeActor };
}
