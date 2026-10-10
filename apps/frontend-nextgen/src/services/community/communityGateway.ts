import type {
  CommunityReplyPage,
  CommunityReplyPager,
  CommunityTopic,
  CommunityTopicPage,
  CommunityTopicQuery,
  CreateCommunityTopicInput,
} from '@/domain/community/types';

export interface CommunityGateway {
  listTopics(query?: CommunityTopicQuery, signal?: AbortSignal): Promise<CommunityTopicPage>;
  getTopic(topicId: string, signal?: AbortSignal): Promise<CommunityTopic>;
  listReplies(topicId: string, pager?: CommunityReplyPager, signal?: AbortSignal): Promise<CommunityReplyPage>;
  createTopic(input: CreateCommunityTopicInput, signal?: AbortSignal): Promise<CommunityTopic>;
  closeTopic(topicId: string, authorId: string, signal?: AbortSignal): Promise<void>;
}
