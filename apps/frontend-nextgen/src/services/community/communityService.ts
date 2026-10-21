import type {
  CommunityReplyPager,
  CommunityTopicQuery,
  CreateCommunityReplyInput,
  CreateCommunityTopicInput,
} from '@/domain/community/types';
import type { CommunityGateway, GetCommunityTopicOptions } from './communityGateway';

export class CommunityService {
  constructor(private readonly gateway: CommunityGateway) {}

  async listTopics(query: CommunityTopicQuery = {}, signal?: AbortSignal) {
    const normalized = {
      ...query,
      ...(query.search?.trim() ? { search: query.search.trim() } : { search: undefined }),
    };
    const page = await this.gateway.listTopics(normalized, signal);
    // “我的”走 openapi §2.4 author_id 真分页（controller 下发 author_id，后端过滤，total 为过滤后行数）。
    // 排序由服务端统一完成，客户端不再对服务端分页结果二次排序。
    return page;
  }

  getTopic(
    topicId: string,
    options: GetCommunityTopicOptions = {},
    signal?: AbortSignal,
  ) {
    return this.gateway.getTopic(topicId, options, signal);
  }

  async listReplies(topicId: string, pager: CommunityReplyPager = {}, signal?: AbortSignal) {
    const page = await this.gateway.listReplies(topicId, pager, signal);
    return {
      total: page.total,
      items: [...page.items].sort((left, right) => left.createdAt.localeCompare(right.createdAt)),
    };
  }

  async createTopic(input: CreateCommunityTopicInput, signal?: AbortSignal) {
    const normalized = { ...input, title: input.title.trim(), body: input.body.trim() };
    if (!normalized.title) throw new Error('请输入主题标题');
    if (!normalized.body) throw new Error('请输入主题正文');
    if (normalized.topicType === 'poll') {
      const options = normalized.poll?.options.map((option) => option.trim()) ?? [];
      if (!normalized.poll || options.length < 2 || options.some((option) => !option)) {
        throw new Error('投票主题至少需要两个有效选项');
      }
      if (new Set(options).size !== options.length) throw new Error('投票选项不能重复');
    }
    return this.gateway.createTopic(normalized, signal);
  }

  createReply(input: CreateCommunityReplyInput, signal?: AbortSignal) {
    const normalized = { ...input, body: input.body.trim() };
    if (!normalized.body) return Promise.reject(new Error('请输入回复内容'));
    return this.gateway.createReply(normalized, signal);
  }

  closeTopic(topicId: string, authorId: string, signal?: AbortSignal) {
    return this.gateway.closeTopic(topicId, authorId, signal);
  }
}
