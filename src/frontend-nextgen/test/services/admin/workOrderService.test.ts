import type { WorkOrder } from '@/domain/admin/models';
import { workOrderService } from '@/services/admin/workOrderService';
import { describe, expect, it, jest } from '@jest/globals';

// workOrderService 经 ensureUserId→identityService→testUser→supportProvider transitive 引入
// @tc-chat/adapters（node 环境 ESM），stub 掉仅满足模块解析。
jest.mock('@tc-chat/adapters', () => ({}));

const base = (over: Partial<WorkOrder>): WorkOrder => ({
  itemId: 'x',
  itemType: 'APPROVAL',
  workOrderId: 1,
  notificationId: 0,
  bizType: '',
  bizId: '',
  eventType: '',
  title: '',
  content: '',
  status: 'PENDING',
  statusLabel: '待处理',
  typeLabel: '审批',
  typeTone: 'blue',
  isRead: false,
  canApprove: true,
  gmtModified: '',
  ...over,
});

describe('workOrderService.canApprove', () => {
  it('allows approve when PENDING and canApprove=true', () => {
    expect(workOrderService.canApprove(base({}))).toEqual({ ok: true });
  });
  it('blocks approve when status is not PENDING', () => {
    const r = workOrderService.canApprove(base({ status: 'APPROVED' }));
    expect(r.ok).toBe(false);
    expect(r.reason).toMatch(/已处理/);
  });
  it('blocks approve when canApprove=false', () => {
    const r = workOrderService.canApprove(base({ canApprove: false }));
    expect(r.ok).toBe(false);
    expect(r.reason).toMatch(/权限/);
  });
  it('blocks approve for notification-type work orders that are already APPROVED', () => {
    const r = workOrderService.canApprove(base({ itemType: 'NOTIFICATION', status: 'APPROVED' }));
    expect(r.ok).toBe(false);
  });
});
