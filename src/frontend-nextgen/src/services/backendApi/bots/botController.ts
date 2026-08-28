import { getCapabilities } from '@/capabilities';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { backendRequest } from '../httpClient';
import type { BackendApiEnvelope, BackendApiPage, BackendUnknownRecord } from '../types';

export type BotDto = BackendUnknownRecord;
export type MutateBotRequest = BackendUnknownRecord;

/** GET /openapi/v1/bots 单条返回结构。 */
export interface OwnedBotDto {
  bot_id: string;
  bot_name: string;
  bot_desc?: string;
  engine?: string;
  cluster_name?: string;
  bot_type?: string;
  status?: string;
  owner_entity_id?: string;
}

/** /openapi/v1/bots/metadata/queries 的 body 里单个 bot 查询项。 */
export interface BotMetadataQueryItem {
  bot_id: string;
  owner_id: string;
}

/** 批量查询好友 Bot 元数据的请求体。 */
export interface BotMetadataQueryBody {
  bots: BotMetadataQueryItem[];
}

/** GET /openapi/v1/bots/metadata/queries 单条返回结构。 */
export interface BotMetadataDto {
  bot_id: string;
  owner_id: string;
  bot_name: string;
  bot_desc?: string;
  engine?: string;
  bot_type?: string;
  status?: string;
}

export interface BotAuthPendingDto extends BackendUnknownRecord {
  bot_id: string;
  iframe_url: string;
  redirect_url: string;
}
export interface BotAuthStatusDto extends BackendUnknownRecord {
  status: string;
  message?: string;
  bot?: BotDto | null;
}

export function userScopedParams(params: BackendUnknownRecord = {}) {
  if (params.user_id) return params;
  const localUserId = typeof TEAMCLAW_OPENAPI_USER_ID === 'string' ? TEAMCLAW_OPENAPI_USER_ID.trim() : '';
  if (localUserId) return { ...params, user_id: localUserId };
  const activeIdentityId = useWorkspaceStore.getState().activeIdentityId;
  const currentUser = getCapabilities().getCurrentOpenApiUserId({ activeIdentityId });
  const userId = currentUser.status === 'available' ? currentUser.value?.trim() : '';
  return userId ? { ...params, user_id: userId } : params;
}

export const BOT_ENDPOINTS = {
  list: '/openapi/v1/bots',
  metadataQueries: '/openapi/v1/bots/metadata/queries',
  inventory: '/openapi/v1/bots/all',
  detail: (bot_id: string) => `/openapi/v1/bots/${bot_id}`,
  checkName: '/openapi/v1/bots/check-name',
  ceiling: '/openapi/v1/bots/ceiling',
  status: (bot_id: string) => `/openapi/v1/bots/${bot_id}/status`,
  restart: (bot_id: string) => `/openapi/v1/bots/${bot_id}/restart`,
  restartEngine: (bot_id: string) => `/openapi/v1/bots/${bot_id}/engine/restart`,
  upgradeService: (bot_id: string) => `/openapi/v1/bots/${bot_id}/lifecycle/upgrade`,
  lifecycle: (bot_id: string) => `/openapi/v1/bots/${bot_id}/lifecycle`,
  local: (bot_id: string) => `/openapi/v1/bots/${bot_id}/local`,
  restartLocal: (bot_id: string) => `/openapi/v1/bots/${bot_id}/local/restart`,
  space: (bot_id: string) => `/openapi/v1/bots/${bot_id}/space`,
  authStatus: (bot_id: string) => `/openapi/v1/bots/${bot_id}/auth-status`,
};

export function changeBotSpace(bot_id: string, space_id: number, user_id?: string) {
  return backendRequest<BackendApiEnvelope<BackendUnknownRecord>>(BOT_ENDPOINTS.space(bot_id), {
    method: 'PUT',
    params: userScopedParams(user_id ? { user_id } : {}),
    data: { space_id },
  });
}

