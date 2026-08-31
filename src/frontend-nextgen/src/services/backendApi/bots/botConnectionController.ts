import { backendRequest } from '../httpClient';
import type { BackendApiEnvelope, BackendUnknownRecord } from '../types';

export const BOT_CONNECTION_ENDPOINTS = { detail: (bot_id: string) => `/openapi/v1/bots/connection/${bot_id}` };

// 查询 Bot 连接状态。
export function getBotConnection(bot_id: string) {
  return backendRequest<BackendApiEnvelope<BackendUnknownRecord>>(BOT_CONNECTION_ENDPOINTS.detail(bot_id), {
    method: 'GET',
  });
}
