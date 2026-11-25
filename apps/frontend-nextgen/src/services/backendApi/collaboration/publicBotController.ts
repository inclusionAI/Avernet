import { isAceLoginResponse } from '../aceLoginBody';
import { backendRequest } from '../httpClient';
import { isEnvelopeFailure } from '../types';

/** TeamClaw Gateway bot-catalog 的公开 Bot 普通搜索 DTO。 */
export interface PublicBotCatalogDto {
  avatar_url?: string | null;
  bot_id: string;
  bot_uuid?: string;
  bot_type: string;
  description: string;
  engine: string;
  entity_id?: string;
  name: string;
  owner_name: string;
  status: string;
  /** Search 返回的当前 Human→Bot 好友关系；缺失时由 Domain 安全降级为 none。 */
  is_friend?: boolean;
  /** BCS 当前在线状态；独立于 Backend 生命周期 status（如 ACTIVE）。 */
  is_online?: boolean;
  actor_kind?: 'bot' | 'human';
  friend_ext?: Record<string, unknown>;
  friend_check_in_strategy?: 'OPEN' | 'APPROVAL' | 'DEPT_FREE';
  /** Bot→Bot 协作可见性（BCS visibility，public/protected/private）；缺失时按未知处理。 */
  visibility?: string;
  /** 人类用户可见性（BCS user_visibility，public/protected/private）；缺失时按未知处理。 */
  user_visibility?: string;
}

/** 公开 Bot 普通搜索的 Gateway query。 */
export interface SearchPublicBotsParams extends Record<string, unknown> {
  search?: string;
  page?: number;
  page_size?: number;
  /** 当前身份（viewer），用于以该身份过滤可见 Bot 与计算 is_friend。 */
  viewer_actor_type?: 'human' | 'bot';
  viewer_actor_id?: string;
}

export interface PublicBotCatalogPage {
  items: PublicBotCatalogDto[];
  total: number;
}

export interface PublicBotCatalogResponse {
  code: 200000;
  message?: string;
  data: PublicBotCatalogPage;
  request_id?: string;
}

export interface DiscoverPublicBotsParams extends Record<string, unknown> {
  keyword: string;
  top_k?: number;
  min_score?: number;
  /** 仅 Human 视角使用；Bot 视角不应下发该参数。 */
  runtime_state?: 'online';
  /** 当前身份（viewer），与 Search 一致。 */
  viewer_actor_type?: 'human' | 'bot';
  viewer_actor_id?: string;
}

export interface PublicBotDiscoveryDto extends PublicBotCatalogDto {
  recommendation?: Record<string, unknown>;
}

export interface PublicBotDiscoveryResponse {
  code: 200000;
  message?: string;
  data: {
    items: PublicBotDiscoveryDto[];
    total: number;
  };
  request_id?: string;
}

export type PublicBotCatalogErrorCode = 'unauthenticated' | 'protocol_error';

export class PublicBotCatalogError extends Error {
  constructor(public readonly code: PublicBotCatalogErrorCode, message: string) {
    super(message);
    this.name = 'PublicBotCatalogError';
  }
}

