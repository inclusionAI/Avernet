import type { WorkOrderTypeMeta } from '@/domain/admin/workOrderMeta';
import { resolveWorkOrderType, WORK_ORDER_STATUS_LABEL, WORK_ORDER_TYPE_META } from '@/domain/admin/workOrderMeta';
import { describe, expect, it } from '@jest/globals';

/**
 * 消息文案矩阵回归基线。
 * - category 事实源：后端契约《Skill 空间体系改造梳理1.0》§4.2 消息事件枚举（14 个事件，4 APPROVAL / 10 NOTICE）
 *   https://yuque.antfin.com/securitytec/otbct4/fn72p6q8tpsog5nk#xPAX3
 * - label / tone 事实源：docs/architecture/admin-visual-interaction-guide.md §2.2
 * title 由后端透传；content 在列表/通知为字符串透传，在详情为结构化对象（mapWorkOrderDto 解析并合成展示文案），均不在文案矩阵断言。
 */
const MATRIX_BASELINE: Record<string, WorkOrderTypeMeta> = {
  // 审批类（APPROVAL）：PRD 四色
  SPACE_JOIN_APPLIED: { label: '申请加入团队', tone: 'orange', category: 'APPROVAL' },
  BOT_COLLABORATOR_APPLIED: { label: '申请管理权限', tone: 'blue', category: 'APPROVAL' },
  HUMAN2BOT_FRIEND_APPLIED: { label: '好友申请', tone: 'green', category: 'APPROVAL' },
  BOT2BOT_FRIEND_APPLIED: { label: '好友申请', tone: 'green', category: 'APPROVAL' },
  // 通知类（NOTICE）：审批通知=绿 / 普通通知=蓝（noticeTypeMeta 二分）
  SPACE_JOIN_REVIEWED: { label: '审批通知', tone: 'green', category: 'NOTICE' },
  SPACE_MEMBER_ADDED: { label: '通知', tone: 'blue', category: 'NOTICE' },
  BOT_COLLABORATOR_REVIEWED: { label: '审批通知', tone: 'green', category: 'NOTICE' },
  BOT_MEMBER_ADDED: { label: '通知', tone: 'blue', category: 'NOTICE' },
  HUMAN2BOT_FRIEND_REVIEWED: { label: '审批通知', tone: 'green', category: 'NOTICE' },
  BOT2BOT_FRIEND_REVIEWED: { label: '审批通知', tone: 'green', category: 'NOTICE' },
  // 通知类（NOTICE）：公开工单紫
  HUMAN2BOT_PUBLIC_ORDER_CREATED: { label: '公开工单', tone: 'purple', category: 'NOTICE' },
  HUMAN2BOT_PUBLIC_ORDER_COMPLETED: { label: '公开工单', tone: 'purple', category: 'NOTICE' },
  BOT2BOT_PUBLIC_ORDER_CREATED: { label: '公开工单', tone: 'purple', category: 'NOTICE' },
  BOT2BOT_PUBLIC_ORDER_COMPLETED: { label: '公开工单', tone: 'purple', category: 'NOTICE' },
};

describe('消息文案矩阵基线（§4.2 全量事件）', () => {
  it('覆盖且仅覆盖契约里的 14 个 event_type：后端增删事件必须显式改基线', () => {
    expect(Object.keys(WORK_ORDER_TYPE_META).sort()).toEqual(Object.keys(MATRIX_BASELINE).sort());
  });

  it.each(Object.entries(MATRIX_BASELINE))('%s 的 label/tone/category 与基线一致', (eventType, expected) => {
    expect(WORK_ORDER_TYPE_META[eventType]).toEqual(expected);
  });

  it('分类配比与契约一致：4 个 APPROVAL / 10 个 NOTICE', () => {
    const categories = Object.values(WORK_ORDER_TYPE_META).map((m) => m.category);
    expect(categories.filter((c) => c === 'APPROVAL')).toHaveLength(4);
    expect(categories.filter((c) => c === 'NOTICE')).toHaveLength(10);
  });

  it('resolveWorkOrderType 对全量事件都返回基线 meta（不落 fallback）', () => {
    for (const [eventType, expected] of Object.entries(MATRIX_BASELINE)) {
      expect(resolveWorkOrderType(eventType)).toEqual(expected);
      // 传入相反的 fallback 也不该改写命中的映射
      expect(resolveWorkOrderType(eventType, expected.category === 'APPROVAL' ? 'NOTICE' : 'APPROVAL')).toEqual(
        expected,
      );
    }
  });
});

describe('WORK_ORDER_TYPE_META', () => {
  it('审批类按 PRD 四色：申请加入团队=橙 / 申请管理权限=蓝 / 好友=绿', () => {
    expect(WORK_ORDER_TYPE_META.SPACE_JOIN_APPLIED).toEqual({
      label: '申请加入团队',
      tone: 'orange',
      category: 'APPROVAL',
    });
    expect(WORK_ORDER_TYPE_META.BOT_COLLABORATOR_APPLIED.tone).toBe('blue');
    expect(WORK_ORDER_TYPE_META.BOT_COLLABORATOR_APPLIED.category).toBe('APPROVAL');
    expect(WORK_ORDER_TYPE_META.HUMAN2BOT_FRIEND_APPLIED.tone).toBe('green');
    expect(WORK_ORDER_TYPE_META.BOT2BOT_FRIEND_APPLIED.tone).toBe('green');
  });

  it('通知类兜底：审批结果通知=绿 / 普通通知=蓝；公开工单=紫', () => {
    expect(WORK_ORDER_TYPE_META.SPACE_JOIN_REVIEWED.category).toBe('NOTICE');
    expect(WORK_ORDER_TYPE_META.SPACE_JOIN_REVIEWED.tone).toBe('green');
    expect(WORK_ORDER_TYPE_META.HUMAN2BOT_PUBLIC_ORDER_CREATED.tone).toBe('purple');
    expect(WORK_ORDER_TYPE_META.BOT2BOT_PUBLIC_ORDER_COMPLETED.category).toBe('NOTICE');
  });
});

describe('resolveWorkOrderType', () => {
  it('已知 event_type 返回对应 meta', () => {
    expect(resolveWorkOrderType('SPACE_JOIN_APPLIED').tone).toBe('orange');
  });

  it('未知 event_type 但 fallbackCategory=APPROVAL 返回审批类默认（蓝）', () => {
    const m = resolveWorkOrderType('UNKNOWN_EVENT', 'APPROVAL');
    expect(m.category).toBe('APPROVAL');
    expect(m.tone).toBe('blue');
  });

  it('未知 event_type 无 fallback 返回通知默认（蓝）', () => {
    expect(resolveWorkOrderType(undefined)).toEqual({ label: '通知', tone: 'blue', category: 'NOTICE' });
  });
});

describe('WORK_ORDER_STATUS_LABEL', () => {
  it('状态中文 label', () => {
    expect(WORK_ORDER_STATUS_LABEL.PENDING).toBe('待审批');
    expect(WORK_ORDER_STATUS_LABEL.APPROVED).toBe('已通过');
    expect(WORK_ORDER_STATUS_LABEL.REJECTED).toBe('已驳回');
    expect(WORK_ORDER_STATUS_LABEL.UNKNOWN).toBe('未知');
  });
});
