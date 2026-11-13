/** @jest-environment jsdom */
import { CollaborationRepairRow } from '@/components/CollaborationPrivacy/BotGeneralConfigDialog/CollaborationRepairRow';
import type { CollaborationRepairReport } from '@/services/bots/botCollaborationRepairService';
import '@testing-library/jest-dom';
import { fireEvent, render, screen } from '@testing-library/react';
import { toast } from 'sonner';

jest.mock('sonner', () => ({ toast: { success: jest.fn(), warning: jest.fn(), error: jest.fn() } }));

type RepairState = Parameters<typeof CollaborationRepairRow>[0]['repair'];

function repairState(overrides: Partial<RepairState> = {}): RepairState {
  return {
    loading: false,
    report: undefined,
    error: undefined,
    refreshError: undefined,
    run: jest.fn(),
    ...overrides,
  };
}

const partialReport: CollaborationRepairReport = {
  bot_id: 'bot-1',
  status: 'partial_failed',
  items: [
    { component: 'ownership', status: 'succeeded', error_code: null },
    { component: 'profile', status: 'failed', error_code: 'sync_failed' },
    { component: 'managers', status: 'unknown', error_code: 'delivery_timeout' },
  ],
};

beforeEach(() => jest.clearAllMocks());

it('渲染修复说明并通过按钮触发 run', () => {
  const repair = repairState();

  render(<CollaborationRepairRow repair={repair} disabled={false} refreshFailed={false} />);

  expect(screen.getByLabelText('修复 Bot 信息')).toBeInTheDocument();
  expect(screen.getByText('修复 Bot 信息')).toBeInTheDocument();
  expect(screen.getByText('修复当前 Bot 在「协作群」中的基本信息（名称、头像、归属、协作者等）')).toBeInTheDocument();
  const button = screen.getByRole('button', { name: '修复信息' });
  expect(button).toBeEnabled();

  fireEvent.click(button);

  expect(repair.run).toHaveBeenCalledTimes(1);
});

it('修复在途时按钮显示「修复中…」并禁用', () => {
  render(<CollaborationRepairRow repair={repairState({ loading: true })} disabled={false} refreshFailed={false} />);

  expect(screen.getByRole('button', { name: '修复中…' })).toBeDisabled();
  expect(screen.queryByRole('button', { name: '修复信息' })).not.toBeInTheDocument();
});

it('disabled 时按钮不可提交', () => {
  const repair = repairState();

  render(<CollaborationRepairRow repair={repair} disabled refreshFailed={false} />);

  const button = screen.getByRole('button', { name: '修复信息' });
  expect(button).toBeDisabled();
  fireEvent.click(button);
  expect(repair.run).not.toHaveBeenCalled();
});

it('partial_failed 报告按固定组件名和分项状态渲染', () => {
  render(<CollaborationRepairRow repair={repairState({ report: partialReport })} disabled={false} refreshFailed={false} />);

  expect(screen.getByText('归属：已修复')).toBeInTheDocument();
  expect(screen.getByText('基本资料：修复失败')).toBeInTheDocument();
  expect(screen.getByText('协作者：结果未确认')).toBeInTheDocument();
  expect(screen.getByText('归属：已修复').closest('ul')).toHaveAttribute('aria-live', 'polite');
});

it('报告列表展示后，标题与按钮所在行仍垂直居中，分项归入文本列', () => {
  render(<CollaborationRepairRow repair={repairState({ report: partialReport })} disabled={false} refreshFailed={false} />);

  const button = screen.getByRole('button', { name: '修复信息' });
  const row = button.closest('.flex');
  expect(row).toHaveClass('items-center', 'justify-between');
  const titleColumn = screen.getByText('修复 Bot 信息').closest('.min-w-0');
  expect(titleColumn).toContainElement(screen.getByText('归属：已修复').closest('ul'));
});

it('全部成功时不渲染分项列表', () => {
  render(
    <CollaborationRepairRow
      repair={repairState({
        report: {
          bot_id: 'bot-1',
          status: 'succeeded',
          items: [{ component: 'ownership', status: 'succeeded', error_code: null }],
        },
      })}
      disabled={false}
      refreshFailed={false}
    />,
  );

  expect(screen.queryByRole('list')).not.toBeInTheDocument();
});

it('错误态使用 role=alert，且不重复 Toast 已由全局提示处理的错误', () => {
  render(
    <CollaborationRepairRow
      repair={repairState({ error: '未能确认修复结果，请稍后重试' })}
      disabled={false}
      refreshFailed={false}
    />,
  );

  expect(screen.getByRole('alert')).toHaveTextContent('未能确认修复结果，请稍后重试');
  expect(toast.error).not.toHaveBeenCalled();
  expect(toast.warning).not.toHaveBeenCalled();
  expect(toast.success).not.toHaveBeenCalled();
});

it('刷新失败独立提示且不改写修复结果', () => {
  render(
    <CollaborationRepairRow
      repair={repairState({
        report: partialReport,
        refreshError: '修复请求已完成，配置刷新失败，请重新打开查看',
      })}
      disabled={false}
      refreshFailed={false}
    />,
  );

  expect(screen.getByRole('status')).toHaveTextContent('修复请求已完成，配置刷新失败，请重新打开查看');
  expect(screen.getByText('协作者：结果未确认')).toBeInTheDocument();
});

it('父级读取失败时对非 failed 报告提示刷新失败', () => {
  render(<CollaborationRepairRow repair={repairState({ report: partialReport })} disabled={false} refreshFailed />);

  expect(screen.getByRole('status')).toHaveTextContent('修复请求已完成，配置刷新失败，请重新打开查看');
});

it('整体 failed 报告不附加刷新失败提示', () => {
  render(
    <CollaborationRepairRow
      repair={repairState({ report: { ...partialReport, status: 'failed' } })}
      disabled={false}
      refreshFailed
    />,
  );

  expect(screen.queryByRole('status')).not.toBeInTheDocument();
});