export const PUBLIC_BOT_ENDPOINTS = {
  search: '/openapi/v1/bots/catalog/search',
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

function validateCatalogResponse<T extends PublicBotCatalogDto>(
  value: unknown,
): {
  code: 200000;
  message?: string;
  data: { items: T[]; total: number };
  request_id?: string;
} {
  if (isAceLoginResponse(value)) {
    throw new PublicBotCatalogError('unauthenticated', 'Bot Catalog request requires authentication');
  }
  if (!isRecord(value) || isEnvelopeFailure(value)) {
    throw new PublicBotCatalogError('protocol_error', 'Unexpected Bot Catalog business response');
  }
  if (!isRecord(value.data) || !Array.isArray(value.data.items) || typeof value.data.total !== 'number') {
    throw new PublicBotCatalogError('protocol_error', 'Invalid Bot Catalog page response');
  }
  return value as unknown as {
    code: 200000;
    message?: string;
    data: { items: T[]; total: number };
    request_id?: string;
  };
}

/**
 * 公开 Bot 普通搜索（bot-catalog）。
 * 空结果仍按成功响应返回，由上层统一映射为空态；不回退到本地 Mock 或 discovery。
 */
export async function searchPublicBots(
  params: SearchPublicBotsParams = {},
  signal?: AbortSignal,
): Promise<PublicBotCatalogResponse> {
  const response = await backendRequest<unknown>(PUBLIC_BOT_ENDPOINTS.search, {
    method: 'GET',
    params,
    injectUserId: false,
    signal,
  });
  return validateCatalogResponse<PublicBotCatalogDto>(response);
}

/**
 * 公开 Bot 智能发现（bot-catalog）。
 * 与普通 Search 保持独立 Controller/DTO，避免将语义搜索退化为前端本地过滤。
 */
export const PUBLIC_BOT_DISCOVERY_ENDPOINT = '/openapi/v1/bots/catalog/discover';

export async function discoverPublicBots(
  params: DiscoverPublicBotsParams,
  signal?: AbortSignal,
): Promise<PublicBotDiscoveryResponse> {
  const response = await backendRequest<unknown>(PUBLIC_BOT_DISCOVERY_ENDPOINT, {
    method: 'GET',
    params,
    injectUserId: false,
    signal,
  });
  return validateCatalogResponse<PublicBotDiscoveryDto>(response);
}

/** Bot 画像（bot-catalog profile）能力条目 DTO：capability_id + display_name。 */
export interface PublicBotCapabilityDto {
  capability_id: string;
  display_name: string;
}

/** Bot 画像（bot-catalog profile）技能条目 DTO：skill_id + display_name。 */
export interface PublicBotSkillDto {
  skill_id: string;
  display_name: string;
}

/** 好友申请弹窗消费的公开 Bot 画像 DTO（后端 `PublicBotProfile` 契约）。 */
export interface PublicBotProfileDto {
  bot_id: string;
  bot_uuid?: string | null;
  bot_name: string;
  owner_name?: string | null;
  description?: string | null;
  engine_name?: string | null;
  capabilities: PublicBotCapabilityDto[];
  /** 已公开技能清单；旧 envelope / BCS-only 画像可能缺省，Mapper 统一回退空列表。 */
  skills?: PublicBotSkillDto[];
  // 线上 envelope 允许携带未知扩展键；未知键不进 Mapper 投影。
  [key: string]: unknown;
}

export interface PublicBotProfileResponse {
  code: 200000;
  message?: string;
  data: PublicBotProfileDto;
  request_id?: string;
}

export const PUBLIC_BOT_PROFILE_ENDPOINT = '/openapi/v1/bots/catalog/profile';

function validateProfileResponse(value: unknown): PublicBotProfileResponse {
  if (isAceLoginResponse(value)) {
    throw new PublicBotCatalogError('unauthenticated', 'Bot Catalog request requires authentication');
  }
  if (!isRecord(value) || isEnvelopeFailure(value)) {
    throw new PublicBotCatalogError('protocol_error', 'Unexpected Bot Catalog business response');
  }
  if (
    !isRecord(value.data) ||
    typeof value.data.bot_id !== 'string' ||
    typeof value.data.bot_name !== 'string' ||
    !Array.isArray(value.data.capabilities)
  ) {
    throw new PublicBotCatalogError('protocol_error', 'Invalid Bot Catalog profile response');
  }
  return value as unknown as PublicBotProfileResponse;
}

/**
 * 公开 Bot 画像（bot-catalog profile）：好友申请弹窗的名称/Owner/引擎/已公开能力来源。
 * 404（不在公开目录）由上层映射为 target_invalid，与其他公开资源口径一致。
 */
export async function getPublicBotProfile(
  botId: string,
  signal?: AbortSignal,
): Promise<PublicBotProfileResponse> {
  const response = await backendRequest<unknown>(
    `${PUBLIC_BOT_PROFILE_ENDPOINT}/${encodeURIComponent(botId)}`,
    {
      method: 'GET',
      injectUserId: false,
      signal,
    },
  );
  return validateProfileResponse(response);
}
