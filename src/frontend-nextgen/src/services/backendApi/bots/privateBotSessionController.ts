import { backendRequest } from '../httpClient';
import type { BackendApiEnvelope } from '../types';

/** /openapi/v1/bots/* 单聊网关接口——与 bot-workshop 的 /openapi/v1/bots CRUD 同前缀但语义不同,
 *  这里只消费 session/connection 子集。命名加 private 前缀避开 workshop 控制器。 */

export interface BotSessionDto {
  session_id: string;
  title: string;
  agent_id: string;
  model: string;
  message_count: number;
  gmt_create: string;
  gmt_modified: string;
}
export interface BotSessionPageDto {
  items: BotSessionDto[];
  total: number;
}

export type BotMessageRole = 'user' | 'assistant' | 'system' | 'tool_use' | 'tool_result';
export interface BotMessageDto {
  message_id: string;
  session_id: string;
  role: BotMessageRole;
  content: string;
  gmt_create: string;
}
export interface BotMessagePageDto {
  items: BotMessageDto[];
  total: number;
}

export interface BotModelDto {
  model_id: string;
  name: string;
  provider: string;
}
export interface BotModelPageDto {
  items: BotModelDto[];
  total: number;
}

export interface BotSocketDto {
  kind: 'chat';
  url: string;
}
export interface BotConnectionDto {
  engine: string;
  expires_at: string;
  sockets: BotSocketDto[];
}

export interface BotRequestParams {
  user_id: string;
  owner_id?: string;
}

export function listBotSessions(botId: string, params: BotRequestParams & { page?: number; page_size?: number }) {
  return backendRequest<BackendApiEnvelope<BotSessionPageDto>>(`/openapi/v1/bots/${botId}/sessions`, {
    method: 'GET',
    params: params as unknown as Record<string, unknown>,
  });
}

export function createBotSession(
  botId: string,
  params: BotRequestParams,
  body: { title?: string },
  signal?: AbortSignal,
) {
  return backendRequest<BackendApiEnvelope<BotSessionDto>>(`/openapi/v1/bots/${botId}/sessions`, {
    method: 'POST',
    params: params as unknown as Record<string, unknown>,
    data: body,
    injectUserId: false,
    signal,
  });
}

export function getBotSession(botId: string, sessionId: string, params: BotRequestParams) {
  return backendRequest<BackendApiEnvelope<BotSessionDto>>(`/openapi/v1/bots/${botId}/sessions/${sessionId}`, {
    method: 'GET',
    params: params as unknown as Record<string, unknown>,
  });
}

export function deleteBotSession(botId: string, sessionId: string, params: BotRequestParams) {
  return backendRequest<BackendApiEnvelope<{ deleted: boolean }>>(`/openapi/v1/bots/${botId}/sessions/${sessionId}`, {
    method: 'DELETE',
    params: params as unknown as Record<string, unknown>,
  });
}

export function deleteBotSessionMessages(botId: string, sessionId: string, params: BotRequestParams) {
  return backendRequest<BackendApiEnvelope<{ deleted: boolean }>>(
    `/openapi/v1/bots/${botId}/sessions/${sessionId}/messages`,
    { method: 'DELETE', params: params as unknown as Record<string, unknown> },
  );
}

export function listBotSessionMessages(
  botId: string,
  sessionId: string,
  params: BotRequestParams & { page?: number; page_size?: number },
) {
  return backendRequest<BackendApiEnvelope<BotMessagePageDto>>(
    `/openapi/v1/bots/${botId}/sessions/${sessionId}/messages`,
    { method: 'GET', params: params as unknown as Record<string, unknown> },
  );
}

export function listBotModels(botId: string, params: BotRequestParams & { page?: number; page_size?: number }) {
  return backendRequest<BackendApiEnvelope<BotModelPageDto>>(`/openapi/v1/bots/${botId}/models`, {
    method: 'GET',
    params: params as unknown as Record<string, unknown>,
  });
}

export function updateBotSession(
  botId: string,
  sessionId: string,
  params: BotRequestParams,
  body: { model?: string; title?: string },
) {
  return backendRequest<BackendApiEnvelope<BotSessionDto>>(`/openapi/v1/bots/${botId}/sessions/${sessionId}`, {
    method: 'PATCH',
    params: params as unknown as Record<string, unknown>,
    data: body,
  });
}

export function getBotConnection(botId: string, params: BotRequestParams) {
  return backendRequest<BackendApiEnvelope<BotConnectionDto>>(`/openapi/v1/bots/${botId}/connection`, {
    method: 'GET',
    params: params as unknown as Record<string, unknown>,
  });
}

export interface SessionFavoriteResultDto {
  session_id: string;
  favorited: boolean;
}

/** PUT /openapi/v1/bots/{bot_id}/sessions/{session_id}/favorite — 收藏会话。 */
export function favoriteBotSession(botId: string, sessionId: string, params: BotRequestParams) {
  return backendRequest<BackendApiEnvelope<SessionFavoriteResultDto>>(
    `/openapi/v1/bots/${botId}/sessions/${sessionId}/favorite`,
    { method: 'PUT', params: params as unknown as Record<string, unknown> },
  );
}

/** DELETE /openapi/v1/bots/{bot_id}/sessions/{session_id}/favorite — 取消收藏。 */
export function unfavoriteBotSession(botId: string, sessionId: string, params: BotRequestParams) {
  return backendRequest<BackendApiEnvelope<SessionFavoriteResultDto>>(
    `/openapi/v1/bots/${botId}/sessions/${sessionId}/favorite`,
    { method: 'DELETE', params: params as unknown as Record<string, unknown> },
  );
}

/** GET /openapi/v1/bots/{bot_id}/sessions/favorites — 查询收藏会话列表。 */
export interface FavoriteSessionDto {
  session_id: string;
  title: string;
  agent_id?: string;
  model?: string;
  permission_mode?: string;
  cwd?: string;
  runtime?: string;
  message_count: number;
  gmt_create: string;
  gmt_modified: string;
}
export interface FavoriteSessionPageDto {
  items: FavoriteSessionDto[];
  total: number;
}

export function listFavoriteSessions(botId: string, params: BotRequestParams & { page?: number; page_size?: number }) {
  return backendRequest<BackendApiEnvelope<FavoriteSessionPageDto>>(`/openapi/v1/bots/${botId}/sessions/favorites`, {
    method: 'GET',
    params: params as unknown as Record<string, unknown>,
  });
}
