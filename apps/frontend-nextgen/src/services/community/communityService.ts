import type {
  CommunityReplyPager,
  CommunityTopicQuery,
  CreateCommunityReplyInput,
  CreateCommunityTopicInput,
} from '@/domain/community/types';
import type { CommunityGateway, GetCommunityTopicOptions } from './communityGateway';

export const MAX_COMMUNITY_POLL_OPTIONS = 10;

export class CommunityService {
  constructor(private readonly gateway: CommunityGateway) {}

  async listTopics(query: CommunityTopicQuery = {}, signal?: AbortSignal) {
    const normalized = {
      ...query,
      ...(query.search?.trim() ? { search: query.search.trim() } : { search: undefined }),
    };
    return this.gateway.listTopics(normalized, signal);
  }

  getTopic(topicId: string, options: GetCommunityTopicOptions = {}, signal?: AbortSignal) {
    return this.gateway.getTopic(topicId, options, signal);
  }

  listReplies(topicId: string, pager: CommunityReplyPager = {}, signal?: AbortSignal) {
    return this.gateway.listReplies(topicId, pager, signal);
  }

  async createTopic(input: CreateCommunityTopicInput, signal?: AbortSignal) {
    const normalized: CreateCommunityTopicInput = {
      ...input,
      title: input.title.trim(),
      body: input.body.trim(),
      help: input.help
        ? { expectedTime: input.help.expectedTime?.trim() || undefined }
        : undefined,
    };
    if (!normalized.title) throw new Error('请输入主题标题');
    if (!normalized.body) throw new Error('请输入主题正文');
    if (normalized.topicType === 'poll') {
      const options = normalized.poll?.options.map((option) => option.trim()) ?? [];
      if (!normalized.poll || options.length < 2 || options.some((option) => !option)) {
        throw new Error('投票主题至少需要两个有效选项');
      }
      if (options.length > MAX_COMMUNITY_POLL_OPTIONS) throw new Error('投票选项最多 10 个');
      if (new Set(options).size !== options.length) throw new Error('投票选项不能重复');
      normalized.poll = { ...normalized.poll, options };
    }
    if (normalized.topicType === 'help' && (normalized.help?.expectedTime?.length ?? 0) > 128) {
      throw new Error('期望解决时间不能超过 128 个字符');
    }
    return this.gateway.createTopic(normalized, signal);
  }

  createReply(input: CreateCommunityReplyInput, signal?: AbortSignal) {
    const normalized = { ...input, body: input.body.trim() };
    if (!normalized.body) return Promise.reject(new Error('请输入回复内容'));
    return this.gateway.createReply(normalized, signal);
  }

  setPostHelpful(
    topicId: string,
    postId: string,
    authorType: 'HUMAN' | 'BOT',
    authorId: string,
    helpful: boolean,
    signal?: AbortSignal,
  ) {
    return this.gateway.setPostHelpful(
      topicId,
      postId,
      authorType,
      authorId,
      helpful,
      signal,
    );
  }

  closeTopic(topicId: string, authorId: string, signal?: AbortSignal) {
    return this.gateway.closeTopic(topicId, authorId, signal);
  }
}
