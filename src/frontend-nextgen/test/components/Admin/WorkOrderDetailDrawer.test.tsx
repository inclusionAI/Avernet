/** @jest-environment jsdom */

import WorkOrderDetailDrawer from '@/components/Admin/WorkOrderTabs/WorkOrderDetailDrawer';
import { mapWorkOrderDto } from '@/domain/admin/mappers';
import '@testing-library/jest-dom';
import { fireEvent, render, screen } from '@testing-library/react';

const noop = () => undefined;

const buildWorkOrder = (overrides: Record<string, unknown> = {}) =>
  mapWorkOrderDto({
    item_id: 'WORK_ORDER_30001',
    item_type: 'APPROVAL',
    work_order_id: 30001,
    work_order_no: 'WO202608120001',
    notification_id: 40001,
    biz_type: 'SPACE_JOIN',
    biz_id: '10001',
    event_type: 'SPACE_JOIN_APPLIED',
    title: '空间加入申请待审批',
    content: '用户「张三」申请加入空间「风控团队」，请及时处理。',
    status: 'PENDING',
    apply_reason: '申请加入空间参与 Skill 建设',
    reviewer_user_id: 'zhangsan.zs',
    can_approve: true,
    gmt_modified: '2026-08-12T11:58:00+08:00',
    ...overrides,
  }).item;

describe('WorkOrderDetailDrawer 审批类补充字段', () => {
  it('渲染 content + 申请理由 / 审批意见 / 审批人（行内截断看不到的字段）', () => {
    render(
      <WorkOrderDetailDrawer
        open
        loading={false}
        detail={buildWorkOrder({ review_remark: '本期名额已满，下个迭代再申请' })}
        onClose={noop}
        onNextUnread={noop}
      />,
    );

    expect(screen.getByText('用户「张三」申请加入空间「风控团队」，请及时处理。')).toBeInTheDocument();
    expect(screen.getByText('申请加入空间参与 Skill 建设')).toBeInTheDocument();
    expect(screen.getByText('本期名额已满，下个迭代再申请')).toBeInTheDocument();
    expect(screen.getByText('zhangsan.zs')).toBeInTheDocument();
  });

  it('详情(content 为对象)渲染 申请人 / 申请理由 / 合成 content；PENDING 不渲染审批人/审批意见', () => {
    // 真实详情 VO：无 item_type、无顶层 apply_reason/applicant_user_id，content 为结构化对象
    const detail = buildWorkOrder({
      item_type: undefined,
      apply_reason: undefined,
      content: {
        space_id: 6,
        space_name: '测试空间2',
        applicant_user_id: '146836',
        applicant_name: '146836',
        reason: 'test',
      },
      reviewer_user_id: null,
      review_remark: null,
      reviewed_at: null,
      can_approve: false,
    });
    render(<WorkOrderDetailDrawer open loading={false} detail={detail} onClose={noop} onNextUnread={noop} />);

    // body content 为对象 → 抽屉改 JSON 原样展示，不再渲染合成文案
    expect(document.body.textContent).toContain(
      JSON.stringify(
        {
          space_id: 6,
          space_name: '测试空间2',
          applicant_user_id: '146836',
          applicant_name: '146836',
          reason: 'test',
        },
        null,
        2,
      ),
    );
    expect(screen.getByText('申请人')).toBeInTheDocument();
    expect(screen.getByText('146836')).toBeInTheDocument();
    expect(screen.getByText('test')).toBeInTheDocument();
    // PENDING：审批人/审批意见/审批时间整行不渲染
    expect(screen.queryByText('审批人')).not.toBeInTheDocument();
    expect(screen.queryByText('审批意见')).not.toBeInTheDocument();
  });

  it('新设计不再展示工单编号（精简为申请人 / 申请理由 / 审批人 / 审批意见 / 审批时间）', () => {
    render(<WorkOrderDetailDrawer open loading={false} detail={buildWorkOrder()} onClose={noop} onNextUnread={noop} />);

    expect(screen.queryByText('WO202608120001')).not.toBeInTheDocument();
  });

  it('空值字段整行不渲染', () => {
    render(
      <WorkOrderDetailDrawer
        open
        loading={false}
        detail={buildWorkOrder({ apply_reason: null })}
        onClose={noop}
        onNextUnread={noop}
      />,
    );

    expect(screen.queryByText('申请理由')).not.toBeInTheDocument();
    expect(screen.queryByText('审批意见')).not.toBeInTheDocument();
  });

  it('通知类不渲染审批补充字段，渲染未读提示文案', () => {
    render(
      <WorkOrderDetailDrawer
        open
        loading={false}
        detail={buildWorkOrder({
          item_type: 'NOTICE',
          event_type: 'SPACE_JOIN_REVIEWED',
          title: '空间加入申请未通过',
          content: '你加入空间「风控团队」的申请未通过。驳回原因：本期名额已满',
        })}
        onClose={noop}
        onNextUnread={noop}
      />,
    );

    expect(screen.getByText('你加入空间「风控团队」的申请未通过。驳回原因：本期名额已满')).toBeInTheDocument();
    expect(screen.queryByText('申请理由')).not.toBeInTheDocument();
    expect(screen.getByText('此通知尚未阅读，标记已读后将移动到「已处理」')).toBeInTheDocument();
  });

  it('空 content 显示占位', () => {
    render(
      <WorkOrderDetailDrawer
        open
        loading={false}
        detail={buildWorkOrder({ content: null })}
        onClose={noop}
        onNextUnread={noop}
      />,
    );

    expect(screen.getByText('（暂无内容）')).toBeInTheDocument();
  });
});

