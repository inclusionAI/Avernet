import { backendRequest } from '../httpClient';
import type { BackendApiEnvelope, BackendApiPage, BackendUnknownRecord } from '../types';

export type BotModelDto = BackendUnknownRecord;
export const BOT_MODEL_ENDPOINTS = {
  list: (bot_id: string) => `/openapi/v1/bots/models/${bot_id}`,
  detail: (bot_id: string, model_id: string) => `/openapi/v1/bots/models/${bot_id}/${model_id}`,
};
// 查询 Bot 模型列表。
export function listBotModels(bot_id: string, params?: BackendUnknownRecord) {
  return backendRequest<BackendApiEnvelope<BackendApiPage<BotModelDto>>>(BOT_MODEL_ENDPOINTS.list(bot_id), {
    method: 'GET',
    params,
  });
}
// 查询 Bot 模型详情。
export function getBotModel(bot_id: string, model_id: string) {
  return backendRequest<BackendApiEnvelope<BotModelDto>>(BOT_MODEL_ENDPOINTS.detail(bot_id, model_id), {
    method: 'GET',
  });
}
