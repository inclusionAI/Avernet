import { backendRequest } from '../httpClient';
import type { BackendApiEnvelope, BackendApiPage, BackendUnknownRecord } from '../types';
import type { BotChatDetailDto, BotChatEnvelope, BotChatPageDto } from './botChatController';

export type BotTraceDto = BackendUnknownRecord;
export const BOT_LOG_ENDPOINTS = {
  traces: '/openapi/v1/bots/logs/traces',
  trace: (trace_id: string) => `/openapi/v1/bots/logs/traces/${trace_id}`,
  groupTraces: (group_id: string) => `/openapi/v1/bots/logs/groups/${group_id}/traces`,
  sessionTraces: (session_key: string) => `/openapi/v1/bots/logs/sessions/${session_key}/traces`,
  taskTraces: (biz_scene: string, biz_task_id: string) =>
    `/openapi/v1/bots/logs/tasks/${biz_scene}/${biz_task_id}/traces`,
};

// 查询 trace 列表，日志正文由上层 Service 做脱敏后再展示。
export function listBotTraces(params?: BackendUnknownRecord) {
  return backendRequest<BackendApiEnvelope<BackendApiPage<BotTraceDto>>>(BOT_LOG_ENDPOINTS.traces, {
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
}

export interface GroupBotTraceDetailParams {
  bot_id: string;
  group_id: string;
  user_id: string;
  owner_id?: string;
}

export function listGroupBotTraces(groupId: string, params: GroupBotTraceParams) {
  return backendRequest<BotChatEnvelope<BotChatPageDto>>(BOT_LOG_ENDPOINTS.groupTraces(groupId), {
    method: 'GET',
    params: params as unknown as Record<string, unknown>,
  });
}

// 查询 trace 详情。保留原有导出的参数和返回类型，避免影响其他调用方。
export function getBotTrace(trace_id: string) {
  return backendRequest<BackendApiEnvelope<BotTraceDto>>(BOT_LOG_ENDPOINTS.trace(trace_id), { method: 'GET' });
}

// 查询 Group 关联 trace 详情，仅供 Bot Chats Group 模式使用。
export function getGroupBotTrace(traceId: string, params: GroupBotTraceDetailParams) {
  return backendRequest<BotChatEnvelope<BotChatDetailDto>>(BOT_LOG_ENDPOINTS.trace(traceId), {
    method: 'GET',
    params: params as unknown as Record<string, unknown>,
  });
}
