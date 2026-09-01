import { backendRequest } from '../httpClient';
import { isEnvelopeFailure, type BackendApiEnvelope } from '../types';

/** Bot 智能融合 Worker 配置 DTO。 */
export interface BcsfuseWorkerConfigDto {
  success: boolean;
  worker_id: string;
  fusion_enable: boolean;
  version: number;
  server_ip?: string;
}

export interface BcsfuseWorkerConfigPatchBody {
  fusion_enable: boolean;
}

type BcsfuseWorkerConfigResponse = BcsfuseWorkerConfigDto | BackendApiEnvelope<BcsfuseWorkerConfigDto>;

function isWorkerConfigDto(value: unknown): value is BcsfuseWorkerConfigDto {
  if (typeof value !== 'object' || value === null) return false;
  const config = value as Partial<BcsfuseWorkerConfigDto>;
  return (
    typeof config.success === 'boolean' &&
    typeof config.worker_id === 'string' &&
    typeof config.fusion_enable === 'boolean' &&
    typeof config.version === 'number' &&
    (config.server_ip === undefined || typeof config.server_ip === 'string')
  );
}

/**
 * BCSFuse 在不同网关版本可能直接返回 data，或返回统一 BackendApiEnvelope。
 * 协议差异在 Controller 层收口，业务层始终只消费 Worker 配置 DTO。
 */
function unwrapWorkerConfigResponse(response: BcsfuseWorkerConfigResponse): BcsfuseWorkerConfigDto {
  if (isWorkerConfigDto(response)) return response;
  if (response && typeof response === 'object' && 'data' in response) {
    if (isEnvelopeFailure(response)) {
      throw new Error(response.message || 'Bot 画像公开配置接口返回失败');
    }
    if (isWorkerConfigDto(response.data)) return response.data;
  }
  throw new Error('Bot 画像公开配置接口返回了无法识别的数据');
}

/** 融合问答请求 body 参数。 */
export interface FuseRequestBody {
  session_id: string;
  question: string;
  driver_bot_id: string;
  participants: string[];
  fusion_mode: 'bot_profile_fuse';
  options: { timeout_ms: number; refresh?: boolean };
}

/** 融合问答响应数据。 */
export interface FuseResponseData {
  group_id?: string;
  session_id?: string;
  fusion_id?: string;
  question?: string;
  driver_bot_id?: string;
  recommendation?: { summary: string; decision?: string; risks?: string[]; next_actions?: string[] };
  partial_success?: boolean;
  warnings?: string[];
  errors?: string[];
  error?: string;
}

/** 获取 Worker 配置：GET /openapi/v1/bcsfuse/workers/{worker_id}/config。 */
export async function getWorkerConfig(worker_id: string, signal?: AbortSignal) {
  const response = await backendRequest<BcsfuseWorkerConfigResponse>(
    `/openapi/v1/bcsfuse/workers/${worker_id}/config`,
    {
      method: 'GET',
      signal,
    },
  );
  return unwrapWorkerConfigResponse(response);
}

/** 修改 Worker 的 Bot 画像公开配置：PUT /openapi/v1/bcsfuse/workers/{worker_id}/config。 */
export async function updateWorkerConfig(worker_id: string, body: BcsfuseWorkerConfigPatchBody, signal?: AbortSignal) {
  const response = await backendRequest<BcsfuseWorkerConfigResponse>(
    `/openapi/v1/bcsfuse/workers/${worker_id}/config`,
    {
      method: 'PUT',
      data: body,
      headers: { 'Content-Type': 'application/json' },
      signal,
    },
  );
  return unwrapWorkerConfigResponse(response);
}

/** 调用 Bot 智能融合：POST /openapi/v1/bcsfuse/groups/{group_id}/fuse。 */
export function postFuse(group_id: string, body: FuseRequestBody) {
  return backendRequest<FuseResponseData>(`/openapi/v1/bcsfuse/groups/${group_id}/fuse`, {
    method: 'POST',
    data: body,
    headers: { 'Content-Type': 'application/json' },
  });
}
