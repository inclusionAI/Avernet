import { backendRequest } from '../httpClient';

/** Bot 智能融合 Worker 配置 DTO。 */
export interface BcsfuseWorkerConfigDto {
  success: boolean;
  worker_id: string;
  fusion_enable: boolean;
  version: number;
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
export function getWorkerConfig(worker_id: string) {
  return backendRequest<BcsfuseWorkerConfigDto>(`/openapi/v1/bcsfuse/workers/${worker_id}/config`, {
    method: 'GET',
  });
}

/** 调用 Bot 智能融合：POST /openapi/v1/bcsfuse/groups/{group_id}/fuse。 */
export function postFuse(group_id: string, body: FuseRequestBody) {
  return backendRequest<FuseResponseData>(`/openapi/v1/bcsfuse/groups/${group_id}/fuse`, {
    method: 'POST',
    data: body,
    headers: { 'Content-Type': 'application/json' },
  });
}
