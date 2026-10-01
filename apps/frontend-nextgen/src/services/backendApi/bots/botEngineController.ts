import { backendRequest } from '../httpClient';
import type { BackendApiEnvelope, BackendUnknownRecord } from '../types';

export const BOT_ENGINE_ENDPOINTS = {
  config: (bot_id: string) => `/openapi/v1/bots/${bot_id}/engine-config`,
  available: (bot_id: string) => `/openapi/v1/bots/engine/${bot_id}/available`,
  capabilities: (bot_id: string) => `/openapi/v1/bots/engine/${bot_id}/capabilities`,
  status: (bot_id: string) => `/openapi/v1/bots/engine/${bot_id}/status`,
};

// 查询 Bot 引擎配置。
export function getBotEngineConfig(bot_id: string) {
  return backendRequest<BackendApiEnvelope<BackendUnknownRecord>>(BOT_ENGINE_ENDPOINTS.config(bot_id), {
    method: 'GET',
  });
}
// 更新 Bot 引擎配置。
export function updateBotEngineConfig(bot_id: string, body: BackendUnknownRecord) {
  return backendRequest<BackendApiEnvelope<BackendUnknownRecord>>(BOT_ENGINE_ENDPOINTS.config(bot_id), {
    method: 'PUT',
    data: body,
  });
}
// 查询 Bot 引擎能力。
export function getBotEngineCapabilities(bot_id: string) {
  return backendRequest<BackendApiEnvelope<BackendUnknownRecord>>(BOT_ENGINE_ENDPOINTS.capabilities(bot_id), {
    method: 'GET',
  });
}
