import { communityErrorMessage } from '@/domain/community/errors';
import { MAX_INTERACTION_SUMMARY_BATCH } from '@/domain/community/constants';
import type {
  ForumActorRef,
  ForumTargetRef,
  InteractionActionType,
  InteractionSummary,
  InteractionSummaryQuery,
  InteractionActiveDelta,
  RecordInteractionInput,
} from '@/domain/community/types';
import { notifyError } from '@/components/ui/notify';
import { communityService } from '@/services/community';
import { useCallback, useMemo, useRef, useState } from 'react';

export interface UseCommunityInteractionsOptions {
  viewer: ForumActorRef;
}

export interface UseCommunityInteractionsResult {
  summaries: Record<string, InteractionSummary>;
  getSummary: (query: InteractionSummaryQuery) => InteractionSummary | undefined;
  refreshSummaries: (
    queries: InteractionSummaryQuery[],
    options?: { force?: boolean },
  ) => Promise<void>;
  recordInteraction: (
    actionType: InteractionActionType,
    target: ForumTargetRef,
    options?: { active?: InteractionActiveDelta; refresh?: boolean },
  ) => Promise<boolean>;
  isActionPending: (actionType: InteractionActionType, target: ForumTargetRef) => boolean;
}

function queryKey(query: InteractionSummaryQuery): string {
  const actor = query.actor ? `${query.actor.actorType}:${query.actor.id}` : '-';
  const target = query.target ? `${query.target.targetType}:${query.target.id}` : '-';
  return `${actor}|${target}`;
}

function chunkQueries(queries: InteractionSummaryQuery[]): InteractionSummaryQuery[][] {
  if (queries.length <= MAX_INTERACTION_SUMMARY_BATCH) return [queries];
  const chunks: InteractionSummaryQuery[][] = [];
  for (let index = 0; index < queries.length; index += MAX_INTERACTION_SUMMARY_BATCH) {
    chunks.push(queries.slice(index, index + MAX_INTERACTION_SUMMARY_BATCH));
  }
  return chunks;
}

export function useCommunityInteractions({
  viewer,
}: UseCommunityInteractionsOptions): UseCommunityInteractionsResult {
  const [summaries, setSummaries] = useState<Record<string, InteractionSummary>>({});
  const [pendingActions, setPendingActions] = useState<Record<string, boolean>>({});
  const summariesRef = useRef<Record<string, InteractionSummary>>({});
  const requestRef = useRef(0);

  const setSummaryMap = useCallback((updater: (current: Record<string, InteractionSummary>) => Record<string, InteractionSummary>) => {
    const next = updater(summariesRef.current);
    summariesRef.current = next;
    setSummaries(next);
  }, []);

  const getSummary = useCallback((query: InteractionSummaryQuery) => {
    return summariesRef.current[queryKey(query)];
  }, []);

  const refreshSummaries = useCallback(
    async (queries: InteractionSummaryQuery[], options?: { force?: boolean }) => {
      if (!queries.length) return;
      const unique = new Map<string, InteractionSummaryQuery>();
      queries.forEach((query) => unique.set(queryKey(query), query));
      const pending = [...unique.values()].filter(
        (query) => options?.force || summariesRef.current[queryKey(query)] === undefined,
      );
      if (!pending.length) return;

      const requestId = ++requestRef.current;
      try {
        for (const chunk of chunkQueries(pending)) {
          const results = await communityService.getInteractionSummaryBatch(chunk);
          if (requestId !== requestRef.current) return;
          setSummaryMap((current) => {
            const next = { ...current };
            chunk.forEach((query, index) => {
              const result = results[index];
              if (result) next[queryKey(query)] = result;
            });
            return next;
          });
        }
      } catch (error) {
        notifyError(communityErrorMessage(error, '互动统计加载失败'), { title: '互动统计' });
      }
    },
    [setSummaryMap],
  );

  const targetQueries = useCallback((target: ForumTargetRef): InteractionSummaryQuery[] => {
    const targetOnly = { target };
    const actorTarget = { actor: viewer, target };
    if (target.targetType === 'HUMAN' || target.targetType === 'BOT') {
      const actor: ForumActorRef = { actorType: target.targetType, id: target.id };
      return [targetOnly, { actor }, actorTarget];
    }
    return [targetOnly, actorTarget];
  }, [viewer]);

  const recordInteraction = useCallback(
    async (
      actionType: InteractionActionType,
      target: ForumTargetRef,
      options?: { active?: InteractionActiveDelta; refresh?: boolean },
    ) => {
      const actionKey = `${actionType}:${target.targetType}:${target.id}`;
      if (pendingActions[actionKey]) return false;
      setPendingActions((previous) => ({ ...previous, [actionKey]: true }));
      try {
        const input: RecordInteractionInput = {
          actor: viewer,
          actionType,
          target,
          active: options?.active ?? 1,
        };
        await communityService.createInteraction(input);
        if (options?.refresh !== false) {
          await refreshSummaries(targetQueries(target), { force: true });
        }
        return true;
      } catch (error) {
        notifyError(communityErrorMessage(error, '互动记录失败'), { title: '互动失败' });
        return false;
      } finally {
        setPendingActions((previous) => {
          const next = { ...previous };
          delete next[actionKey];
          return next;
        });
      }
    },
    [pendingActions, refreshSummaries, targetQueries, viewer],
  );

  const isActionPending = useCallback(
    (actionType: InteractionActionType, target: ForumTargetRef) =>
      pendingActions[`${actionType}:${target.targetType}:${target.id}`] === true,
    [pendingActions],
  );

  return useMemo(
    () => ({ summaries, getSummary, refreshSummaries, recordInteraction, isActionPending }),
    [getSummary, isActionPending, recordInteraction, refreshSummaries, summaries],
  );
}

export { queryKey as interactionQueryKey };
