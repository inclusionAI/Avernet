import { getCapabilities } from '@/capabilities';
import { normalizeOpenApiUserId } from '@/domain/userIdentity';
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
  engine_type?: string;
  template_type?: string;
  template_name?: string;
  template_config?: Record<string, unknown>;
  bot_template_config?: Record<string, unknown>;
  engine_properties?: Record<string, unknown>;
  cluster_name?: string;
  bot_type?: string;
  status?: string;
  owner_entity_id?: string;
  space_id?: string | number;
  space_name?: string;
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
  const explicitUserId = typeof params.user_id === 'string' ? normalizeOpenApiUserId(params.user_id) : '';
  if (explicitUserId) return { ...params, user_id: explicitUserId };
  const safeParams = { ...params };
  delete safeParams.user_id;
  const localUserId = typeof TEAMCLAW_OPENAPI_USER_ID === 'string' ? TEAMCLAW_OPENAPI_USER_ID.trim() : '';
  const normalizedLocalUserId = normalizeOpenApiUserId(localUserId);
  if (normalizedLocalUserId) return { ...safeParams, user_id: normalizedLocalUserId };
  const activeIdentityId = useWorkspaceStore.getState().activeIdentityId;
  const currentUser = getCapabilities().getCurrentOpenApiUserId({ activeIdentityId });
  const userId = currentUser.status === 'available' ? normalizeOpenApiUserId(currentUser.value) : '';
  return userId ? { ...safeParams, user_id: userId } : safeParams;
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
export function listBots(params?: BackendUnknownRecord, signal?: AbortSignal) {
  return backendRequest<BackendApiEnvelope<BackendApiPage<OwnedBotDto>>>(BOT_ENDPOINTS.list, {
    method: 'GET',
    params: userScopedParams(params),
    signal,
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

/** AgentCoding 创建后的架构 Bot 扩展写入。动作 key 由 Service 白名单映射后才能调用。 */
export function updateBotExt(bot_id: string, body: BackendUnknownRecord) {
  return backendRequest<BackendApiEnvelope<BackendUnknownRecord>>(
    `/api/public/bots/${encodeURIComponent(bot_id)}/ext`,
    {
      method: 'PATCH',
      data: body,
      operation: 'agent-coding-after-create',
      target: 'legacy-agentclaw',
    },
  );
}
