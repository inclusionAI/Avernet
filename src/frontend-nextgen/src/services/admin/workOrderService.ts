// 工单中心 Service：三视图查询 / 分类筛选 / 详情 / 审批 / 通知已读。
// 契约对齐 clawweb=Avernet：user_id query 必填（Service 经 resolveUserId 注入）；分页 page_no；
// query_type=INITIATED_BY_ME、item_type=NOTICE；审批走统一 /approval 入口（decision=APPROVED/REJECTED，reject 非空）。
// 错误标准化（catch -> {message,apiPath}），不 throw 到 Component。

import { mapWorkOrderDto, mapWorkOrderList } from '@/domain/admin/mappers';
import type {
  ApprovalAvailability,
  ServiceError,
  WorkOrder,
  WorkOrderCategory,
  WorkOrderListQuery,
  WorkOrderListResult,
  WorkOrderView,
} from '@/domain/admin/models';
import { notificationService } from '@/services/admin/notificationService';
import { ensureUserId, ensureUserName } from '@/services/admin/userIdentity';
import { getWorkOrderDetail, listWorkOrders, submitWorkOrderApproval } from '@/services/backendApi';
import type { WorkOrderListParams } from '@/services/backendApi/admin/workOrderController';
import { BackendRequestError } from '@/services/backendApi/httpClient';
import { extractFriendlyErrorMessage, formatApiPath } from '@/utils/requestErrorHandler';

export interface WorkOrderServiceResult<T> {
  data?: T;
  error?: ServiceError;
}

function toServiceError(e: unknown): ServiceError {
  if (e instanceof BackendRequestError) {
    return { message: e.message, apiPath: e.apiPath };
  }
  return { message: extractFriendlyErrorMessage(e), apiPath: formatApiPath() };
}

const MISSING_IDENTITY_ERROR: ServiceError = {
  message: '未获取到当前用户身份，请刷新后重试',
  apiPath: formatApiPath(),
};

/** 内部视图名 → 后端 query_type 取值映射。 */
const VIEW_TO_QUERY_TYPE: Record<WorkOrderView, string> = {
  pending_mine: 'PENDING_FOR_ME',
  initiated_mine: 'INITIATED_BY_ME',
  processed: 'PROCESSED_BY_ME',
};

/** 三视图 × 分类 → 查询参数映射：统一用 query_type 区分视图；通知类 item_type 取后端 NOTICE。 */
function buildListParams(query: WorkOrderListQuery, user_id: string): WorkOrderListParams {
  const p: WorkOrderListParams = {
    user_id,
    page_no: query.page ?? 1,
    page_size: query.pageSize ?? 20,
    query_type: VIEW_TO_QUERY_TYPE[query.view],
  };

  // 分类筛选：后端 WorkOrderItemType 为 ALL/APPROVAL/NOTICE（model 对外 NOTIFICATION，请求侧翻译）
  if (query.category === 'APPROVAL') p.item_type = 'APPROVAL';
  else if (query.category === 'NOTIFICATION') p.item_type = 'NOTICE';
  else p.item_type = 'ALL';

  if (query.keyword?.trim()) p.keyword = query.keyword.trim();
  return p;
}

export const workOrderService = {
  /** 三视图 × 分类查询工单列表。 */
  async list(query: WorkOrderListQuery): Promise<WorkOrderServiceResult<WorkOrderListResult>> {
    const user_id = await ensureUserId();
    if (!user_id) return { error: MISSING_IDENTITY_ERROR };
    try {
      const resp = await listWorkOrders(buildListParams(query, user_id));
      return { data: mapWorkOrderList(resp.data) };
    } catch (e) {
      return { error: toServiceError(e) };
    }
  },

  /** 工单详情。 */
  async getDetail(workOrderId: number | string): Promise<WorkOrderServiceResult<WorkOrder | undefined>> {
    const user_id = await ensureUserId();
    if (!user_id) return { error: MISSING_IDENTITY_ERROR };
    try {
      const resp = await getWorkOrderDetail(workOrderId, { user_id });
      if (!resp.data) return { data: undefined };
      return { data: mapWorkOrderDto(resp.data).item };
    } catch (e) {
      return { error: toServiceError(e) };
    }
  },

  /** 审批可用性判定（plan §4）。 */
  canApprove(wo: WorkOrder): ApprovalAvailability {
    if (wo.status !== 'PENDING') return { ok: false, reason: '该工单已处理' };
    if (!wo.canApprove) return { ok: false, reason: '无审批权限' };
    return { ok: true };
  },

  /** 同意工单（remark 可空，后端 review_remark 允许 null）。统一审批入口 decision=APPROVED。 */
  async approve(workOrderId: number | string, remark?: string): Promise<WorkOrderServiceResult<boolean>> {
    const user_id = await ensureUserId();
    if (!user_id) return { error: MISSING_IDENTITY_ERROR };
    // 审批人花名：后端无用户表，需在审批时随 user_id 写入工单 reviewer 展示名，
    // 否则申请人一侧「审批人」只能看到工号。取不到或回落为工号时不传（同 requestJoin 契约）。
    const user_name = await ensureUserName();
    try {
      const params: { user_id: string; user_name?: string } = { user_id };
      if (user_name && user_name !== user_id) params.user_name = user_name;
      await submitWorkOrderApproval(
        workOrderId,
        { decision: 'APPROVED', review_remark: remark?.trim() || null },
        params,
      );
      return { data: true };
    } catch (e) {
      return { error: toServiceError(e) };
    }
  },

  /** 驳回工单（remark 必填非空，后端否则 422）。统一审批入口 decision=REJECTED。 */
  async reject(workOrderId: number | string, remark: string): Promise<WorkOrderServiceResult<boolean>> {
    const user_id = await ensureUserId();
    if (!user_id) return { error: MISSING_IDENTITY_ERROR };
    // 审批人花名：同 approve，REJECTED 时也记录审批人展示名。
    const user_name = await ensureUserName();
    try {
      const params: { user_id: string; user_name?: string } = { user_id };
      if (user_name && user_name !== user_id) params.user_name = user_name;
      await submitWorkOrderApproval(workOrderId, { decision: 'REJECTED', review_remark: remark.trim() }, params);
      return { data: true };
    } catch (e) {
      return { error: toServiceError(e) };
    }
  },

  /** 标记通知已读（查看通知类工单时调，复用 notificationService）。 */
  async markNotificationRead(notificationId: number | string): Promise<WorkOrderServiceResult<boolean>> {
    const r = await notificationService.markOneRead(notificationId);
    if (r.unsupported) return { error: MISSING_IDENTITY_ERROR };
    return { data: r.data ? true : undefined, error: r.error };
  },

  /** 通知类详情（查看通知类工单时调，走 /openapi/v1/work-order-notifications/{id}，
   *  而非 work-orders/{work_order_id}；接收人由 SSO cookie 识别，无需 user_id）。 */
  async getNotificationDetail(notificationId: number | string): Promise<WorkOrderServiceResult<WorkOrder | undefined>> {
    const r = await notificationService.getDetail(notificationId);
    if (r.unsupported) return { error: MISSING_IDENTITY_ERROR };
    return { data: r.data, error: r.error };
  },
};

export type { WorkOrderCategory, WorkOrderView };
