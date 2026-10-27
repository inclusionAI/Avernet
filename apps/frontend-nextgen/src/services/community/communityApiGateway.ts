import {
  mapCommunityReplyDto,
  mapCommunityTopicDto,
} from '@/domain/community/mapper';
import type {
  CommunityReplyPager,
  CommunityTopicQuery,
  CreateCommunityReplyInput,
  CreateCommunityTopicInput,
} from '@/domain/community/types';
import {
  closeCommunityTopic,
  createCommunityReply,
  createCommunityTopic,
  getCommunityTopic,
  listCommunityReplies,
  listCommunityTopics,
  setCommunityPostHelpful,
} from '@/services/backendApi/community/communityController';
import { isEnvelopeSuccess } from '@/services/backendApi/types';
import type { CommunityGateway, GetCommunityTopicOptions } from './communityGateway';

function requireData<T>(response: { code?: string | number; success?: boolean; message?: string; data?: T }): T {
  if (!isEnvelopeSuccess(response) || response.data === undefined) {
    throw new Error(response.message || '社区服务暂时不可用');
  }
  return response.data;
}

export class CommunityApiGateway implements CommunityGateway {
  async listTopics(query?: CommunityTopicQuery, signal?: AbortSignal) {
    const data = requireData(await listCommunityTopics(query, signal));
    return { total: data.total, items: data.items.map(mapCommunityTopicDto).filter((item) => item.id) };
  }

  async getTopic(
    topicId: string,
    options: GetCommunityTopicOptions = {},
    signal?: AbortSignal,
  ) {
    return mapCommunityTopicDto(requireData(await getCommunityTopic(topicId, options, signal)));
  }

  async listReplies(topicId: string, pager?: CommunityReplyPager, signal?: AbortSignal) {
    const page = requireData(await listCommunityReplies(topicId, pager, signal));
    return { total: page.total, items: (page.items ?? []).map(mapCommunityReplyDto).filter((item) => item.id) };
  }

  async createTopic(input: CreateCommunityTopicInput, signal?: AbortSignal) {
    const created = requireData(await createCommunityTopic(input, signal));
    return this.getTopic(created.topic_id, undefined, signal);
  }

  async createReply(input: CreateCommunityReplyInput, signal?: AbortSignal) {
    const created = requireData(await createCommunityReply(input, signal));
    if (!created.post_id) throw new Error('回复发布失败');
    return created.post_id;
  }

  async setPostHelpful(
    topicId: string,
    postId: string,
    authorType: 'HUMAN' | 'BOT',
    authorId: string,
    helpful: boolean,
    signal?: AbortSignal,
  ) {
    const updated = requireData(
      await setCommunityPostHelpful(topicId, postId, authorType, authorId, helpful, signal),
    );
    if (updated.post_id !== postId || updated.helpful !== helpful) {
      throw new Error('有帮助状态更新结果不一致');
    }
  }

  async closeTopic(topicId: string, authorId: string, signal?: AbortSignal) {
    const response = await closeCommunityTopic(topicId, authorId, signal);
    if (!isEnvelopeSuccess(response)) throw new Error(response.message || '结帖失败，请稍后重试');
  }
}
