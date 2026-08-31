// 消息通知 Service：铃铛未读数 / 最近 N 条 / 全部已读 / 单条已读。
// 契约对齐 clawweb=Avernet：user_id query 必填（Service 经 resolveUserId 注入）；分页 page_no。
// 错误标准化（catch BackendRequestError -> {message,apiPath}），不 throw 到上层。
// workspace 未初始化（activeIdentityId 为 null）时返回 unsupported，轮询/visibilitychange 自愈。

import { mapWorkOrderDto, mapWorkOrderList } from '@/domain/admin/mappers';
import type { NotificationSummary, ServiceError, UnsupportedResult, WorkOrder } from '@/domain/admin/models';
import { ensureUserId } from '@/services/admin/userIdentity';
import {
  fetchUnreadCount as fetchUnreadCountApi,
  getNotificationDetail,
  listNotificationsForBell,
  markAllNotificationsRead,
  markNotificationRead,
} from '@/services/backendApi';
import { BackendRequestError } from '@/services/backendApi/httpClient';
import { extractFriendlyErrorMessage, formatApiPath } from '@/utils/requestErrorHandler';

export interface NotificationServiceResult<T> {
  data?: T;
  error?: ServiceError;
  unsupported?: boolean;
}

function toServiceError(e: unknown): ServiceError {
  if (e instanceof BackendRequestError) {
    return { message: e.message, apiPath: e.apiPath };
  }
  return { message: extractFriendlyErrorMessage(e), apiPath: formatApiPath() };
}

export const notificationService = {
  /** 红点总数（铃铛 + 工单中心「待我处理」徽标共用）。
   * 后端 UnreadCountResponse 同时返回 unread_count / pending_approval_count / unread_notice_count / badge_count，
   * 铃铛红点语义是「需要我处理的总数」(待审批 + 未读通知)，因此读 badge_count。
   * 单读 unread_count 会漏掉待审批工单的提醒。 */
  async fetchUnreadCount(): Promise<NotificationServiceResult<number>> {
    const user_id = await ensureUserId();
    if (!user_id) return { unsupported: true };
    try {
      const resp = await fetchUnreadCountApi({ user_id });
      return { data: resp.data?.badge_count ?? 0 };
    } catch (e) {
      return { error: toServiceError(e) };
    }
  },

  /** 最近 N 条通知（铃铛 tooltip）。按 gmt_modified desc，未读优先。 */
  async fetchRecentNotifications(limit = 3): Promise<NotificationServiceResult<NotificationSummary[]>> {
    const user_id = await ensureUserId();
    if (!user_id) return { unsupported: true };
    try {
      const resp = await listNotificationsForBell({
        user_id,
        item_type: 'ALL',
        page_no: 1,
        page_size: limit,
      });
      const mapped = mapWorkOrderList(resp.data);
      const recent = mapped.items.map((wo) => ({
        itemId: wo.itemId,
        title: wo.title,
        content: wo.content,
        gmtModified: wo.gmtModified,
        itemType: wo.itemType,
        notificationId: wo.notificationId,
        isRead: wo.isRead,
      }));
      return { data: recent };
    } catch (e) {
      return { error: toServiceError(e) };
    }
  },

  /** 全部标记已读。 */
  async markAllRead(): Promise<NotificationServiceResult<{ updatedCount: number }>> {
    const user_id = await ensureUserId();
    if (!user_id) return { unsupported: true };
    try {
      const resp = await markAllNotificationsRead({ user_id });
      return { data: { updatedCount: resp.data?.updated_count ?? 0 } };
    } catch (e) {
      return { error: toServiceError(e) };
    }
  },

  /** 标记单条通知已读。后端 ActingCallerDep require_user_id 强制 user_id query；activeIdentityId 未就绪时降级 unsupported。 */
  async markOneRead(notification_id: number | string): Promise<NotificationServiceResult<boolean>> {
    const user_id = await ensureUserId();
    if (!user_id) return { unsupported: true };
    try {
      await markNotificationRead(notification_id, { user_id });
      return { data: true };
    } catch (e) {
      return { error: toServiceError(e) };
    }
  },

  /** 查询通知详情（GET /bots/work-order-notifications/{id}）。后端 require_user_id 强制 user_id query。 */
  async getDetail(notification_id: number | string): Promise<NotificationServiceResult<WorkOrder | undefined>> {
    const user_id = await ensureUserId();
    if (!user_id) return { unsupported: true };
    try {
      const resp = await getNotificationDetail(notification_id, { user_id });
      if (!resp.data) return { data: undefined };
      return { data: mapWorkOrderDto(resp.data).item };
    } catch (e) {
      return { error: toServiceError(e) };
    }
  },

  /** Open Core 缺 identity 降级判断的统一出口（本期 identity 可选，暂多处复用）。 */
  unsupported(reason: string): NotificationServiceResult<UnsupportedResult> {
    return { unsupported: true, data: { unsupported: true, reason } };
  },
};
