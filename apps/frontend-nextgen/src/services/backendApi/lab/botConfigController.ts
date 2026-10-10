import type { BrowseSubscriptionDto, UpsertSubscriptionInput } from '@/domain/lab/types';
import { backendRequest } from '../httpClient';
import type { BackendApiEnvelope, BackendApiPage } from '../types';

export type BotConfigEnvelope<T> = BackendApiEnvelope<T>;

/**
 * BBS 逛论坛订阅内部 /api Unified 面端点（Avernet contract §3；预发 teamclawgw-pre 网关尚未合并 /openapi/v1/bbs 转发，故 dev 直连 engine）：
 * - 列表订阅 §3.3：GET /api/v1/bbs/browse-subscriptions?owner_user_id=&page=&page_size=
 *   （按 owner 列其已开启周期逛的 Bot；OPEN/NoCheck，不校验登录人）。
 * - 单 Bot CRUD §3.1/§3.2：POST/DELETE /api/v1/bots/{bot_id}/bbs/browse-subscription
 *   （§3.1 需必填 owner_user_id(query) 声明订阅 owner，不取自登录人、不校验登录人；body 只 {note}，不发 mode；
 *   §3.2 仅 bot_id 路径，无 owner query）。
 *
 * /api/v1/bbs 与 /api/v1/bots 均 dev 直连 agentclawengine-pre（同 app）；httpClient 另行自动注入 user_id（多余无害）。
 * bot_id 取 mine 列表拆分后的真实 bot_id（realBotId，详见 useOwnedBots/splitBotId）。
 */
const BROWSE_SUBSCRIPTION_ENDPOINTS = {
  list: '/api/v1/bbs/browse-subscriptions',
  subscription: (botId: string) => `/api/v1/bots/${encodeURIComponent(botId)}/bbs/browse-subscription`,
};

/**
 * 列出某 owner 已开启周期逛的全部 Bot（§3.3）。owner_user_id 必填；httpClient 另行自动注入 user_id（OPEN/NoCheck，
 * 多余无害）。订阅量小，前端按本人 bot 列表交集展示。
 * pageSize 默认 100：后端 PageParams 约束 page_size le=100，超出会 422。
 */
export function listBrowseSubscriptions(ownerUserId: string, signal?: AbortSignal, page = 1, pageSize = 100) {
  return backendRequest<BotConfigEnvelope<BackendApiPage<BrowseSubscriptionDto>>>(BROWSE_SUBSCRIPTION_ENDPOINTS.list, {
    method: 'GET',
    params: { owner_user_id: ownerUserId, page, page_size: pageSize },
    signal,
  });
}

/**
 * 加入/更新逛论坛订阅（§3.1）。Unified 面：owner_user_id(query, 必填, 声明订阅 owner，不取自登录人)
 * + body 只 {note}；httpClient 另行自动注入 user_id（OPEN/NoCheck，多余无害）。新订阅 201，更新 200。
 */
export function upsertBrowseSubscription(input: UpsertSubscriptionInput, signal?: AbortSignal) {
  return backendRequest<BotConfigEnvelope<BrowseSubscriptionDto>>(
    BROWSE_SUBSCRIPTION_ENDPOINTS.subscription(input.botId),
    {
      method: 'POST',
      params: { owner_user_id: input.ownerUserId },
      data: { note: input.note ?? null },
      signal,
    },
  );
}

/** 取消逛论坛订阅（§3.2）。Unified 面：仅 bot_id 路径寻址，无 owner query（OPEN/NoCheck，不校验登录人）；httpClient 自动注入 user_id 多余无害；幂等返回 deleted=false。 */
export function deleteBrowseSubscription(botId: string, signal?: AbortSignal) {
  return backendRequest<BotConfigEnvelope<{ deleted: boolean }>>(BROWSE_SUBSCRIPTION_ENDPOINTS.subscription(botId), {
    method: 'DELETE',
    signal,
  });
}
