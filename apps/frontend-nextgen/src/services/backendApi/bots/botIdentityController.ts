import { backendRequest } from '../httpClient';
import type { BotRuntimeStage } from '@/domain/botWorkshop';
import type { BackendApiEnvelope, BackendUnknownRecord } from '../types';

export const BOT_IDENTITY_ENDPOINTS = {
  authStatus: (bot_id: string) => `/openapi/v1/bots/${bot_id}/auth-status`,
  passport: (bot_id: string) => `/openapi/v1/bots/${bot_id}/passport`,
  identity: (bot_id: string) => `/openapi/v1/bots/${bot_id}/identity`,
  identityFile: (bot_id: string, file_type: string) => `/openapi/v1/bots/${bot_id}/identity/${file_type}`,
};

// 查询 Bot 身份状态。
export function getBotIdentity(bot_id: string, ownerId?: string, stage?: BotRuntimeStage) {
  return backendRequest<BackendApiEnvelope<BackendUnknownRecord>>(BOT_IDENTITY_ENDPOINTS.identity(bot_id), {
    method: 'GET',
    params: { owner_id: ownerId, stage },
  });
}
// 查询 Bot 身份文件。
export function getBotIdentityFile(bot_id: string, file_type: string, ownerId?: string, stage?: BotRuntimeStage) {
  return backendRequest<BackendApiEnvelope<BackendUnknownRecord>>(
    BOT_IDENTITY_ENDPOINTS.identityFile(bot_id, file_type),
    { method: 'GET', params: { owner_id: ownerId, stage } },
  );
}
// 更新 Bot 身份文件。
export function updateBotIdentityFile(bot_id: string, file_type: string, body: BackendUnknownRecord, ownerId?: string) {
  return backendRequest<BackendApiEnvelope<BackendUnknownRecord>>(
    BOT_IDENTITY_ENDPOINTS.identityFile(bot_id, file_type),
    { method: 'PUT', params: { owner_id: ownerId, stage: 'draft' }, data: body },
  );
}
