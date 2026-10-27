import type {
  CommunityAttachment,
  CommunityReplyPage,
  CommunityReplyPager,
  CommunityTopic,
  CommunityTopicPage,
  CommunityTopicQuery,
  CreateCommunityReplyInput,
  CreateCommunityTopicInput,
} from '@/domain/community/types';

export interface GetCommunityTopicOptions {
  viewerAuthorId?: string;
  viewerAuthorType?: 'HUMAN' | 'BOT';
}

export interface CommunityGateway {
  listTopics(query?: CommunityTopicQuery, signal?: AbortSignal): Promise<CommunityTopicPage>;
  getTopic(
    topicId: string,
    options?: GetCommunityTopicOptions,
    signal?: AbortSignal,
  ): Promise<CommunityTopic>;
  listReplies(topicId: string, pager?: CommunityReplyPager, signal?: AbortSignal): Promise<CommunityReplyPage>;
  uploadAttachment(file: File, authorId: string, signal?: AbortSignal): Promise<CommunityAttachment>;
  downloadAttachment(attachmentId: string, signal?: AbortSignal): Promise<Blob>;
  createTopic(input: CreateCommunityTopicInput, signal?: AbortSignal): Promise<CommunityTopic>;
  createReply(input: CreateCommunityReplyInput, signal?: AbortSignal): Promise<string>;
  closeTopic(topicId: string, authorId: string, signal?: AbortSignal): Promise<void>;
}
