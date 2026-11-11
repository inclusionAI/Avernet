/** @jest-environment jsdom */
import { WorkOrderTabs } from '@/components/Admin/WorkOrderTabs';
import type { WorkOrder } from '@/domain/admin/models';
import '@testing-library/jest-dom';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

const mockNavigate = jest.fn();
const mockMarkOneRead = jest.fn().mockResolvedValue(true);
const mockRefreshUnread = jest.fn().mockResolvedValue(undefined);
let mockWorkOrder: WorkOrder;
let mockEmpty = false;
let mockCategory = 'APPROVAL';
let mockError: { message: string } | null = null;
const mockSetView = jest.fn();
const mockSetCategory = jest.fn();
const mockSetPageNo = jest.fn();
const mockFetchList = jest.fn().mockResolvedValue(undefined);
const mockMarkAllRead = jest.fn().mockResolvedValue(undefined);
beforeEach(() => {
  mockEmpty = false;
  mockCategory = 'APPROVAL';
  mockError = null;
  jest.clearAllMocks();
});

jest.mock('@umijs/max', () => ({ useNavigate: () => mockNavigate }));

jest.mock('@/hooks/useNotifications', () => ({
  useNotifications: () => ({
    unreadCount: 1,
    markAllRead: mockMarkAllRead,
    markOneRead: mockMarkOneRead,
    refreshUnread: mockRefreshUnread,
  }),
}));

jest.mock('@/hooks/useWorkOrders', () => ({
  useWorkOrders: () => ({
    view: 'pending_mine',
    category: mockCategory,
    pageNo: 1,
    pageSize: 10,
    items: mockEmpty ? [] : [mockWorkOrder],
    total: 1,
    loading: false,
    error: mockError,
    setView: mockSetView,
    setCategory: mockSetCategory,
    setPageNo: mockSetPageNo,
    setPageSize: jest.fn(),
    fetchList: mockFetchList,
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
    expect(mockNavigate).toHaveBeenCalledWith('/lab/community?topicId=topic-1&postId=post-1');
  });
});

describe('通知中心设计交互', () => {
  it('纵向视图切换并重置页码', () => {
    render(<WorkOrderTabs />);
    expect(screen.getByRole('navigation', { name: '通知视图' })).toBeInTheDocument();
    expect(screen.queryByRole('tab', { name: '全部' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '待我处理' })).toHaveAttribute('aria-current', 'page');
    fireEvent.click(screen.getByRole('button', { name: /^已处理$/ }));
    expect(mockSetView).toHaveBeenCalledWith('processed');
    expect(mockSetPageNo).toHaveBeenCalledWith(1);
  });

  it('分类切换并重置页码', () => {
    render(<WorkOrderTabs />);
    fireEvent.click(screen.getByRole('tab', { name: '通知类' }));
    expect(mockSetCategory).toHaveBeenCalledWith('NOTIFICATION');
    expect(mockSetPageNo).toHaveBeenCalledWith(1);
  });

  it('全部已读完成后刷新当前列表', async () => {
    render(<WorkOrderTabs />);
    fireEvent.click(screen.getByRole('button', { name: '标记为全部已读' }));
    await waitFor(() => expect(mockFetchList).toHaveBeenCalledTimes(1));
    expect(mockMarkAllRead).toHaveBeenCalledTimes(1);
  });

  it('空态保留插图，不再提供返回全部的筛选入口', () => {
    mockEmpty = true;
    mockCategory = 'APPROVAL';
    render(<WorkOrderTabs />);
    expect(screen.getByText('暂无通知')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '清除筛选' })).not.toBeInTheDocument();
    expect(screen.queryByRole('tab', { name: '全部' })).not.toBeInTheDocument();
  });

  it('请求失败仍提供重试入口', () => {
    mockError = { message: '网络连接失败' };
    render(<WorkOrderTabs />);
    fireEvent.click(screen.getByRole('button', { name: '重试' }));
    expect(mockFetchList).toHaveBeenCalledTimes(1);
  });
});
