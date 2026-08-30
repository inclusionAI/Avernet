// 工单中心协议层 Controller。
// 契约对齐 clawweb=Avernet（/openapi/v1/bots/work-orders）：user_id query 必填；
// 审批统一入口 POST .../{id}/approval（decision=APPROVED/REJECTED，reject 时 review_remark 必填）。分页 page_no。
// DTO=BackendUnknownRecord，由 domain/admin/mappers 读字段映射。

import { backendRequest } from '../httpClient';
import type { BackendApiEnvelope, BackendApiPage, BackendUnknownRecord } from '../types';

export type WorkOrderDto = BackendUnknownRecord;

export const WORK_ORDER_ENDPOINTS = {
  list: '/openapi/v1/bots/work-orders',
  detail: (work_order_id: number | string) => `/openapi/v1/bots/work-orders/${work_order_id}`,
  approval: (work_order_id: number | string) => `/openapi/v1/bots/work-orders/${work_order_id}/approval`,
};

export interface WorkOrderListParams {
  [key: string]: unknown;
  user_id: string;
  /** 视图过滤：PENDING_FOR_ME=待我处理 / INITIATED_BY_ME=我发起的 / PROCESSED_BY_ME=已处理 */
  query_type?: string;
  /** 全部 ALL / 审批类 APPROVAL / 通知类 NOTICE */
  item_type?: string;
  page_no?: number;
  page_size?: number;
  // 以下后端 list_work_orders 未声明（FastAPI 忽略未知 query，保留供后端后续扩展）：
  applicant_user_id?: string;
  reviewer_user_id?: string;
  notification_category?: string;
}

export interface WorkOrderApprovalBody {
  /** 审批决策：APPROVED 通过 / REJECTED 驳回 */
  decision: 'APPROVED' | 'REJECTED';
  /** 审批意见；REJECTED 时必填非空，APPROVED 可空 */
  review_remark?: string | null;
}

// 查询我的工单列表（消息通知铃铛与工单中心共用，靠参数区分）。
export function listWorkOrders(params: WorkOrderListParams) {
  return backendRequest<BackendApiEnvelope<BackendApiPage<WorkOrderDto>>>(WORK_ORDER_ENDPOINTS.list, {
    method: 'GET',
    params,
  });
}

// 查询工单详情。
export function getWorkOrderDetail(work_order_id: number | string, params: { user_id: string }) {
  return backendRequest<BackendApiEnvelope<WorkOrderDto>>(WORK_ORDER_ENDPOINTS.detail(work_order_id), {
    method: 'GET',
    params,
  });
}

// 统一审批：POST /openapi/v1/bots/work-orders/{id}/approval。decision=APPROVED/REJECTED；
// REJECTED 时 review_remark 必填非空（否则后端 422）；APPROVED 可空。
// 审批人 user_id query 必填；user_name（花名）为可选 query——后端无用户表，记录到工单 reviewer 展示名。
export function submitWorkOrderApproval(
  work_order_id: number | string,
  body: WorkOrderApprovalBody,
  params: { user_id: string; /** 审批人花名（后端无用户表，记录到工单 reviewer 展示名；可选）。 */ user_name?: string },
) {
  return backendRequest<BackendApiEnvelope<WorkOrderDto>>(WORK_ORDER_ENDPOINTS.approval(work_order_id), {
    method: 'POST',
    params,
    data: body,
  });
}