describe('WorkOrderDetailDrawer 已读与未读导航', () => {
  it('已读通知头部展示「已查看」Tag 并提示已读', () => {
    render(
      <WorkOrderDetailDrawer
        open
        loading={false}
        detail={buildWorkOrder({ item_type: 'NOTICE', event_type: 'SPACE_JOIN_REVIEWED', is_read: true })}
        onClose={noop}
        onNextUnread={noop}
      />,
    );

    expect(screen.getByText('已查看')).toBeInTheDocument();
    expect(screen.getByText('此通知已标记为已读')).toBeInTheDocument();
  });

  it('「下一条未读」仅在通知类且有 nextUnread 时出现；审批类即使有 nextUnread 也不出现', () => {
    const notif = buildWorkOrder({ item_type: 'NOTICE', event_type: 'SPACE_JOIN_REVIEWED' });
    const anotherNotif = buildWorkOrder({
      item_type: 'NOTICE',
      event_type: 'SPACE_MEMBER_ADDED',
      work_order_id: 30002,
    });

    const { rerender } = render(
      <WorkOrderDetailDrawer
        open
        loading={false}
        detail={notif}
        nextUnread={anotherNotif}
        onClose={noop}
        onNextUnread={noop}
      />,
    );
    expect(screen.getByRole('button', { name: '下一条未读' })).toBeInTheDocument();

    // 审批类即使给了 nextUnread，也不出现「下一条未读」
    rerender(
      <WorkOrderDetailDrawer
        open
        loading={false}
        detail={buildWorkOrder()}
        nextUnread={anotherNotif}
        onClose={noop}
        onNextUnread={noop}
      />,
    );
    expect(screen.queryByRole('button', { name: '下一条未读' })).not.toBeInTheDocument();
  });

  it('open=false 时抽屉不渲染详情内容', () => {
    render(<WorkOrderDetailDrawer open={false} loading={false} detail={null} onClose={noop} onNextUnread={noop} />);

    expect(screen.queryByText('空间加入申请待审批')).not.toBeInTheDocument();
  });
});

describe('WorkOrderDetailDrawer JSON 负载块（JsonBlock）', () => {
  it('长 JSON 默认折叠但不丢内容：头部渲染标题/复制，点「展开」后可「收起」', () => {
    // > 12 行的 content 对象，触发折叠
    const bigContent = Object.fromEntries(Array.from({ length: 20 }, (_, i) => [`field_${i}`, `value_${i}`]));
    const detail = buildWorkOrder({ content: bigContent });
    render(<WorkOrderDetailDrawer open loading={false} detail={detail} onClose={noop} onNextUnread={noop} />);

    expect(screen.getByText('JSON')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '复制 JSON' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '展开' })).toBeInTheDocument();

    // 折叠只裁视觉，不裁 DOM：原文（含换行缩进）仍完整在文档中
    expect(document.body.textContent).toContain(JSON.stringify(bigContent, null, 2));

    fireEvent.click(screen.getByRole('button', { name: '展开' }));
    expect(screen.getByRole('button', { name: '收起' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '展开' })).not.toBeInTheDocument();
  });

  it('行数少的 JSON 原样展示，不出现展开/收起', () => {
    const detail = buildWorkOrder({
      content: { space_id: 6, space_name: '测试空间2', reason: 'test' },
    });
    render(<WorkOrderDetailDrawer open loading={false} detail={detail} onClose={noop} onNextUnread={noop} />);

    expect(document.body.textContent).toContain(JSON.stringify({ space_id: 6, space_name: '测试空间2', reason: 'test' }, null, 2));
    expect(screen.queryByRole('button', { name: '展开' })).not.toBeInTheDocument();
  });
});
