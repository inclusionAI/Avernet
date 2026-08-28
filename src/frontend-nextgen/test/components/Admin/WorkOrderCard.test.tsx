/** @jest-environment jsdom */

import WorkOrderCard from '@/components/Admin/WorkOrderCard';
import { mapWorkOrderDto } from '@/domain/admin/mappers';
import '@testing-library/jest-dom';
import { fireEvent, render, screen } from '@testing-library/react';

const buildWorkOrder = (overrides: Record<string, unknown> = {}) =>
  mapWorkOrderDto({
    item_id: 'WORK_ORDER_30001',
    item_type: 'APPROVAL',
    work_order_id: 30001,
    notification_id: 40001,
    biz_type: 'SPACE_JOIN',
    biz_id: '10001',
    event_type: 'SPACE_JOIN_APPLIED',
    title: '空间加入申请待审批',
    content: '用户「张三」申请加入空间「风控团队」，请及时处理。',
    status: 'PENDING',
    can_approve: true,
    gmt_modified: '2026-08-12T11:58:00+08:00',
    ...overrides,
  }).item;

describe('WorkOrderCard 详情入口', () => {
  it('待审批的审批类只给出同意 + 驳回，不固定展示查看按钮', () => {
    render(<WorkOrderCard workOrder={buildWorkOrder()} />);

    expect(screen.queryByRole('button', { name: '查看' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '同意' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '驳回' })).toBeInTheDocument();
  });

  it('已驳回的审批类只展示状态文字，不固定展示查看按钮', () => {
    render(
      <WorkOrderCard
        workOrder={buildWorkOrder({ status: 'REJECTED', can_approve: false, review_remark: '暂不开放' })}
      />,
    );

    expect(screen.getByText('已驳回')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '查看' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '同意' })).not.toBeInTheDocument();
  });

  it('已通过的审批类只展示状态文字，不固定展示查看按钮', () => {
    render(<WorkOrderCard workOrder={buildWorkOrder({ status: 'APPROVED', can_approve: false })} />);

    expect(screen.getByText('已通过')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '查看' })).not.toBeInTheDocument();
  });

  it('通知类未查看时显示查看', () => {
    render(
      <WorkOrderCard
        workOrder={buildWorkOrder({
          item_type: 'NOTICE',
          event_type: 'SPACE_JOIN_REVIEWED',
          status: 'REJECTED',
          can_approve: false,
        })}
      />,
    );

    expect(screen.getByRole('button', { name: '查看' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '同意' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '驳回' })).not.toBeInTheDocument();
  });

  it('未查看通知点击查看回传整条工单，供容器按 itemType 选详情端点', () => {
    const onView = jest.fn();
    const wo = buildWorkOrder({ item_type: 'NOTICE', event_type: 'SPACE_JOIN_REVIEWED', can_approve: false });
    render(<WorkOrderCard workOrder={wo} onView={onView} />);

    fireEvent.click(screen.getByRole('button', { name: '查看' }));

    expect(onView).toHaveBeenCalledWith(wo);
  });

  it('通知类已查看后直接展示已查看，不再展示查看按钮', () => {
    render(
      <WorkOrderCard
        workOrder={buildWorkOrder({
          item_type: 'NOTICE',
          event_type: 'SPACE_JOIN_REVIEWED',
          can_approve: false,
          is_read: true,
        })}
      />,
    );

    expect(screen.getByText('已查看')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '查看' })).not.toBeInTheDocument();
  });

  it('canAct=false（缺 identity 只读）时未读通知查看按钮禁用', () => {
    render(
      <WorkOrderCard
        workOrder={buildWorkOrder({ item_type: 'NOTICE', event_type: 'SPACE_JOIN_REVIEWED', can_approve: false })}
        canAct={false}
      />,
    );

    expect(screen.getByRole('button', { name: '查看' })).toBeDisabled();
  });
});
