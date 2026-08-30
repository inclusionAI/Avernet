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
  /** public = 走审批工单；private = 快速路径直接收回可见性。默认 private。 */
  visibility?: 'public' | 'private';
}

/** 发布结果。 */
export interface BcsPublishResult {
  success: boolean;
  /** 审批工单 ID；快速路径为 null。 */
  puid: string | null;
  /** 审批 URL；快速路径为 null。 */
  approval_url: string | null;
  /** 工单状态：PROCESSING（待审）/ COMPLETED（已终态）。 */
  state: 'PROCESSING' | 'COMPLETED' | null;
  /** 终态标记：AGREE / DISAGREE / CANCEL；PROCESSING 时为 null。 */
  last_operate: string | null;
  error_msg: string | null;
  /** 快速路径返回实际可见性值。 */
  visibility?: string;
  /** 快速路径返回更新的可见性字段名。 */
  visibility_field?: string;
}

/** 提交 Bot 公开范围变更（审批工单或快速收回）。 */
export function publishBotPublic(botUuid: string, body: BcsPublicRequest, signal?: AbortSignal) {
  return backendRequest<BackendApiEnvelope<BcsPublishResult>>(COLLABORATION_PUBLICATION_ENDPOINTS.publish(botUuid), {
    method: 'POST',
    data: body,
    signal,
  });
}
