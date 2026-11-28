// 工单类型→颜色/标签/分类 单一真相源（admin 视觉交互指南 §2.2）。
// 事件元数据供 mapper 使用；通知中心分类展示由 workOrderCategoryLabel 统一，不按事件着色。
// 审批类按 PRD：申请加入团队=橙 / 申请管理权限=蓝 / 好友申请=绿；
// 通知类统一二分（noticeTypeMeta）：审批类=绿 / 通知=蓝；公开工单=紫（兜底）。
// 全量事件与后端契约 §4.2 一一对应，基线锁在 test/domain/admin/workOrderMeta.test.ts。

import type { WorkOrder, WorkOrderStatus } from './models';

export type WorkOrderTone = 'blue' | 'green' | 'orange' | 'purple';
export type WorkOrderCategoryCode = 'APPROVAL' | 'NOTICE';

export interface WorkOrderTypeMeta {
  label: string;
  tone: WorkOrderTone;
  category: WorkOrderCategoryCode;
}

const DEFAULT_META: WorkOrderTypeMeta = { label: '通知', tone: 'blue', category: 'NOTICE' };

/** event_type → {label, tone, category}。审批类取 PRD 文案；通知类条目仅作 event 兜底，
 *  列表 Tag 统一走 noticeTypeMeta 二分（审批类/通知）。 */
export const WORK_ORDER_TYPE_META: Record<string, WorkOrderTypeMeta> = {
  // 审批类（APPROVAL）
  SPACE_JOIN_APPLIED: { label: '申请加入团队', tone: 'orange', category: 'APPROVAL' },
  BOT_COLLABORATOR_APPLIED: { label: '申请管理权限', tone: 'blue', category: 'APPROVAL' },
  SKILL_COLLABORATOR_APPLIED: { label: '申请编辑权限', tone: 'blue', category: 'APPROVAL' },
  HUMAN2BOT_FRIEND_APPLIED: { label: '好友申请', tone: 'green', category: 'APPROVAL' },
  BOT2BOT_FRIEND_APPLIED: { label: '好友申请', tone: 'green', category: 'APPROVAL' },
  // 通知类（NOTICE）—— 列表 Tag 统一走 noticeTypeMeta，此处仅兜底
  SPACE_JOIN_REVIEWED: { label: '审批类', tone: 'green', category: 'NOTICE' },
  SPACE_MEMBER_ADDED: { label: '通知', tone: 'blue', category: 'NOTICE' },
  BOT_COLLABORATOR_REVIEWED: { label: '审批类', tone: 'green', category: 'NOTICE' },
  BOT_MEMBER_ADDED: { label: '通知', tone: 'blue', category: 'NOTICE' },
  HUMAN2BOT_FRIEND_REVIEWED: { label: '审批类', tone: 'green', category: 'NOTICE' },
  BOT2BOT_FRIEND_REVIEWED: { label: '审批类', tone: 'green', category: 'NOTICE' },
  // bot-friend-roster：Owner 移除好友的系统通知（非审批结果,走普通通知蓝兜底）。
  HUMAN2BOT_FRIEND_REMOVED: { label: '通知', tone: 'blue', category: 'NOTICE' },
  BOT2BOT_FRIEND_REMOVED: { label: '通知', tone: 'blue', category: 'NOTICE' },
  HUMAN2BOT_PUBLIC_ORDER_CREATED: { label: '公开工单', tone: 'purple', category: 'NOTICE' },
  HUMAN2BOT_PUBLIC_ORDER_COMPLETED: { label: '公开工单', tone: 'purple', category: 'NOTICE' },
  BOT2BOT_PUBLIC_ORDER_CREATED: { label: '公开工单', tone: 'purple', category: 'NOTICE' },
  BOT2BOT_PUBLIC_ORDER_COMPLETED: { label: '公开工单', tone: 'purple', category: 'NOTICE' },
  // bot-public-risk-check：Bot 公开风险前置检测结果通知（失败/需关注），携带检测工单外链。
  BOT_PUBLIC_RISK: { label: 'Bot公开风险检测', tone: 'purple', category: 'NOTICE' },
  TODO_HUMAN_ACTION_REQUIRED: { label: '待办人工处理', tone: 'blue', category: 'NOTICE' },
  TODO_BOT_INFORMATION_REQUIRED: { label: '待办补充信息', tone: 'blue', category: 'NOTICE' },
  TODO_EXECUTION_INTERRUPTED: { label: '待办执行中断', tone: 'orange', category: 'NOTICE' },
  TODO_VERIFICATION_REQUIRED: { label: '待办验收', tone: 'blue', category: 'NOTICE' },
  TODO_COMPLETED: { label: '待办完成', tone: 'green', category: 'NOTICE' },
};

/** 工单状态 → 中文 label（admin 视觉交互指南 §2.3）。 */
export const WORK_ORDER_STATUS_LABEL: Record<WorkOrderStatus, string> = {
  PENDING: '待审批',
  APPROVED: '已通过',
  REJECTED: '已驳回',
  UNKNOWN: '未知',
};

/** 通知类 Tag 二分（对齐 PRD notifTypeLabel）：审批类=绿 / 通知=蓝。 */
export function noticeTypeMeta(category?: string | null): WorkOrderTypeMeta {
  return category && category.toUpperCase() === 'APPROVAL'
    ? { label: '审批类', tone: 'green', category: 'NOTICE' }
    : { label: '通知', tone: 'blue', category: 'NOTICE' };
}

/** 按 event_type 解析类型元数据；未知时按 fallback 分类退回默认。 */
export function resolveWorkOrderType(
  eventType: string | undefined,
  fallbackCategory?: WorkOrderCategoryCode,
): WorkOrderTypeMeta {
  if (eventType && WORK_ORDER_TYPE_META[eventType]) return WORK_ORDER_TYPE_META[eventType];
  if (fallbackCategory === 'APPROVAL') {
    return { label: '审批类', tone: 'blue', category: 'APPROVAL' };
  }
  return DEFAULT_META;
}

/** 展示分类独立于处理状态；审批结果仍是通知，不因此获得审批权限。 */
export function workOrderCategoryLabel(
  workOrder: Pick<WorkOrder, 'itemType' | 'notificationCategory'>,
): '审批类' | '通知类' {
  return workOrder.itemType === 'APPROVAL' || workOrder.notificationCategory?.toUpperCase() === 'APPROVAL'
    ? '审批类'
    : '通知类';
}
