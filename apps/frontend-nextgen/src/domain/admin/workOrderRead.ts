// 通知中心「已读同步」纯函数。
// 背景契约：List VO 里通知类（NOTICE）行的 work_order_id 恒为 null（后端 _list_item 序列化 None），
// mappers 兜底把 workOrderId 落成 0——通知类条目若按 workOrderId 比对会彼此视为同一条，
// 出现「查看一条未读通知，同列表所有通知在界面上全部变成已查看」（服务端已读状态并不随之变化）。
// 因此通知类唯一键 = notificationId；审批类（work_order_id 真实存在）唯一键 = workOrderId。

import type { WorkOrder } from './models';

/** 通知类条目（itemType=NOTIFICATION）判定。 */
export function isNotificationItem(wo: Pick<WorkOrder, 'itemType'>): boolean {
  return wo.itemType === 'NOTIFICATION';
}

/**
 * 两条通知中心条目是否同一实体：通知类按 notificationId 比对，
 * 审批类按 workOrderId 比对。跨类型（通知 vs 审批）恒为不同条目。
 */
export function isSameWorkOrderItem(a: WorkOrder, b: WorkOrder): boolean {
  const aNotification = isNotificationItem(a);
  const bNotification = isNotificationItem(b);
  if (aNotification !== bNotification) return false;
  if (aNotification) return String(a.notificationId) === String(b.notificationId);
  return String(a.workOrderId) === String(b.workOrderId);
}

/**
 * 就地把列表中与 notificationId 同条的通知类条目标记为已读，其余条目原样返回。
 * 审批类条目不受影响（审批已读由「标记为已读」显式触发，查看不点亮）。
 */
export function markNotificationReadInList(items: WorkOrder[], notificationId: number | string): WorkOrder[] {
  return items.map((it) =>
    isNotificationItem(it) && String(it.notificationId) === String(notificationId) ? { ...it, isRead: true } : it,
  );
}
