import { sessionInitiatorId, type SessionInitiator } from '@/domain/conversation/sessionInitiator';
import { useHumanIdentity } from '@/hooks/useHumanIdentity';
import { loadSessionInitiators } from '@/services/workspace/sessionInitiatorService';
import { useEffect, useState } from 'react';

export function useSessionInitiators(sessionIds: string[], enabled = true) {
  const { identity } = useHumanIdentity();
  const viewerId = identity?.userId ?? '';
  const idsKey = JSON.stringify(
    enabled ? [...new Set(sessionIds.map(sessionInitiatorId).filter((id): id is string => !!id))].sort() : [],
  );
  const key = JSON.stringify([viewerId, idsKey]);
  const [state, setState] = useState<{ key: string; profiles: Record<string, SessionInitiator> }>({
    key: '',
    profiles: {},
  });
  useEffect(() => {
    if (!viewerId || idsKey === '[]') return;
    let active = true;
    void loadSessionInitiators(viewerId, JSON.parse(idsKey) as string[]).then((profiles) => {
      if (active) setState({ key, profiles });
    });
    return () => {
      active = false;
    };
  }, [viewerId, idsKey, key]);
  return (sessionId: string): SessionInitiator | undefined => {
    if (!enabled) return undefined;
    const userId = sessionInitiatorId(sessionId);
    return userId ? (state.key === key ? state.profiles[userId] : undefined) ?? { userId } : undefined;
  };
}
