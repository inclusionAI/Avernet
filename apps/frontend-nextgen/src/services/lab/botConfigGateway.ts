import type { BrowseSubscription, UpsertSubscriptionInput } from '@/domain/lab/types';

/**
 * Bot 逛社区订阅网关。镜像 CommunityGateway 范式：
 * - Service 负责 owner/校验/排序，Gateway 仅做 HTTP 与信封解码 + DTO→领域映射。
 * - 数据源切换由实现类决定（BotConfigApiGateway 走 openapi 公共面 API；后续真实后端不改 contract）。
 */
export interface BotConfigGateway {
  listSubscriptions(ownerUserId: string, signal?: AbortSignal): Promise<BrowseSubscription[]>;
  upsertSubscription(input: UpsertSubscriptionInput, signal?: AbortSignal): Promise<BrowseSubscription>;
  deleteSubscription(botId: string, signal?: AbortSignal): Promise<void>;
}
