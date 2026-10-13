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

// 折叠态侧栏工具簇经 size/className 透传收 28px + muted 色阶（与导航项做主次区分），
// 未读红点挂在IconButton 外层 span 上做绝对定位，不随规格联动。规格断言锁 AppSidebar 折叠态的区分信号。
describe('NotificationBell 规格透传', () => {
  it('size="sm" + muted：按钮收 28px 且色阶降级，未读红点不受影响', () => {
    mockRecent = [];
    mockUnreadCount = 1;
    const view = render(<NotificationBell size="sm" className="text-muted-foreground" />);
    expect(view.getByRole('button', { name: '通知中心' })).toHaveClass('h-7', 'w-7', 'text-muted-foreground');
    expect(view.getByLabelText('1 条未读')).toBeInTheDocument();
  });

  it('未传 size 维持默认 32px（md），既有调用点视觉不变', () => {
    mockUnreadCount = 0;
    const view = render(<NotificationBell />);
    expect(view.getByRole('button', { name: '通知中心' })).toHaveClass('h-8', 'w-8');
  });
});
