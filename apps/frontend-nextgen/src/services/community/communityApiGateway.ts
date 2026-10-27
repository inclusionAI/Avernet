import {
  mapCommunityAttachmentDto,
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
  downloadCommunityAttachment,
  getCommunityTopic,
  listCommunityReplies,
  listCommunityTopics,
  uploadCommunityAttachment,
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

  async uploadAttachment(file: File, authorId: string, signal?: AbortSignal) {
    return mapCommunityAttachmentDto(requireData(await uploadCommunityAttachment(file, authorId, signal)));
  }

  downloadAttachment(attachmentId: string, signal?: AbortSignal) {
    return downloadCommunityAttachment(attachmentId, signal);
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

  async closeTopic(topicId: string, authorId: string, signal?: AbortSignal) {
    const response = await closeCommunityTopic(topicId, authorId, signal);
    if (!isEnvelopeSuccess(response)) throw new Error(response.message || '结帖失败，请稍后重试');
  }
}
