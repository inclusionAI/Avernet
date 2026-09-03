import { backendRequest } from '../httpClient';
import type { BackendApiEnvelope } from '../types';

export const COLLABORATION_PUBLICATION_ENDPOINTS = {
  /** POST /openapi/v1/collaboration/bots/{bot_uuid}/public */
  publish: (botUuid: string) => `/openapi/v1/collaboration/bots/${botUuid}/public`,
} as const;

/** 部门可见范围项。 */
export interface ViewDept {
  deptNo: string;
  deptName: string;
}

/** 发布请求体。 */
export interface BcsPublicRequest {
  /** 发布域：user = 用户可见，agent = Agent/群聊可见。 */
  public_scope: 'user' | 'agent';
  /** 部门范围；null 或空数组表示不限部门。 */
  view_depts?: ViewDept[] | null;
  /** public = 公开（是否限制部门由 view_depts 区分）；private = 不公开。 */
  visibility?: 'public' | 'private';
}

/** 发布结果。除 success 外，快速路径可能不返回审批字段。 */
export interface BcsPublishResult {
  success: boolean;
  puid?: string | null;
  approval_url?: string | null;
  state?: string | null;
  last_operate?: string | null;
  error_msg?: string | null;
  visibility?: string | null;
  visibility_field?: string | null;
}

/** 提交 Bot 公开范围变更（审批工单或快速收回）。 */
export function publishBotPublic(botUuid: string, userId: string, body: BcsPublicRequest, signal?: AbortSignal) {
  return backendRequest<BackendApiEnvelope<BcsPublishResult>>(COLLABORATION_PUBLICATION_ENDPOINTS.publish(botUuid), {
    method: 'POST',
    params: { user_id: userId },
    data: body,
    injectUserId: false,
    signal,
  });
}
