import { useErrorNotifyStore } from '@/stores/errorNotifyStore';
import { backendRequest, BackendRequestError } from '../httpClient';
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

/** 获取 Worker 配置：GET /openapi/v1/bcsfuse/workers/{worker_id}/config。
 *
 * 404 语义为该 Worker 尚未配置画像公开(Bot 未开启允许其他 Bot 可添加好友),属可降级态而非用户可见异常:
 * 协议层 httpClient 已为任意非 2xx enqueue 默认 toast,此处仅就 status===404 cancel 该 toastKey 静默,
 * 仍照常上抛 BackendRequestError 供调用方降级(collaborationPrivacyRuntimeAdapter → profilePublicStatus
 * 'unavailable' 置灰开关;bcsfuseService → fusionEnable false)。其余状态码维持默认提示。*/
export async function getWorkerConfig(worker_id: string, signal?: AbortSignal) {
  try {
    const response = await backendRequest<BcsfuseWorkerConfigResponse>(
      `/openapi/v1/bcsfuse/workers/${worker_id}/config`,
      {
        method: 'GET',
        signal,
      },
    );
    return unwrapWorkerConfigResponse(response);
  } catch (error) {
    if (error instanceof BackendRequestError && error.status === 404 && error.toastKey) {
      useErrorNotifyStore.getState().cancel(error.toastKey);
    }
    throw error;
  }
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
