import { backendRequest } from '../httpClient';
import type { BackendApiEnvelope, BackendApiPage, BackendUnknownRecord } from '../types';

export type BotMcpServerDto = BackendUnknownRecord;
export const BOT_MCP_ENDPOINTS = {
  servers: '/openapi/v1/bots/mcp/servers',
  server: (server_code: string) => `/openapi/v1/bots/mcp/servers/${server_code}`,
  config: (server_code: string) => `/openapi/v1/bots/mcp/servers/${server_code}/config`,
  permissions: (server_code: string) => `/openapi/v1/bots/mcp/servers/${server_code}/permissions`,
  tenants: '/openapi/v1/bots/mcp/tenants',
};
// 查询 MCP 服务列表。
export function listBotMcpServers(params?: BackendUnknownRecord) {
  return backendRequest<BackendApiEnvelope<BackendApiPage<BotMcpServerDto>>>(BOT_MCP_ENDPOINTS.servers, {
    method: 'GET',
    params,
  });
}
// 查询 MCP 服务详情。
export function getBotMcpServer(server_code: string) {
  return backendRequest<BackendApiEnvelope<BotMcpServerDto>>(BOT_MCP_ENDPOINTS.server(server_code), { method: 'GET' });
}
// 更新 MCP 服务配置。
export function updateBotMcpServerConfig(server_code: string, body: BackendUnknownRecord) {
  return backendRequest<BackendApiEnvelope<BackendUnknownRecord>>(BOT_MCP_ENDPOINTS.config(server_code), {
    method: 'PUT',
    data: body,
  });
}
