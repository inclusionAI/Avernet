import { MAX_INTERACTION_SUMMARY_BATCH } from '@/domain/community/constants';
import type {
  CommunityReplyPager,
  CommunityTopicQuery,
  CreateCommunityReplyInput,
  CreateCommunityTopicInput,
  InteractionSummary,
  UpdateCommunityTopicInput,
  InteractionSummaryQuery,
  PersonalCenterActivityPage,
  PersonalCenterActivityQuery,
  RecordInteractionInput,
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
    return this.gateway.createTopic(normalized, signal);
  }

  createReply(input: CreateCommunityReplyInput, signal?: AbortSignal) {
    const normalized = { ...input, body: input.body.trim(), replyToPostId: input.replyToPostId?.trim() || undefined };
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

  async updateTopic(input: UpdateCommunityTopicInput, signal?: AbortSignal) {
    const normalized = { ...input, title: input.title.trim(), body: input.body.trim() };
    if (!normalized.title) throw new Error('请输入主题标题');
    if (!normalized.body) throw new Error('请输入主题正文');
    if (!normalized.authorId.trim()) throw new Error('缺少当前用户身份');
    return this.gateway.updateTopic(normalized, signal);
  }

  deleteTopic(topicId: string, authorId: string, signal?: AbortSignal) {
    if (!authorId.trim()) return Promise.reject(new Error('缺少当前用户身份'));
    return this.gateway.deleteTopic(topicId, authorId, signal);
  }

  closeTopic(topicId: string, authorId: string, signal?: AbortSignal) {
    return this.gateway.closeTopic(topicId, authorId, signal);
  }

  async createInteraction(input: RecordInteractionInput, signal?: AbortSignal) {
    if (!input.actor.id.trim() || !input.target.id.trim()) {
      throw new Error('互动主体或目标不能为空');
    }
    await this.gateway.createInteraction(input, signal);
  }

  getInteractionSummary(query: InteractionSummaryQuery, signal?: AbortSignal) {
    if (!query.actor && !query.target) throw new Error('缺少互动统计维度');
    return this.gateway.getInteractionSummary(query, signal);
  }

  getInteractionSummaryBatch(
    queries: InteractionSummaryQuery[],
    signal?: AbortSignal,
  ): Promise<InteractionSummary[]> {
    if (!queries.length) throw new Error('互动统计批次不能为空');
    if (queries.length > MAX_INTERACTION_SUMMARY_BATCH) {
      throw new Error(`互动统计每次最多 ${MAX_INTERACTION_SUMMARY_BATCH} 项`);
    }
    return this.gateway.getInteractionSummaryBatch(queries, signal);
  }

  getPersonalCenterActivities(
    query: PersonalCenterActivityQuery,
    signal?: AbortSignal,
  ): Promise<PersonalCenterActivityPage> {
    if (!query.ownerUserId.trim()) throw new Error('缺少当前用户身份');
    return this.gateway.getPersonalCenterActivities(query, signal);
  }

  listBrowseSubscriptions(ownerUserId: string, signal?: AbortSignal) {
    if (!ownerUserId.trim()) throw new Error('缺少当前用户身份');
    return this.gateway.listBrowseSubscriptions(ownerUserId, signal);
  }
}
