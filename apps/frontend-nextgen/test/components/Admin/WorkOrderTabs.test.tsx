/** @jest-environment jsdom */
import { WorkOrderTabs } from '@/components/Admin/WorkOrderTabs';
import type { WorkOrder } from '@/domain/admin/models';
import '@testing-library/jest-dom';
import { fireEvent, render, screen } from '@testing-library/react';

const mockNavigate = jest.fn();
const mockMarkOneRead = jest.fn().mockResolvedValue(true);
const mockRefreshUnread = jest.fn().mockResolvedValue(undefined);
let mockWorkOrder: WorkOrder;

jest.mock('@umijs/max', () => ({ useNavigate: () => mockNavigate }));

jest.mock('@/hooks/useNotifications', () => ({
  useNotifications: () => ({
    unreadCount: 1,
    markAllRead: jest.fn(),
    markOneRead: mockMarkOneRead,
    refreshUnread: mockRefreshUnread,
  }),
}));

jest.mock('@/hooks/useWorkOrders', () => ({
  useWorkOrders: () => ({
    view: 'pending_mine',
    category: 'ALL',
    pageNo: 1,
    pageSize: 10,
    items: [mockWorkOrder],
    total: 1,
    loading: false,
    error: null as string | null,
    setView: jest.fn(),
    setCategory: jest.fn(),
    setPageNo: jest.fn(),
    setPageSize: jest.fn(),
    fetchList: jest.fn(),
    approve: jest.fn(),
    reject: jest.fn(),
    getDetail: jest.fn(),
    getNotificationDetail: jest.fn(),
  }),
}));

jest.mock('@/components/Admin/WorkOrderTabs/WorkOrderDetailDrawer', () => ({
  WorkOrderDetailDrawer: () => null,
}));

jest.mock('@/components/Admin/WorkOrderCard', () => ({
  WorkOrderCard: ({
    workOrder,
    onView,
  }: {
    workOrder: WorkOrder;
    onView: (item: WorkOrder) => void | Promise<void>;
  }) => (
    <button type="button" onClick={() => void onView(workOrder)}>
      {workOrder.title}
    </button>
  ),
}));

const workOrder = {
  itemId: 'NOTIFICATION_9002',
  itemType: 'NOTIFICATION',
  workOrderNo: '',
  workOrderId: 0,
  notificationId: 9002,
  bizType: 'BBS',
  bizId: 'topic-1',
  eventType: 'BBS_REPLIED',
  title: '张三回复了你的帖子',
  content: '张三回复了你的帖子',
  status: 'UNKNOWN',
  statusLabel: '未知',
  typeLabel: '通知',
  typeTone: 'blue' as const,
  isRead: false,
  canApprove: false,
  gmtModified: '2026-11-03T10:00:00+08:00',
  bbsTarget: { topicId: 'topic-1', postId: 'post-1' },
} as unknown as WorkOrder;

mockWorkOrder = workOrder;

describe('WorkOrderTabs', () => {
  it('opens a BBS notice directly at the exact topic and marks it read', async () => {
    mockNavigate.mockClear();
    mockMarkOneRead.mockClear();
    render(<WorkOrderTabs />);
    fireEvent.click(screen.getByRole('button', { name: '张三回复了你的帖子' }));
    await screen.findByRole('button', { name: '张三回复了你的帖子' });
    expect(mockMarkOneRead).toHaveBeenCalledWith(9002);
    expect(mockNavigate).toHaveBeenCalledWith(
      '/lab/community?topicId=topic-1&postId=post-1',
    );
  });
});
