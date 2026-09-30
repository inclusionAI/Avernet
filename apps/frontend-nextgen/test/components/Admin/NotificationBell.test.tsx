/** @jest-environment jsdom */
// 通知铃铛深链（split-admin-space-ticket-pages）：「查看全部」与单条点击改指独立通知中心路由
// /ticket-center（单条携带 category 分类参数），不再经 /admin?tab=work-orders 单页。
import type { NotificationSummary } from '@/domain/admin/models';
import { describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen } from '@testing-library/react';

const mockNavigate = jest.fn();
jest.mock('@umijs/max', () => ({ useNavigate: () => mockNavigate }));

let mockRecent: NotificationSummary[] = [];
let mockUnreadCount = 0;
jest.mock('@/hooks/useNotifications', () => ({
  useNotifications: () => ({
    unreadCount: mockUnreadCount,
    recent: mockRecent,
    loadingRecent: false,
    loadRecent: jest.fn(),
    markAllRead: jest.fn(),
    refreshUnread: jest.fn(),
  }),
}));

const { NotificationBell } =
  require('@/components/Admin/NotificationBell') as typeof import('@/components/Admin/NotificationBell');

const approvalItem: NotificationSummary = {
  itemId: 'wo-1',
  notificationId: 9001,
  title: '好友申请待审批',
  content: 'A 申请添加 B',
  gmtModified: '2026-09-22 10:00:00',
  itemType: 'APPROVAL',
  isRead: false,
};

function openBell(view: ReturnType<typeof render>) {
  fireEvent.click(view.getByRole('button', { name: /^通知中心/ }));
}

describe('NotificationBell 深链路由', () => {
  it('「查看全部」导航至独立通知中心路由 /ticket-center', async () => {
    mockRecent = [];
    mockUnreadCount = 0;
    mockNavigate.mockClear();
    const view = render(<NotificationBell />);
    openBell(view);
    fireEvent.click(await screen.findByRole('button', { name: '查看全部' }));
    expect(mockNavigate).toHaveBeenCalledWith('/ticket-center');
  });

  it('点击通知条目携带 category 分类参数跳转', async () => {
    mockRecent = [approvalItem];
    mockUnreadCount = 1;
    mockNavigate.mockClear();
    const view = render(<NotificationBell />);
    openBell(view);
    fireEvent.click(await screen.findByRole('button', { name: /好友申请待审批/ }));
    expect(mockNavigate).toHaveBeenCalledWith('/ticket-center?category=APPROVAL');
  });
});
