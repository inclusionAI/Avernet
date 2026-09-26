import { backendRequest } from '../httpClient';
import type { BackendApiEnvelope } from '../types';

export interface ChatMessageMetadata {
  biz_task_id?: string;
  biz_scene?: string;
  [key: string]: unknown;
}
export interface CreateChatMessageBody {
  bot_id: string;
  message: string;
  message_id?: string | null;
  callback_url?: string | null;
  metadata?: ChatMessageMetadata | null;
}
export interface ChatMessageData {
  message_id: string;
  session_id?: string;
  accepted?: boolean;
  [key: string]: unknown;
}

// Endpoint 常量（保留供外部协议层测试与诊断工具使用）。
export const CHAT_MESSAGE_ENDPOINTS = {
  create: '/openapi/v1/chat/messages',
  stream: '/openapi/v1/chat/messages/stream',
  detail: (message_id: string) => `/openapi/v1/chat/messages/${message_id}`,
};

// 发送普通聊天消息。
export async function sendMessage(body: CreateChatMessageBody) {
  return backendRequest<BackendApiEnvelope<ChatMessageData>>('/openapi/v1/chat/messages', {
    method: 'POST',
    data: body,
  });
}

// 发送流式聊天消息。
export async function sendStreamMessage(body: CreateChatMessageBody) {
  return backendRequest<BackendApiEnvelope<ChatMessageData>>('/openapi/v1/chat/messages/stream', {
    method: 'POST',
    data: body,
  });
}

// 查询单条消息详情。
export async function getMessage(message_id: string) {
  return backendRequest<BackendApiEnvelope<ChatMessageData>>(`/openapi/v1/chat/messages/${message_id}`, {
    method: 'GET',
  });
}
