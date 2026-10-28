import { getCapabilities } from '@/capabilities';
import { normalizeOpenApiUserId } from '@/domain/userIdentity';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { backendRequest } from '../httpClient';
import type { BackendApiEnvelope, BackendApiPage, BackendUnknownRecord } from '../types';
import type {
  BotEngineRestartResultDto,
  BotEngineRuntimeRequestOptions,
  BotEngineRuntimeStatusDto,
  BotEngineVersionDto,
} from './botEngineVersionDtos';

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
  actions?: string[];
  disabled_actions?: Record<string, string>;
  space_id?: string | number;
  space_name?: string;
  /** 归属空间(2026-10-21 对齐线上实际返回的嵌套形态;bots 接口实测 space_id/name/kind)。 */
  space?: { space_id: string; name: string; kind: string } | null;
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

/** POST /openapi/v1/bots/metadata/queries 单条返回结构。 */
export interface BotMetadataDto {
  bot_id: string;
  owner_id: string;
  bot_name: string;
  bot_desc?: string;
  engine?: string;
  template_type?: string;
  template_name?: string;
  bot_type?: string;
  /** 服务 Bot 是否有可用的 online 发布；非服务 Bot 为 null。 */
  online_available?: boolean | null;
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
  engineStatus: (bot_id: string) => `/openapi/v1/bots/${encodeURIComponent(bot_id)}/engine/status`,
  engineVersion: (bot_id: string) => `/openapi/v1/bots/${encodeURIComponent(bot_id)}/engine-version`,
  refetchEngine: (bot_id: string) => `/openapi/v1/bots/${bot_id}/engine/refetch`,
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

export function listBotInventory(params?: BackendUnknownRecord, spaceId?: string, signal?: AbortSignal) {
  return backendRequest<BackendApiEnvelope<BackendApiPage<BotDto>>>(BOT_ENDPOINTS.inventory, {
    method: 'GET',
    params: userScopedParams(params),
    headers: spaceId ? { 'X-Space-Id': spaceId } : undefined,
    signal,
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
export function getBot(bot_id: string, owner_id?: string) {
  return backendRequest<BackendApiEnvelope<BotDto>>(BOT_ENDPOINTS.detail(bot_id), {
    method: 'GET',
    params: userScopedParams({ owner_id }),
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
export function restartBot(bot_id: string, target?: BotEngineRuntimeRequestOptions) {
  return backendRequest<BackendApiEnvelope<BotDto>>(BOT_ENDPOINTS.restart(bot_id), {
    method: 'POST',
    params: target ? userScopedParams({ user_id: target.userId, entity_id: target.ownerId }) : userScopedParams(),
    signal: target?.signal,
  });
}

/** Poll the Bot-level restart lifecycle; readiness alone is not version confirmation. */
export function getBotRestartStatus(bot_id: string, target: BotEngineRuntimeRequestOptions) {
  return backendRequest<BackendApiEnvelope<{ status: string; is_ready: boolean; device_id?: string | null }>>(
    BOT_ENDPOINTS.status(bot_id),
    {
      method: 'GET',
      params: userScopedParams({ user_id: target.userId, entity_id: target.ownerId }),
      signal: target.signal,
    },
  );
}

function engineRuntimeParams(target?: BotEngineRuntimeRequestOptions): BackendUnknownRecord {
  if (!target) return userScopedParams();
  return userScopedParams({
    user_id: target.userId,
    ...(target.ownerId ? { entity_id: target.ownerId } : {}),
    // Engine inspection/restart only supports draft; omit it and use the Backend default.
    ...(target.deviceUuid ? { device_uuid: target.deviceUuid } : {}),
  });
}

/** Read the Backend's existing advisory contract; this does not grant restart permission. */
export function getBotEngineVersion(bot_id: string, target: BotEngineRuntimeRequestOptions) {
  return backendRequest<BackendApiEnvelope<BotEngineVersionDto>>(BOT_ENDPOINTS.engineVersion(bot_id), {
    method: 'GET',
    params: engineRuntimeParams(target),
    signal: target.signal,
  });
}

/** Read the selected draft engine's running state after an engine restart request. */
export function getBotEngineStatus(bot_id: string, target: BotEngineRuntimeRequestOptions) {
  return backendRequest<BackendApiEnvelope<BotEngineRuntimeStatusDto>>(BOT_ENDPOINTS.engineStatus(bot_id), {
    method: 'GET',
    params: engineRuntimeParams(target),
    signal: target.signal,
  });
}

export function restartBotEngine(bot_id: string, target?: BotEngineRuntimeRequestOptions) {
  return backendRequest<BackendApiEnvelope<BotEngineRestartResultDto>>(BOT_ENDPOINTS.restartEngine(bot_id), {
    method: 'POST',
    params: engineRuntimeParams(target),
    // The existing Backend relay expects a JSON request even though force is not exposed to callers.
    data: { force: false },
    signal: target?.signal,
  });
}

// Coding Bot 的引擎重启预设为 Relay refetch 语义：重新拉取二进制并重启运行时。
export function refetchBotEngine(bot_id: string) {
  return backendRequest<BackendApiEnvelope<BackendUnknownRecord>>(BOT_ENDPOINTS.refetchEngine(bot_id), {
    method: 'POST',
    params: userScopedParams(),
    data: { force: true },
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
