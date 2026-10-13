import type {
  ParticipantRoutingStates,
  ServiceBotRoutingMode,
  ServiceBotStage,
} from '@/domain/collaboration/serviceBotRouting';
import { areParticipantRoutesReady, attachParticipantStageTags } from '@/domain/collaboration/serviceBotRouting';
import { queryIsServiceBot } from '@/services/workspace/botClassificationService';
import { useCallback, useEffect, useRef, useState } from 'react';

/** 状态属于建群弹窗；移除、重选、关闭以及卸载均使旧查询失效。 */
export function useServiceBotRouting(actorIds: string[], open: boolean) {
  const [states, setStates] = useState<ParticipantRoutingStates>({});
  const statesRef = useRef<ParticipantRoutingStates>({});
  const requests = useRef(new Map<string, symbol>());

  const update = useCallback((next: ParticipantRoutingStates) => {
    statesRef.current = next;
    setStates(next);
  }, []);

  const inspect = useCallback(
    async (actorId: string) => {
      const token = Symbol();
      requests.current.set(actorId, token);
      update({ ...statesRef.current, [actorId]: { status: 'loading' } });
      try {
        const isService = await queryIsServiceBot(actorId);
        if (requests.current.get(actorId) !== token) return;
        update({
          ...statesRef.current,
          [actorId]: isService ? { status: 'service', routingMode: 'dynamic' } : { status: 'non_service' },
        });
      } catch {
        if (requests.current.get(actorId) !== token) return;
        update({ ...statesRef.current, [actorId]: { status: 'error' } });
      }
    },
    [update],
  );

  const selectedKey = JSON.stringify(open ? Array.from(new Set(actorIds.filter(Boolean))) : []);
  useEffect(() => {
    const selected: string[] = JSON.parse(selectedKey);
    const next = { ...statesRef.current };
    for (const id of Object.keys(next)) {
      if (!selected.includes(id)) {
        requests.current.delete(id);
        delete next[id];
      }
    }
    update(next);
    selected.forEach((id) => {
      if (!statesRef.current[id]) void inspect(id);
    });
  }, [selectedKey, inspect, update]);

  useEffect(() => {
    const pending = requests.current;
    return () => {
      pending.clear();
      statesRef.current = {};
    };
  }, []);

  const selectRoutingMode = useCallback(
    (id: string, routingMode: ServiceBotRoutingMode) => {
      const current = statesRef.current[id];
      if (current?.status !== 'service' || current.routingMode === routingMode) return;
      update({ ...statesRef.current, [id]: { status: 'service', routingMode } });
    },
    [update],
  );

  const selectStage = useCallback(
    (id: string, stage: ServiceBotStage) => {
      const current = statesRef.current[id];
      if (current?.status !== 'service' || current.routingMode !== 'specified') return;
      update({ ...statesRef.current, [id]: { ...current, stage } });
    },
    [update],
  );

  return {
    states,
    ready: areParticipantRoutesReady(actorIds, states),
    selectRoutingMode,
    selectStage,
    retry: inspect,
    applyTags: <T extends { actor_id: string }>(participants: T[]) => attachParticipantStageTags(participants, states),
  };
}
