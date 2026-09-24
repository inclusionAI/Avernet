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
    const fallbackOwnerBot =
      group.participants.find((p) => p.kind === 'bot' && (p.role === 'driver' || p.role === 'manager')) ??
      group.participants.find((p) => p.kind === 'bot');
    const ownerBotId = group.driverBotUuid?.trim() || fallbackOwnerBot?.actorId;
    if (!ownerBotId || !activeIdentity?.id) return null;
    return {
      sourceType: 'coop_group',
      ownerUserId: resolveUserId(activeIdentity.id),
      ownerBotId,
      mainSessionId: session.sessionId,
      mainSessionName: session.title,
      sourceGroupId: group.groupId,
      parentTaskId: null,
    };
  }, [group, session, activeIdentity]);
}
