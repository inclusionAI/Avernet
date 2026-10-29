// 通知中心已读同步纯函数回归：通知类（NOTICE）行 work_order_id 恒为 null → mapper 兜底 0，
// 按 workOrderId 比对会把同列表所有通知误判为同一条（查看一条 → 全部「已查看」的线上 bug）。

import type { WorkOrder } from '@/domain/admin/models';
import { isSameWorkOrderItem, markNotificationReadInList } from '@/domain/admin/workOrderRead';

const NOTIFICATION_BASE = {
  itemType: 'NOTIFICATION',
  workOrderId: 0,
  bizType: 'BOT_PUBLIC_RISK',
  bizId: '37f078a7',
  workOrderNo: undefined,
  notificationCategory: 'SECURITY',
} as const;

function notification(id: number, overrides: Partial<WorkOrder> = {}): WorkOrder {
  return {
    itemId: `NOTIFICATION_${id}`,
    notificationId: id,
    title: '公开检测通知',
    content: '检测已发起',
    contentRaw: undefined,
    status: 'PENDING',
    statusLabel: '待处理',
    typeLabel: '公开检测',
    typeTone: 'info',
    isRead: false,
    readAt: undefined,
    applicantUserId: undefined,
    applicantName: undefined,
    applyReason: undefined,
    reviewerUserId: undefined,
    reviewerUserName: undefined,
    reviewRemark: undefined,
    reviewedAt: undefined,
    recipientUserId: 'u1',
    eventType: 'BOT_PUBLIC_RISK',
    gmtCreated: '2026-10-29 10:00:00',
    gmtModified: '2026-10-29 10:00:00',
    env: undefined,
    canApprove: false,
    ...NOTIFICATION_BASE,
    ...overrides,
  } as unknown as WorkOrder;
}

function approval(id: number, overrides: Partial<WorkOrder> = {}): WorkOrder {
  return notification(id, {
    itemType: 'APPROVAL',
    workOrderId: id,
    notificationId: 0,
    ...overrides,
  });
}

describe('markNotificationReadInList', () => {
  it('只点亮与给定 notificationId 相同的那一条通知，其余通知保持未读', () => {
    const allUnread = [notification(101), notification(202), notification(303)];
    const updated = markNotificationReadInList(allUnread, 202);

    expect(updated[0].isRead).toBe(false);
    expect(updated[1].isRead).toBe(true);
    expect(updated[2].isRead).toBe(false);
  });

  it('通知类 workOrderId 同为 0 不会连坐（回归：按 workOrderId 比对时查看一条全部已读）', () => {
    const allUnread = [notification(101), notification(202), notification(303)];
    // 前置：契约上通知类 workOrderId 恒为 0
    allUnread.forEach((it) => expect(it.workOrderId).toBe(0));

    const updated = markNotificationReadInList(allUnread, 101);
    expect(updated.filter((it) => it.isRead)).toHaveLength(1);
  });

  it('不影响审批类条目（审批已读须由「标记为已读」显式触发）', () => {
    const mixed = [approval(900, { isRead: false }), notification(101)];
    const updated = markNotificationReadInList(mixed, 101);
    expect(updated[0].isRead).toBe(false);
    expect(updated[1].isRead).toBe(true);
  });

  it('列表无匹配通知时原样返回（新对象，不修改入参）', () => {
    const items = [notification(101)];
    const updated = markNotificationReadInList(items, 999);
    expect(updated[0].isRead).toBe(false);
    expect(updated).not.toBe(items);
  });
});

describe('isSameWorkOrderItem', () => {
  it('通知类按 notificationId 判同一：id 不同即不是同一条（workOrderId 同为 0）', () => {
    expect(isSameWorkOrderItem(notification(101), notification(101))).toBe(true);
    expect(isSameWorkOrderItem(notification(101), notification(202))).toBe(false);
  });

  it('审批类按 workOrderId 判同一', () => {
    expect(isSameWorkOrderItem(approval(900), approval(900))).toBe(true);
    expect(isSameWorkOrderItem(approval(900), approval(901))).toBe(false);
  });

  it('通知与审批跨类型恒为不同条目', () => {
    expect(isSameWorkOrderItem(notification(0), approval(0))).toBe(false);
  });
});
