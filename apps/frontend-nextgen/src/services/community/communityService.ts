import type { CommunityReplyPager, CommunityTopicQuery, CreateCommunityTopicInput } from '@/domain/community/types';
import type { CommunityGateway } from './communityGateway';

function byNewestActivity(left: { latestActivityAt?: string }, right: { latestActivityAt?: string }) {
  return (right.latestActivityAt ?? '').localeCompare(left.latestActivityAt ?? '');
}

export class CommunityService {
  constructor(private readonly gateway: CommunityGateway) {}

  async listTopics(query: CommunityTopicQuery = {}, signal?: AbortSignal) {
    const normalized = {
      ...query,
      ...(query.search?.trim() ? { search: query.search.trim() } : { search: undefined }),
    };
    const page = await this.gateway.listTopics(normalized, signal);
    // “我的”走 openapi §2.4 author_id 真分页（controller 下发 author_id，后端过滤、total 为过滤后行数）；
    // 无需客户端 isMine 二次过滤。仅按最新活跃时间排序展示。
    return { total: page.total, items: [...page.items].sort(byNewestActivity) };
  }

  getTopic(topicId: string, signal?: AbortSignal) {
    return this.gateway.getTopic(topicId, signal);
  }

  async listReplies(topicId: string, pager: CommunityReplyPager = {}, signal?: AbortSignal) {
    const page = await this.gateway.listReplies(topicId, pager, signal);
    return {
      total: page.total,
      items: [...page.items].sort((left, right) => left.createdAt.localeCompare(right.createdAt)),
    };
  }

  createTopic(input: CreateCommunityTopicInput, signal?: AbortSignal) {
    const normalized = { ...input, title: input.title.trim(), body: input.body.trim() };
    if (!normalized.title) return Promise.reject(new Error('请输入主题标题'));
    if (!normalized.body) return Promise.reject(new Error('请输入主题正文'));
    return this.gateway.createTopic(normalized, signal);
  }

  closeTopic(topicId: string, authorId: string, signal?: AbortSignal) {
    return this.gateway.closeTopic(topicId, authorId, signal);
  }
}
