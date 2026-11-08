import { communityErrorMessage } from '@/domain/community/errors';
import { PERSONAL_CENTER_PAGE_SIZE } from '@/domain/community/constants';
import type {
  ForumActorRef,
  PersonalCenterActivityPage,
  PersonalCenterActivityType,
  PersonalCenterActorScope,
  PersonalCenterTargetFilter,
} from '@/domain/community/types';
import {
  EMPTY_ACTIVITY_PAGE,
  EMPTY_STATS,
  aggregateStats,
  type PersonalCenterIdentityStats,
  type UsePersonalCenterResult,
} from './personalCenterModel';
export type { PersonalCenterIdentityStats, UsePersonalCenterResult } from './personalCenterModel';
import { useHumanIdentity } from '@/hooks/useHumanIdentity';
import { communityService } from '@/services/community';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

export function usePersonalCenter(): UsePersonalCenterResult {
  const { identity } = useHumanIdentity();
  const ownerUserId = identity?.userId.trim() ?? '';

  const [botActors, setBotActors] = useState<ForumActorRef[]>([]);
  const [actorScope, setActorScopeState] = useState<PersonalCenterActorScope>('ALL');
  const [activityType, setActivityTypeState] = useState<PersonalCenterActivityType>('AUTHORED');
  const [targetType, setTargetTypeState] = useState<PersonalCenterTargetFilter>('ALL');
  const [page, setPage] = useState(1);
  const [activityPage, setActivityPage] = useState<PersonalCenterActivityPage>(EMPTY_ACTIVITY_PAGE);
  const [identityStats, setIdentityStats] = useState<PersonalCenterIdentityStats[]>([]);
  const [loading, setLoading] = useState(false);
  const [loadingStats, setLoadingStats] = useState(false);
  const [subscriptionLoading, setSubscriptionLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadNonce, setReloadNonce] = useState(0);
  const activityRequestRef = useRef(0);
  const statsRequestRef = useRef(0);

  useEffect(() => {
    if (!ownerUserId) return;
    let cancelled = false;
    setError(null);
    setSubscriptionLoading(true);
    communityService
      .listBrowseSubscriptions(ownerUserId)
      .then((botIds) => {
        if (!cancelled) {
          setBotActors(botIds.map((id) => ({ actorType: 'BOT' as const, id })));
        }
      })
      .catch((loadError: unknown) => {
        if (!cancelled) {
          setError(communityErrorMessage(loadError, 'Bot 订阅加载失败，请稍后重试'));
          setBotActors([]);
        }
      })
      .finally(() => {
        if (!cancelled) setSubscriptionLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [ownerUserId, reloadNonce]);

  const humanActor = useMemo<ForumActorRef | null>(
    () => (ownerUserId ? { actorType: 'HUMAN', id: ownerUserId } : null),
    [ownerUserId],
  );

  const actors = useMemo<ForumActorRef[]>(() => {
    if (actorScope === 'HUMAN') return humanActor ? [humanActor] : [];
    if (actorScope === 'BOT') return botActors;
    return humanActor ? [humanActor, ...botActors] : botActors;
  }, [actorScope, botActors, humanActor]);

  useEffect(() => {
    const statsReady = actorScope === 'HUMAN' || !subscriptionLoading;
    if (!ownerUserId || !statsReady) {
      setIdentityStats([]);
      return;
    }
    if (!actors.length) {
      setIdentityStats([]);
      return;
    }
    const requestId = ++statsRequestRef.current;
    setLoadingStats(true);
    setError(null);
    void communityService
      .getInteractionSummaryBatch(actors.map((actor) => ({ actor })))
      .then((results) => {
        if (requestId !== statsRequestRef.current) return;
        setIdentityStats(
          actors.map((actor, index) => {
            const stats = results[index];
            return {
              actor,
              ...(stats ? {
                viewCount: stats.viewCount,
                likeCount: stats.likeCount,
                favoriteCount: stats.favoriteCount,
                followCount: stats.followCount,
                acceptCount: stats.acceptCount,
                topicCount: stats.topicCount,
                replyCount: stats.replyCount,
                acceptedReplyCount: stats.acceptedReplyCount,
                replyParticipantCount: stats.replyParticipantCount,
                helpfulReplyCount: stats.helpfulReplyCount,
              } : EMPTY_STATS),
            };
          }),
        );
      })
      .catch((statsError: unknown) => {
        if (requestId !== statsRequestRef.current) return;
        setError(communityErrorMessage(statsError, '互动统计加载失败，请稍后重试'));
      })
      .finally(() => {
        if (requestId === statsRequestRef.current) setLoadingStats(false);
      });
  }, [actorScope, actors, ownerUserId, reloadNonce, subscriptionLoading]);

  useEffect(() => {
    if (!ownerUserId) return;
    const requestId = ++activityRequestRef.current;
    setLoading(true);
    setError(null);
    void communityService
      .getPersonalCenterActivities({
        ownerUserId,
        actorScope,
        activityType,
        targetType,
        page,
        pageSize: PERSONAL_CENTER_PAGE_SIZE,
      })
      .then((result) => {
        if (requestId !== activityRequestRef.current) return;
        setActivityPage(result);
      })
      .catch((activityError: unknown) => {
        if (requestId !== activityRequestRef.current) return;
        setError(communityErrorMessage(activityError, '个人中心活动加载失败，请稍后重试'));
        setActivityPage(EMPTY_ACTIVITY_PAGE);
      })
      .finally(() => {
        if (requestId === activityRequestRef.current) setLoading(false);
      });
  }, [
    activityType,
    actorScope,
    ownerUserId,
    page,
    reloadNonce,
    targetType,
  ]);

  const setActorScope = useCallback((scope: PersonalCenterActorScope) => {
    setActorScopeState(scope);
    setPage(1);
  }, []);

  const setActivityType = useCallback((type: PersonalCenterActivityType) => {
    setActivityTypeState(type);
    setPage(1);
  }, []);

  const setTargetType = useCallback((type: PersonalCenterTargetFilter) => {
    setTargetTypeState(type);
    setPage(1);
  }, []);

  const goToPage = useCallback((nextPage: number) => {
    setPage(nextPage);
  }, []);

  const reload = useCallback(() => {
    setReloadNonce((nonce) => nonce + 1);
  }, []);

  return {
    ownerUserId,
    botActors,
    actorScope,
    activityType,
    targetType,
    page,
    pageSize: PERSONAL_CENTER_PAGE_SIZE,
    activityPage,
    identityStats,
    aggregateStats: aggregateStats(identityStats),
    loading,
    loadingStats,
    subscriptionLoading,
    error,
    setActorScope,
    setActivityType,
    setTargetType,
    goToPage,
    reload,
  };
}
