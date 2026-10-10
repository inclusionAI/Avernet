import { mapBrowseSubscriptionDto } from '@/domain/lab/mapper';
import type { UpsertSubscriptionInput } from '@/domain/lab/types';
import {
  deleteBrowseSubscription,
  listBrowseSubscriptions,
  upsertBrowseSubscription,
} from '@/services/backendApi/lab/botConfigController';
import { isEnvelopeSuccess } from '@/services/backendApi/types';
import type { BotConfigGateway } from './botConfigGateway';

function requireData<T>(response: { code?: string | number; success?: boolean; message?: string; data?: T }): T {
  if (!isEnvelopeSuccess(response) || response.data === undefined) {
    throw new Error(response.message || '社区配置服务暂时不可用');
  }
  return response.data;
}

export class BotConfigApiGateway implements BotConfigGateway {
  async listSubscriptions(ownerUserId: string, signal?: AbortSignal) {
    const page = requireData(await listBrowseSubscriptions(ownerUserId, signal));
    return (page.items ?? []).map(mapBrowseSubscriptionDto).filter((item) => item.botId);
  }

  async upsertSubscription(input: UpsertSubscriptionInput, signal?: AbortSignal) {
    return mapBrowseSubscriptionDto(requireData(await upsertBrowseSubscription(input, signal)));
  }

  async deleteSubscription(botId: string, signal?: AbortSignal) {
    const response = await deleteBrowseSubscription(botId, signal);
    if (!isEnvelopeSuccess(response)) {
      throw new Error(response.message || '取消订阅失败，请稍后重试');
    }
  }
}
