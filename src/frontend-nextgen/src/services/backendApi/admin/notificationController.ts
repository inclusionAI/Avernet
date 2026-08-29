// 消息通知协议层 Controller。铃铛未读数 / 最近 N 条 / 全部已读 / 单条已读。
// 契约对齐 clawweb=Avernet（/openapi/v1/bots/work-order-notifications）：user_id query 必填。
// 「通知列表」复用 work-orders 端点（与工单中心同源，靠 query 参数区分），仅复用路径常量与 DTO 类型。

import { backendRequest } from '../httpClient';
import type { BackendApiEnvelope, BackendApiPage } from '../types';
import { WORK_ORDER_ENDPOINTS, type WorkOrderDto } from './workOrderController';

export const NOTIFICATION_ENDPOINTS = {
  // 复用 work-orders 列表端点（语义化为「我的消息列表」）。
  list: WORK_ORDER_ENDPOINTS.list,
  readAll: '/openapi/v1/bots/work-order-notifications/read-all',
  // 工单通知端点（/bots 前缀，与 work-orders 同源）。接收人由 SSO cookie 识别，无需 user_id query。
  readOne: (notification_id: number | string) => `/openapi/v1/bots/work-order-notifications/${notification_id}/read`,
  detail: (notification_id: number | string) => `/openapi/v1/bots/work-order-notifications/${notification_id}`,
  unreadCount: '/openapi/v1/bots/work-order-notifications/unread-count',
};

export interface NotificationListParams {
  [key: string]: unknown;
  user_id: string;
  item_type?: string; // ALL / APPROVAL / NOTICE（铃铛默认 ALL）
  page_no?: number;
  page_size?: number;
  is_read?: number; // 0=未读（铃铛未读数过滤）
}

// 查询我的消息列表（铃铛 tooltip 最近 N 条 / 未读数）。
export function listNotificationsForBell(params: NotificationListParams) {
  return backendRequest<BackendApiEnvelope<BackendApiPage<WorkOrderDto>>>(NOTIFICATION_ENDPOINTS.list, {
    method: 'GET',
    params,
  });
}

// 全部标记已读。
export function markAllNotificationsRead(params: { user_id: string }) {
  return backendRequest<BackendApiEnvelope<{ updated_count?: number }>>(NOTIFICATION_ENDPOINTS.readAll, {
    method: 'POST',
    params,
  });
}

/** 标记单条已读响应（契约：POST /openapi/v1/work-order-notifications/{id}/read）。 */
export interface NotificationReadResult {
  notification_id: number;
  is_read: boolean;
  read_at?: string;
}

// 标记单条通知已读。路径参数 notification_id + user_id query（后端 ActingCallerDep require_user_id 强制）。
export function markNotificationRead(notification_id: number | string, params: { user_id: string }) {
  return backendRequest<BackendApiEnvelope<NotificationReadResult>>(NOTIFICATION_ENDPOINTS.readOne(notification_id), {
    method: 'POST',
    params,
  });
}

/** 通知详情响应（契约：GET /bots/work-order-notifications/{id}）。复用 WorkOrderDto 字段集。user_id query 强制。 */
export function getNotificationDetail(notification_id: number | string, params: { user_id: string }) {
  return backendRequest<BackendApiEnvelope<WorkOrderDto>>(NOTIFICATION_ENDPOINTS.detail(notification_id), {
    method: 'GET',
    params,
  });
}

// 查询当前接收人维度的未读通知数量（铃铛红点）。
// 契约（clawweb=Avernet）：同时返回 unread_count / pending_approval_count / unread_notice_count / badge_count；
// 铃铛红点读 badge_count（待审批 + 未读通知总数），语义见 notificationService.fetchUnreadCount。
export function fetchUnreadCount(params: { user_id: string }) {
  return backendRequest<
    BackendApiEnvelope<{
      unread_count?: number;
      pending_approval_count?: number;
      unread_notice_count?: number;
      badge_count?: number;
    }>
  >(NOTIFICATION_ENDPOINTS.unreadCount, {
    method: 'GET',
    params,
  });
}
