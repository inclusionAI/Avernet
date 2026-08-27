/** @jest-environment node */
import { notificationService } from '@/services/admin/notificationService';
import * as notificationController from '@/services/backendApi/admin/notificationController';
import { identityService } from '@/services/workspace/identityService';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';

// auto-mock controller，避免真实网络（与 test/services/workspace/* 同款写法）。
jest.mock('@/services/backendApi/admin/notificationController');
// ensureUserId 在 activeIdentityId 未就绪时补拉 identityService.loadIdentities；
// stub @tc-chat/adapters ESM transitive（identityService→testUser→supportProvider）。
jest.mock('@/services/workspace/identityService');
jest.mock('@tc-chat/adapters', () => ({}));

const nc = notificationController as unknown as Record<string, jest.Mock<any>>;

beforeEach(() => {
  jest.resetAllMocks();
  // Service 经 ensureUserId → resolveUserId(activeIdentityId) 注入 user_id query。
  useWorkspaceStore.setState({ activeIdentityId: 'human_327325' });
  // 命中缓存用例不调 loadIdentities；未就绪用例走 ensureUserId 补拉，默认失败降级为 unsupported（不发业务请求）。
  (identityService.loadIdentities as unknown as jest.Mock<any>).mockResolvedValue({
    ok: false,
    error: { code: 'IDENTITY_LOAD_FAILED', friendlyMessage: '', canRetry: true },
  });
});

describe('notificationService.fetchUnreadCount', () => {
  it('走专用 unread-count 端点并读 data.badge_count（待审批+未读通知总数），注入 user_id=327325', async () => {
    nc.fetchUnreadCount.mockResolvedValue({
      success: true,
      code: 'SUCCESS',
      message: 'success',
      data: { unread_count: 3, pending_approval_count: 4, badge_count: 7 },
    });
    const r = await notificationService.fetchUnreadCount();
    expect(nc.fetchUnreadCount).toHaveBeenCalledTimes(1);
    expect(nc.fetchUnreadCount).toHaveBeenCalledWith({ user_id: '327325' });
    expect(r.data).toBe(7);
    expect(r.error).toBeUndefined();
  });

  it('badge_count 缺失时回退 0', async () => {
    nc.fetchUnreadCount.mockResolvedValue({ success: true, code: 'SUCCESS', data: {} });
    const r = await notificationService.fetchUnreadCount();
    expect(r.data).toBe(0);
  });

  it('controller 抛错时返回 error 且不向上抛', async () => {
    nc.fetchUnreadCount.mockRejectedValue(new Error('upstream down'));
    const r = await notificationService.fetchUnreadCount();
    expect(r.error).toBeDefined();
    expect(r.data).toBeUndefined();
  });

  it('activeIdentityId 未就绪时返回 unsupported 且不发请求', async () => {
    useWorkspaceStore.setState({ activeIdentityId: null });
    const r = await notificationService.fetchUnreadCount();
    expect(r.unsupported).toBe(true);
    expect(nc.fetchUnreadCount).not.toHaveBeenCalled();
  });
});

describe('notificationService.fetchRecentNotifications', () => {
  it('传 user_id + page_no，映射为 NotificationSummary（NOTICE→NOTIFICATION）', async () => {
    nc.listNotificationsForBell.mockResolvedValue({
      success: true,
      code: 'SUCCESS',
      data: {
        items: [
          {
            item_id: 'NOTIFICATION_40001',
            item_type: 'NOTICE',
            work_order_id: 30001,
            notification_id: 40001,
            title: '空间通知',
            content: '成员已加入',
            is_read: false,
            gmt_modified: '2026-08-12T11:58:00Z',
            status: 'PENDING',
          },
        ],
        total: 1,
      },
    });
    const r = await notificationService.fetchRecentNotifications(3);
    expect(nc.listNotificationsForBell).toHaveBeenCalledWith({
      user_id: '327325',
      item_type: 'ALL',
      page_no: 1,
      page_size: 3,
    });
    expect(r.data).toHaveLength(1);
    expect(r.data?.[0].title).toBe('空间通知');
    expect(r.data?.[0].itemType).toBe('NOTIFICATION');
  });
});
