/** @jest-environment jsdom */
import type { TaskEscortWorkspaceState } from '@/hooks/useTaskEscortWorkspace';
import { LiveReadView } from '@/pages/TaskEscort/LiveReadView';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';

function controller(): TaskEscortWorkspaceState {
  return {
    page: 'runs',
    query: '',
    workflowId: 'all',
    runFilter: 'all',
    workflows: [],
    runs: [
      {
        id: 'real-r1',
        workflowId: 'real-w1',
        name: '真实工作流',
        status: 'unknown',
        rawStatus: 'new-state',
        duration: null,
        startedAt: null,
        trigger: '—',
        bot: '—',
        node: '—',
      },
    ],
    selectedRun: null,
    setSelectedRun: jest.fn(),
    setQuery: jest.fn(),
    setWorkflowId: jest.fn(),
    setRunFilter: jest.fn(),
    loadRunDetail: jest.fn().mockResolvedValue({
      run: { id: 'real-r1', name: '真实工作流', status: 'unknown', duration: null, startedAt: null },
      nodes: [],
    }),
  } as unknown as TaskEscortWorkspaceState;
}
it('真实列表明确样本范围、未知值，不展示合成指标', () => {
  render(<LiveReadView demo={controller()} />);
  expect(screen.getByText(/最多 100 条/)).toBeTruthy();
  expect(screen.getByText('真实工作流')).toBeTruthy();
  expect(screen.getByText('未知状态')).toBeTruthy();
  expect(screen.queryByText('98.6%')).toBeNull();
});
it('打开详情仅显示实际节点，没有节点时不构造成功轨迹', async () => {
  const demo = controller();
  demo.selectedRun = 'real-r1';
  render(<LiveReadView demo={demo} />);
  await waitFor(() => expect(demo.loadRunDetail).toHaveBeenCalledWith('real-r1'));
  expect(await screen.findByText('暂无节点执行记录')).toBeTruthy();
  expect(screen.queryByText('拉取任务')).toBeNull();
});
it('切换记录时旧响应不覆盖新详情，失败可以重试', async () => {
  const demo = controller();
  let resolveOld: ((value: any) => void) | undefined;
  const load = jest
    .fn()
    .mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveOld = resolve;
        }),
    )
    .mockRejectedValueOnce(new Error('denied'))
    .mockResolvedValueOnce({
      run: { id: 'r2', name: '新记录', status: 'success', duration: 1, startedAt: null },
      nodes: [],
    });
  demo.loadRunDetail = load;
  demo.selectedRun = 'real-r1';
  const view = render(<LiveReadView demo={demo} />);
  view.rerender(<LiveReadView demo={{ ...demo, selectedRun: 'r2' }} />);
  expect(await screen.findByText('运行详情读取失败')).toBeTruthy();
  await act(async () => resolveOld?.({ run: { id: 'real-r1', name: '不应显示的旧记录' }, nodes: [] }));
  expect(screen.queryByText('不应显示的旧记录')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: '重试详情' }));
  expect(await screen.findByText('新记录')).toBeTruthy();
});

it('关闭真实详情后恢复键盘焦点至原始按钮', async () => {
  const demo = controller();
  const { rerender } = render(<LiveReadView demo={demo} />);
  const opener = screen.getByRole('button', { name: '查看运行 real-r1' });
  opener.focus();
  fireEvent.click(opener);
  rerender(<LiveReadView demo={{ ...demo, selectedRun: 'real-r1' }} />);
  await screen.findByText('暂无节点执行记录');
  fireEvent.keyDown(document, { key: 'Escape' });
  rerender(<LiveReadView demo={{ ...demo, selectedRun: null }} />);
  await waitFor(() => expect(document.activeElement).toBe(opener));
});

it('原始详情按钮消失后焦点回到仍连接的列表区域', async () => {
  const demo = controller();
  const { rerender } = render(<LiveReadView demo={demo} />);
  const opener = screen.getByRole('button', { name: '查看运行 real-r1' });
  opener.focus();
  rerender(<LiveReadView demo={{ ...demo, selectedRun: 'real-r1' }} />);
  await screen.findByText('暂无节点执行记录');
  rerender(<LiveReadView demo={{ ...demo, selectedRun: null, query: 'no-match' }} />);
  await waitFor(() => expect(document.activeElement?.getAttribute('data-detail-focus-fallback')).not.toBeNull());
});
