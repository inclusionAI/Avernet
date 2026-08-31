import { backendRequest } from '../httpClient';
import type { BackendApiEnvelope, BackendApiPage, BackendUnknownRecord } from '../types';

export type ChatSessionDto = BackendUnknownRecord;
export type ChatSessionMessageDto = BackendUnknownRecord;

export const CHAT_SESSION_ENDPOINTS = {
  detail: (session_id: string) => `/openapi/v1/chat/sessions/${session_id}`,
  messages: (session_id: string) => `/openapi/v1/chat/sessions/${session_id}/messages`,
};

// 查询聊天会话详情。
export function getChatSession(session_id: string) {
  return backendRequest<BackendApiEnvelope<ChatSessionDto>>(CHAT_SESSION_ENDPOINTS.detail(session_id), {
    method: 'GET',
  });
}

// 查询聊天会话消息列表。
export function listChatSessionMessages(session_id: string, params?: BackendUnknownRecord) {
  return backendRequest<BackendApiEnvelope<BackendApiPage<ChatSessionMessageDto>>>(
    CHAT_SESSION_ENDPOINTS.messages(session_id),
    { method: 'GET', params },
  );
}
