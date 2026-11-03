import type {
  CommunityReplyPage,
  CommunityReplyPager,
  CommunityTopic,
  CommunityTopicPage,
  CommunityTopicQuery,
  CreateCommunityReplyInput,
  CreateCommunityTopicInput,
  UpdateCommunityTopicInput,
  InteractionSummary,
  InteractionSummaryQuery,
  PersonalCenterActivityPage,
  PersonalCenterActivityQuery,
  RecordInteractionInput,
} from '@/domain/community/types';

export interface GetCommunityTopicOptions {
  viewerAuthorId?: string;
  viewerAuthorType?: 'HUMAN' | 'BOT';
}

/**
 * Stable identities shown in Personal Center. This list is derived only from
 * the BCS-style browse-subscription source, never from a Bot ownership list.
 */
export type CommunitySubscriptionBotId = string;

export interface CommunityGateway {
  listTopics(query?: CommunityTopicQuery, signal?: AbortSignal): Promise<CommunityTopicPage>;
  getTopic(
    topicId: string,
    options?: GetCommunityTopicOptions,
    signal?: AbortSignal,
  ): Promise<CommunityTopic>;
  listReplies(topicId: string, pager?: CommunityReplyPager, signal?: AbortSignal): Promise<CommunityReplyPage>;
  createTopic(input: CreateCommunityTopicInput, signal?: AbortSignal): Promise<CommunityTopic>;
  createReply(input: CreateCommunityReplyInput, signal?: AbortSignal): Promise<string>;
  updateTopic(input: UpdateCommunityTopicInput, signal?: AbortSignal): Promise<CommunityTopic>;
  deleteTopic(topicId: string, authorId: string, signal?: AbortSignal): Promise<void>;
  setPostHelpful(
    topicId: string,
    postId: string,
    authorType: 'HUMAN' | 'BOT',
    authorId: string,
    helpful: boolean,
    signal?: AbortSignal,
  ): Promise<void>;
  createInteraction(input: RecordInteractionInput, signal?: AbortSignal): Promise<void>;
  getInteractionSummary(query: InteractionSummaryQuery, signal?: AbortSignal): Promise<InteractionSummary>;
  getInteractionSummaryBatch(
    queries: InteractionSummaryQuery[],
    signal?: AbortSignal,
  ): Promise<InteractionSummary[]>;
  getPersonalCenterActivities(
    query: PersonalCenterActivityQuery,
    signal?: AbortSignal,
  ): Promise<PersonalCenterActivityPage>;
  listBrowseSubscriptions(
    ownerUserId: string,
    signal?: AbortSignal,
  ): Promise<CommunitySubscriptionBotId[]>;
  closeTopic(topicId: string, authorId: string, signal?: AbortSignal): Promise<void>;
}
