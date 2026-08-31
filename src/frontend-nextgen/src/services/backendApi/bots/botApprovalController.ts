import { backendRequest } from '../httpClient';
import type { BackendApiEnvelope, BackendUnknownRecord } from '../types';

export const BOT_APPROVAL_ENDPOINTS = {
  mode: (bot_id: string) => `/openapi/v1/bots/approvals/${bot_id}/mode`,
  modes: (bot_id: string) => `/openapi/v1/bots/approvals/${bot_id}/modes`,
};

// 查询 Bot 审批模式。
export function getBotApprovalMode(bot_id: string) {
  return backendRequest<BackendApiEnvelope<BackendUnknownRecord>>(BOT_APPROVAL_ENDPOINTS.mode(bot_id), {
    method: 'GET',
  });
}
// 更新 Bot 审批模式。
export function updateBotApprovalMode(bot_id: string, body: BackendUnknownRecord) {
  return backendRequest<BackendApiEnvelope<BackendUnknownRecord>>(BOT_APPROVAL_ENDPOINTS.mode(bot_id), {
    method: 'PUT',
    data: body,
  });
}
// 查询可用审批模式。
export function listBotApprovalModes(bot_id: string) {
  return backendRequest<BackendApiEnvelope<BackendUnknownRecord[]>>(BOT_APPROVAL_ENDPOINTS.modes(bot_id), {
    method: 'GET',
  });
}