export function listBotInventory(params?: BackendUnknownRecord, spaceId?: string) {
  return backendRequest<BackendApiEnvelope<BackendApiPage<BotDto>>>(BOT_ENDPOINTS.inventory, {
    method: 'GET',
    params: userScopedParams(params),
    headers: spaceId ? { 'X-Space-Id': spaceId } : undefined,
  });
}

// 查询 Bot 列表。
export function listBots(params?: BackendUnknownRecord) {
  return backendRequest<BackendApiEnvelope<BackendApiPage<OwnedBotDto>>>(BOT_ENDPOINTS.list, {
    method: 'GET',
    params: userScopedParams(params),
  });
}

/** 按 bot_id + owner_id 批量查询好友 Bot 元数据（返回顺序不保证，消费方需按 id 映射）。 */
export function listBotMetadata(params: BackendUnknownRecord, body: BotMetadataQueryBody) {
  return backendRequest<BackendApiEnvelope<BackendApiPage<BotMetadataDto>>>(BOT_ENDPOINTS.metadataQueries, {
    method: 'POST',
    params: userScopedParams(params),
    data: body,
  });
}

// 创建 Bot。
export function createBot(body: MutateBotRequest) {
  return backendRequest<BackendApiEnvelope<BotDto | BotAuthPendingDto>>(BOT_ENDPOINTS.list, {
    method: 'POST',
    params: userScopedParams(),
    data: body,
  });
}

export function pollBotAuthStatus(bot_id: string, body: MutateBotRequest) {
  return backendRequest<BackendApiEnvelope<BotAuthStatusDto>>(BOT_ENDPOINTS.authStatus(bot_id), {
    method: 'POST',
    params: userScopedParams(),
    data: body,
  });
}

// 查询 Bot 详情。
export function getBot(bot_id: string) {
  return backendRequest<BackendApiEnvelope<BotDto>>(BOT_ENDPOINTS.detail(bot_id), {
    method: 'GET',
    params: userScopedParams(),
  });
}

// 更新 Bot。
export function updateBot(bot_id: string, body: MutateBotRequest) {
  return backendRequest<BackendApiEnvelope<BotDto>>(BOT_ENDPOINTS.detail(bot_id), {
    method: 'PUT',
    params: userScopedParams(),
    data: body,
  });
}

// 删除 Bot。
export function deleteBot(bot_id: string) {
  return backendRequest<BackendApiEnvelope<void>>(BOT_ENDPOINTS.detail(bot_id), {
    method: 'DELETE',
    params: userScopedParams(),
  });
}

// 重启 Bot。
export function restartBot(bot_id: string) {
  return backendRequest<BackendApiEnvelope<BotDto>>(BOT_ENDPOINTS.restart(bot_id), {
    method: 'POST',
    params: userScopedParams(),
  });
}

export function restartBotEngine(bot_id: string) {
  return backendRequest<BackendApiEnvelope<BackendUnknownRecord>>(BOT_ENDPOINTS.restartEngine(bot_id), {
    method: 'POST',
    params: userScopedParams(),
  });
}

export function upgradeBotToService(bot_id: string) {
  return backendRequest<BackendApiEnvelope<BackendUnknownRecord>>(BOT_ENDPOINTS.upgradeService(bot_id), {
    method: 'POST',
    params: userScopedParams(),
  });
}

export function deleteServiceDraft(bot_id: string) {
  return backendRequest<BackendApiEnvelope<void>>(BOT_ENDPOINTS.lifecycle(bot_id), {
    method: 'DELETE',
    params: userScopedParams(),
  });
}

export function deleteLocalBot(bot_id: string) {
  return backendRequest<BackendApiEnvelope<void>>(BOT_ENDPOINTS.local(bot_id), {
    method: 'DELETE',
    params: userScopedParams(),
  });
}

export function restartLocalBot(bot_id: string) {
  return backendRequest<BackendApiEnvelope<BackendUnknownRecord>>(BOT_ENDPOINTS.restartLocal(bot_id), {
    method: 'POST',
    params: userScopedParams(),
  });
}
