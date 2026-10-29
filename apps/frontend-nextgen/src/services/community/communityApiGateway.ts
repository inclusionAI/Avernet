import { mapBrowseSubscriptionDto } from '@/domain/lab/mapper';
import {
  mapCommunityReplyDto,
  mapCommunityTopicDto,
  mapInteractionSummaryDto,
  mapPersonalCenterActivityDto,
} from '@/domain/community/mapper';
import type {
  CommunityReplyPager,
  CommunityTopicQuery,
  CreateCommunityReplyInput,
  CreateCommunityTopicInput,
  InteractionSummary,
  UpdateCommunityTopicInput,
  InteractionSummaryQuery,
  PersonalCenterActivity,
  PersonalCenterActivityPage,
  PersonalCenterActivityQuery,
  RecordInteractionInput,
} from '@/domain/community/types';
import {
  listBrowseSubscriptions,
} from '@/services/backendApi/lab/botConfigController';
import {
  closeCommunityTopic,
  createCommunityReply,
  createCommunityTopic,
  deleteCommunityTopic,
  getCommunityTopic,
  listCommunityReplies,
  listCommunityTopics,
  setCommunityPostHelpful,
  updateCommunityTopic,
} from '@/services/backendApi/community/communityController';
import {
  createCommunityInteraction,
  getCommunityInteractionSummary,
  getCommunityInteractionSummaryBatch,
} from '@/services/backendApi/community/interactionController';
import { getPersonalCenterActivities } from '@/services/backendApi/community/personalCenterController';
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

  async updateTopic(input: UpdateCommunityTopicInput, signal?: AbortSignal) {
    return mapCommunityTopicDto(requireData(await updateCommunityTopic(input, signal)));
  }

  async deleteTopic(topicId: string, authorId: string, signal?: AbortSignal) {
    const deleted = requireData(await deleteCommunityTopic(topicId, authorId, signal));
    if (deleted.deleted === false) throw new Error('主题删除失败');
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

  async createInteraction(input: RecordInteractionInput, signal?: AbortSignal) {
    const recorded = requireData(await createCommunityInteraction(input, signal));
    if (recorded.recorded !== true) throw new Error('互动记录失败');
  }

  async getInteractionSummary(
    query: InteractionSummaryQuery,
    signal?: AbortSignal,
  ): Promise<InteractionSummary> {
    return mapInteractionSummaryDto(requireData(await getCommunityInteractionSummary(query, signal)));
  }

  async getInteractionSummaryBatch(
    queries: InteractionSummaryQuery[],
    signal?: AbortSignal,
  ): Promise<InteractionSummary[]> {
    const data = requireData(await getCommunityInteractionSummaryBatch(queries, signal));
    return data.items.map(mapInteractionSummaryDto);
  }

  async getPersonalCenterActivities(
    query: PersonalCenterActivityQuery,
    signal?: AbortSignal,
  ): Promise<PersonalCenterActivityPage> {
    const page = requireData(await getPersonalCenterActivities(query, signal));
    const items: PersonalCenterActivity[] = (page.items ?? [])
      .map(mapPersonalCenterActivityDto)
      .filter((item): item is PersonalCenterActivity => item !== null);
    return { total: page.total ?? 0, items };
  }

  async listBrowseSubscriptions(ownerUserId: string, signal?: AbortSignal) {
    const botIds = new Set<string>();
    let page = 1;
    let total = 0;
    let loaded = 0;
    do {
      const data = requireData(await listBrowseSubscriptions(ownerUserId, signal, page, 100));
      total = data.total ?? 0;
      const items = data.items ?? [];
      if (!items.length) break;
      loaded += items.length;
      items
        .map(mapBrowseSubscriptionDto)
        .forEach((item) => item.botId && botIds.add(item.botId));
      page += 1;
    } while (loaded < total);
    return [...botIds];
  }
}
