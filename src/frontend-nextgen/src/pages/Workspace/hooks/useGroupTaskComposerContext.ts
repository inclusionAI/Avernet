import type { GroupView, IdentityView, SessionView } from '@/domain/collaboration';
import type { TaskComposerContext } from '@/services/tasks/taskMapper';
import { resolveUserId } from '@/services/workspace/botSessionService';
import { useMemo } from 'react';

export function useGroupTaskComposerContext(
  group: GroupView | null,
  session: SessionView | null,
  activeIdentity?: IdentityView | null,
) {
  return useMemo<TaskComposerContext | null>(() => {
    if (!group || !session || activeIdentity?.kind !== 'user') return null;
    const ownerBot = group.participants.find((p) => p.kind === 'bot');
    if (!ownerBot || !activeIdentity?.id) return null;
    return {
      sourceType: 'coop_group',
      ownerUserId: resolveUserId(activeIdentity.id),
      ownerBotId: ownerBot.actorId,
      mainSessionId: session.sessionId,
      mainSessionName: session.title,
      sourceGroupId: group.groupId,
      parentTaskId: null,
    };
  }, [group, session, activeIdentity]);
}
