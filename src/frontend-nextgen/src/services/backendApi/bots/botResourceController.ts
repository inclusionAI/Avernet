import { backendRequest } from '../httpClient';
import type { BackendApiEnvelope, BackendApiPage, BackendUnknownRecord } from '../types';

export type BotResourceDto = BackendUnknownRecord;
export const BOT_RESOURCE_ENDPOINTS = {
  list: '/openapi/v1/bots/resources',
  checkName: '/openapi/v1/bots/resources/check-name',
  upload: '/openapi/v1/bots/resources/upload',
  detail: (resource_id: string) => `/openapi/v1/bots/resources/${resource_id}`,
  download: (resource_id: string) => `/openapi/v1/bots/resources/${resource_id}/download`,
  preview: (resource_id: string) => `/openapi/v1/bots/resources/${resource_id}/preview`,
};
// 查询资源列表。
export function listBotResources(params?: BackendUnknownRecord) {
  return backendRequest<BackendApiEnvelope<BackendApiPage<BotResourceDto>>>(BOT_RESOURCE_ENDPOINTS.list, {
    method: 'GET',
    params,
  });
}
// 创建资源。
export function createBotResource(body: BackendUnknownRecord) {
  return backendRequest<BackendApiEnvelope<BotResourceDto>>(BOT_RESOURCE_ENDPOINTS.list, {
    method: 'POST',
    data: body,
  });
}
// 查询资源详情。
export function getBotResource(resource_id: string) {
  return backendRequest<BackendApiEnvelope<BotResourceDto>>(BOT_RESOURCE_ENDPOINTS.detail(resource_id), {
    method: 'GET',
  });
}
