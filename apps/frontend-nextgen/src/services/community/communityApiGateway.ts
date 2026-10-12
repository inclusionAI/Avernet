import { mapCommunityReplyDto, mapCommunityTopicDto } from '@/domain/community/mapper';
import type { CommunityReplyPager, CommunityTopicQuery, CreateCommunityTopicInput } from '@/domain/community/types';
import {
  closeCommunityTopic,
  createCommunityTopic,
  getCommunityTopic,
  listCommunityReplies,
  listCommunityTopics,
} from '@/services/backendApi/community/communityController';
import { isEnvelopeSuccess } from '@/services/backendApi/types';
import type { CommunityGateway } from './communityGateway';

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

  async getTopic(topicId: string, signal?: AbortSignal) {
    return mapCommunityTopicDto(requireData(await getCommunityTopic(topicId, signal)));
  }

  async listReplies(topicId: string, pager?: CommunityReplyPager, signal?: AbortSignal) {
    // Backend GET /topics/{id}/posts returns Envelope<Page<PostItem>> (data:{total, items}).
    const page = requireData(await listCommunityReplies(topicId, pager, signal));
    return { total: page.total, items: (page.items ?? []).map(mapCommunityReplyDto).filter((item) => item.id) };
  }

  async createTopic(input: CreateCommunityTopicInput, signal?: AbortSignal) {
    // TopicCreated only carries {topic_id, topic_type}; refetch the full TopicDetail
    // to populate title/body/author for the list prepend.
    const created = requireData(await createCommunityTopic(input, signal));
    return this.getTopic(created.topic_id, signal);
  }

  async closeTopic(topicId: string, authorId: string, signal?: AbortSignal) {
    const response = await closeCommunityTopic(topicId, authorId, signal);
    if (!isEnvelopeSuccess(response)) throw new Error(response.message || '结帖失败，请稍后重试');
  }
}
