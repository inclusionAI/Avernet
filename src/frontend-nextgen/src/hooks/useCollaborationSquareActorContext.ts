import type { BotCatalogViewer, FriendRequestActor, HumanBotActionContext } from '@/domain/collaborationSquare/types';
import { useHumanIdentity, type HumanIdentityStatus } from '@/hooks/useHumanIdentity';
import { useWorkIdentityAccess } from '@/hooks/useWorkIdentityAccess';
import { useEffect, useMemo, useRef } from 'react';

interface CollaborationSquareActorContext {
  humanIdentityStatus: HumanIdentityStatus;
  humanBotContext: HumanBotActionContext | null;
  viewer: BotCatalogViewer | null;
  activeActor: FriendRequestActor | null;
}

export function useCollaborationSquareActorContext(resetSquare: () => void): CollaborationSquareActorContext {
  const { identity: humanIdentity, status: humanIdentityStatus } = useHumanIdentity();
  const { activeIdentity } = useWorkIdentityAccess();
  const humanBotContext = useMemo<HumanBotActionContext | null>(
    () =>
      activeIdentity && humanIdentity?.userId ? { actorId: activeIdentity.id, userId: humanIdentity.userId } : null,
    [activeIdentity, humanIdentity?.userId],
  );
  const viewer = useMemo<BotCatalogViewer | null>(
    () =>
      activeIdentity
        ? {
            viewerActorType: activeIdentity.kind === 'user' ? 'human' : 'bot',
            viewerActorId:
              activeIdentity.kind === 'user' ? humanIdentity?.userId ?? activeIdentity.id : activeIdentity.id,
          }
        : null,
    [activeIdentity, humanIdentity?.userId],
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
