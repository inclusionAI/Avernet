/** @jest-environment jsdom */
import WorkOrderCard from '@/components/Admin/WorkOrderCard';
import WorkOrderDetailDrawer from '@/components/Admin/WorkOrderTabs/WorkOrderDetailDrawer';
import { mapWorkOrderDto } from '@/domain/admin/mappers';
import '@testing-library/jest-dom';
import { cleanup, render, screen } from '@testing-library/react';

const noop = () => undefined;

describe.each([
  { view: '待我处理', processed: false },
  { view: '已处理', processed: true },
])('$view 分类标签', ({ processed }) => {
  it.each([
    ['APPROVAL', undefined, '审批类'],
    ['NOTICE', 'APPROVAL', '审批类'],
    ['NOTICE', 'NOTICE', '通知类'],
  ])('%s / %s 的列表和详情都使用中性 %s 标签', (itemType, category, label) => {
    const workOrder = mapWorkOrderDto({
      item_type: itemType,
      notification_category: category,
      work_order_id: itemType === 'APPROVAL' ? 30001 : 0,
      notification_id: itemType === 'NOTICE' ? 40001 : 0,
      event_type: 'SPACE_JOIN_APPLIED',
      title: '分类测试',
      status: processed ? 'APPROVED' : 'PENDING',
      is_read: processed,
      can_approve: !processed && itemType === 'APPROVAL',
    }).item;
    render(<WorkOrderCard workOrder={workOrder} />);
    const rowTag = screen.getByText(label);
    expect(rowTag).toHaveClass('bg-muted', 'text-muted-foreground');
    cleanup();
    render(<WorkOrderDetailDrawer open loading={false} detail={workOrder} onClose={noop} onNextUnread={noop} />);
    expect(screen.getByText(label)).toHaveClass('bg-muted', 'text-muted-foreground');
  });
});
