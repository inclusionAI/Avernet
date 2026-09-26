import { backendRequest } from '../httpClient';
import type { BackendApiEnvelope, BackendUnknownRecord } from '../types';
import type { BotChatDetailDto, BotChatEnvelope, BotChatPageDto } from './botChatController';

export type BotTraceDto = BackendUnknownRecord;

/**
 * Compatibility controller for callers that still use the historical log
 * names. The gateway only publishes the bot-scoped OpenAPI chat routes, so
 * these helpers forward to those routes instead of the retired logs API.
 */
export const BOT_LOG_ENDPOINTS = {
  traces: (botId: string) => `/openapi/v1/bots/${encodeURIComponent(botId)}/chats`,
  trace: (botId: string, traceId: string) =>
    `/openapi/v1/bots/${encodeURIComponent(botId)}/chats/${encodeURIComponent(traceId)}`,
  groupTraces: (botId: string) => `/openapi/v1/bots/${encodeURIComponent(botId)}/chats`,
};

export interface BotTraceListParams extends BackendUnknownRecord {
  bot_id: string;
}

// 查询 trace 列表，日志正文由上层 Service 做脱敏后再展示。
export function listBotTraces(params: BotTraceListParams) {
  return backendRequest<BotChatEnvelope<BotChatPageDto>>(BOT_LOG_ENDPOINTS.traces(params.bot_id), {
    method: 'GET',
    params,
  });
}

export interface GroupBotTraceParams {
  bot_id: string;
  user_id: string;
  owner_id?: string;
  page?: number;
  limit?: number;
  time_scope?: 'default' | 'all';
}

export interface GroupBotTraceDetailParams {
  bot_id: string;
  group_id: string;
  user_id: string;
  owner_id?: string;
}

export function listGroupBotTraces(groupId: string, params: GroupBotTraceParams) {
  return backendRequest<BotChatEnvelope<BotChatPageDto>>(BOT_LOG_ENDPOINTS.groupTraces(params.bot_id), {
    method: 'GET',
    params: { ...params, group_id: groupId, match_mode: 'exact', time_scope: params.time_scope ?? 'all' },
  });
}

// 查询 trace 详情。保留历史导出名，但使用 bot-scoped Gateway 路由。
export function getBotTrace(traceId: string, params: { bot_id: string; user_id?: string; owner_id?: string }) {
  return backendRequest<BackendApiEnvelope<BotTraceDto>>(BOT_LOG_ENDPOINTS.trace(params.bot_id, traceId), {
    method: 'GET',
    params: { user_id: params.user_id, owner_id: params.owner_id },
  });
}

// 查询 Group 关联 trace 详情，仅供 Bot Chats Group 模式使用。
export function getGroupBotTrace(traceId: string, params: GroupBotTraceDetailParams) {
  return backendRequest<BotChatEnvelope<BotChatDetailDto>>(BOT_LOG_ENDPOINTS.trace(params.bot_id, traceId), {
    method: 'GET',
    params: { user_id: params.user_id, owner_id: params.owner_id },
  });
}
