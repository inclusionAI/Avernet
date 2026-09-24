import { backendRequest } from '../httpClient';
import type { BackendApiEnvelope, BackendUnknownRecord } from '../types';

export interface LocalBotCreateRequest {
  bot_name: string;
  bot_desc: string;
  engine: string;
  machine_id: string;
  mount_path: string;
}
export interface LocalDeviceDto {
  machine_id: string;
  machine_name: string;
  hostname: string;
  status: string;
}
const root = '/openapi/v1/bots';
export const listLocalDevices = (page = 1) =>
  backendRequest<BackendApiEnvelope<{ items: LocalDeviceDto[]; total: number }>>(`${root}/local/devices`, {
    params: { page, page_size: 100 },
  });
export const getLocalDirectory = (machineId: string) =>
  backendRequest<BackendApiEnvelope<{ absolute_path: string }>>(
    `${root}/local/devices/${encodeURIComponent(machineId)}/files`,
  );
export const createLocalBot = (data: LocalBotCreateRequest) =>
  backendRequest<BackendApiEnvelope<BackendUnknownRecord>>(`${root}/local`, { method: 'POST', data });
export const pollLocalAuthorization = (botId: string, params: LocalBotCreateRequest) =>
  backendRequest<BackendApiEnvelope<{ status: string; message?: string; bot?: BackendUnknownRecord }>>(
    `${root}/${encodeURIComponent(botId)}/local/auth-status`,
    { params: { ...params } },
  );
export const getLocalStartProgress = (botId: string) =>
  backendRequest<BackendApiEnvelope<BackendUnknownRecord>>(`${root}/${encodeURIComponent(botId)}/local/start-progress`);
export const openLocalFolder = (botId: string, folderPath?: string) =>
  backendRequest(`${root}/${encodeURIComponent(botId)}/local/open-folder`, {
    method: 'POST',
    data: { folder_path: folderPath },
  });
