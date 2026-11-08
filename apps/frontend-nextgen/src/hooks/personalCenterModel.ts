import type {
  ForumActorRef,
  InteractionStats,
  PersonalCenterActivityPage,
  PersonalCenterActivityType,
  PersonalCenterActorScope,
  PersonalCenterTargetFilter,
} from '@/domain/community/types';

export interface PersonalCenterIdentityStats extends InteractionStats {
  actor: ForumActorRef;
}

export interface UsePersonalCenterResult {
  ownerUserId: string;
  botActors: ForumActorRef[];
  actorScope: PersonalCenterActorScope;
  activityType: PersonalCenterActivityType;
  targetType: PersonalCenterTargetFilter;
  page: number;
  pageSize: number;
  activityPage: PersonalCenterActivityPage;
  identityStats: PersonalCenterIdentityStats[];
  aggregateStats: InteractionStats;
  loading: boolean;
  loadingStats: boolean;
  subscriptionLoading: boolean;
  error: string | null;
  setActorScope: (scope: PersonalCenterActorScope) => void;
  setActivityType: (activityType: PersonalCenterActivityType) => void;
  setTargetType: (targetType: PersonalCenterTargetFilter) => void;
  goToPage: (page: number) => void;
  reload: () => void;
}

export const EMPTY_ACTIVITY_PAGE: PersonalCenterActivityPage = { items: [], total: 0 };

export const EMPTY_STATS: InteractionStats = {
  viewCount: 0,
  likeCount: 0,
  favoriteCount: 0,
  followCount: 0,
  acceptCount: 0,
  topicCount: 0,
  replyCount: 0,
  acceptedReplyCount: 0,
  replyParticipantCount: 0,
  helpfulReplyCount: 0,
};

export function aggregateStats(items: PersonalCenterIdentityStats[]): InteractionStats {
  return items.reduce<InteractionStats>(
    (result, item) => ({
      viewCount: result.viewCount + item.viewCount,
      likeCount: result.likeCount + item.likeCount,
      favoriteCount: result.favoriteCount + item.favoriteCount,
      followCount: result.followCount + item.followCount,
      acceptCount: result.acceptCount + item.acceptCount,
      topicCount: result.topicCount + item.topicCount,
      replyCount: result.replyCount + item.replyCount,
      acceptedReplyCount: result.acceptedReplyCount + item.acceptedReplyCount,
      replyParticipantCount: result.replyParticipantCount + item.replyParticipantCount,
      helpfulReplyCount: result.helpfulReplyCount + item.helpfulReplyCount,
    }),
    { ...EMPTY_STATS },
  );
}
