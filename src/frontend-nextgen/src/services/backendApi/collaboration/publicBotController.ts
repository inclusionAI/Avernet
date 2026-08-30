import { backendRequest } from '../httpClient';

/** TeamClaw Gateway bot-catalog 的公开 Bot 普通搜索 DTO。 */
export interface PublicBotCatalogDto {
  bot_id: string;
  bot_type: string;
  description: string;
  engine: string;
  entity_id: string;
  name: string;
  owner_name: string;
  status: string;
}

/** 公开 Bot 普通搜索的 Gateway query。 */
export interface SearchPublicBotsParams extends Record<string, unknown> {
  search?: string;
  page?: number;
  page_size?: number;
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
  runtime_state?: 'online';
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

function isAceLoginResponse(value: unknown) {
  return (
    isRecord(value) &&
    value.actionType === 'LOGIN' &&
    value.buserviceErrorCode === 'USER_NOT_LOGIN' &&
    value.decisionBy === 'ACE'
  );
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
  if (!isRecord(value) || value.code !== 200000) {
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
