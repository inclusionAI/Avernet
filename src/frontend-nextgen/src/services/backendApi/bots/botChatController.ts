import { backendRequest } from '../httpClient';

export interface BotChatEnvelope<T> {
  code?: number;
  success?: boolean;
  message: string;
  error_code?: number;
  data?: T;
  request_id?: string;
}

export interface BotChatMetadataDto {
  attributes?: Record<string, unknown>;
}

export interface BotChatSessionDto {
  id: string;
  timestamp: string;
  session_id?: string | null;
  session_key?: string | null;
  name?: string;
  input?: unknown;
  output_preview?: string | null;
  biz_scene?: string | null;
  biz_task_id?: string | null;
  group_id?: string | null;
  bot_id?: string | null;
  bot_name?: string | null;
  status?: string;
  latency_ms?: number;
  total_tokens?: number;
  total_cost?: number;
  metadata?: BotChatMetadataDto | null;
}

export interface BotChatObservationDto {
  id: string;
  type: string;
  name?: string;
  model_name?: string | null;
  input?: unknown;
  output?: unknown;
  metadata?: Record<string, unknown> | null;
  latency_ms?: number;
  total_tokens?: number;
  total_cost?: number;
  children?: BotChatObservationDto[];
}

export interface BotChatDetailDto extends BotChatSessionDto {
  output?: unknown;
  observations?: BotChatObservationDto[];
}

export interface BotChatPageDto {
  sessions: BotChatSessionDto[];
  total: number;
  page: number;
  limit: number;
  has_more: boolean;
}

export interface BotChatListParams {
  user_id: string;
  bot_id?: string;
  owner_id?: string;
  trace_id?: string;
  session_id?: string;
  session_key?: string;
  query?: string;
  biz_scene?: string;
  biz_task_id?: string;
  group_id?: string;
  match_mode?: 'exact' | 'contains';
  include_output_match?: boolean;
  time_scope?: 'default' | 'all';
  from_date?: string;
  to_date?: string;
  page?: number;
  limit?: number;
}

export function listBotChats(botId: string, params: BotChatListParams) {
  return backendRequest<BotChatEnvelope<BotChatPageDto>>(`/openapi/v1/bots/${encodeURIComponent(botId)}/chats`, {
    method: 'GET',
    params: params as unknown as Record<string, unknown>,
  });
}

export function getBotChat(botId: string, traceId: string, params: { user_id: string; owner_id?: string }) {
  return backendRequest<BotChatEnvelope<BotChatDetailDto>>(
    `/openapi/v1/bots/${encodeURIComponent(botId)}/chats/${encodeURIComponent(traceId)}`,
    { method: 'GET', params: params as unknown as Record<string, unknown> },
  );
}
