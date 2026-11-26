/** @jest-environment jsdom */
import { Button } from '@/components/ui';
import { TaskEscortPanel } from '@/components/Workspace/TaskEscortPanel';
import type { TaskComposerContext } from '@/services/tasks/taskMapper';
import { fetchWorkflows, type WorkflowListItem } from '@/services/tasks/taskService';
import '@testing-library/jest-dom';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useLayoutEffect, useState } from 'react';

jest.mock('@/services/tasks/taskService', () => ({ fetchWorkflows: jest.fn() }));
// 即使某个真实 ID 恰好与演示 ID 相同，也不允许加载演示状态/指标/定义。
jest.mock('@/components/Workspace/TaskEscortDemo/model', () => { throw new Error('Live drawer imported demo model'); });
jest.mock('@/components/Workspace/TaskEscortDemo/workflowCatalog', () => { throw new Error('Live drawer imported demo catalog'); });
jest.mock('@/components/Workspace/TaskEscortDemo/useTaskEscortDemo', () => { throw new Error('Live drawer imported demo hook'); });

const mockFetchWorkflows = jest.mocked(fetchWorkflows);
const context: TaskComposerContext = {
  sourceType: 'bot', ownerUserId: 'viewer-101', ownerBotId: 'bot-a:asset-owner-202',
  mainSessionId: 'session-a', mainSessionName: '协作会话', parentTaskId: null,
};
const item = (workflowId: string, title: string): WorkflowListItem => ({ workflowId, title, packId: null, updatedAt: 1 });
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

function Host({
  taskContext = context,
  onCommit,
}: {
  taskContext?: TaskComposerContext | null;
  onCommit?: (text: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [messages, setMessages] = useState(0);
  useLayoutEffect(() => {
    onCommit?.(document.querySelector('[role="dialog"]')?.textContent ?? '');
  });
  return (
    <>
      <Button data-task-escort-trigger aria-controls="task-escort-demo-panel" aria-expanded={open} onClick={() => setOpen(!open)}>
        任务护航
      </Button>
      <Button onClick={() => setMessages((count) => count + 1)}>聊天背景 {messages}</Button>
      <TaskEscortPanel open={open} onOpenChange={setOpen} context={taskContext} botName="协作 Bot" />
    </>
  );
}
async function openPanel(taskContext: TaskComposerContext | null = context) {
  const user = userEvent.setup();
  const rendered = render(<Host taskContext={taskContext} />);
  await user.click(screen.getByRole('button', { name: '任务护航' }));
  return { user, ...rendered };
}

beforeAll(() => {
  Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', { configurable: true, value: jest.fn() });
  Object.defineProperty(HTMLElement.prototype, 'hasPointerCapture', { configurable: true, value: () => false });
  Object.defineProperty(HTMLElement.prototype, 'setPointerCapture', { configurable: true, value: jest.fn() });
  Object.defineProperty(HTMLElement.prototype, 'releasePointerCapture', { configurable: true, value: jest.fn() });
});
beforeEach(() => {
  mockFetchWorkflows.mockReset().mockResolvedValue([]);
});

it('loads lazily using the exact composer owner/Bot and displays only real titles and IDs', async () => {
  const user = userEvent.setup();
  mockFetchWorkflows.mockResolvedValue([
    item('onemodel-risk-defense', '真实后端攻防流程'), item('live-release', '真实后端发布审批'),
  ]);
  // 编辑 URL 不能替换共享 composer 提供的请求身份。
  window.history.replaceState(null, '', '/workspace/chat?userId=forged&ownerId=forged&botId=forged');
  render(<Host />);
  expect(mockFetchWorkflows).not.toHaveBeenCalled();
  await user.click(screen.getByRole('button', { name: '任务护航' }));
  expect(mockFetchWorkflows).toHaveBeenCalledWith(context.ownerUserId, context.ownerBotId);
  const selector = screen.getByRole('combobox', { name: '切换工作流' });
  await waitFor(() => expect(selector).toHaveTextContent('真实后端攻防流程'));
  expect(selector).toHaveTextContent('onemodel-risk-defense');
  expect(screen.getByText('运行数据暂未接入')).toBeInTheDocument();
  expect(screen.getByText(/运行记录、指标与工作流定义尚未连接/)).toBeInTheDocument();
  expect(screen.queryByText(/DEMO|模拟权限|虚构|OneModel 攻防工作流/)).not.toBeInTheDocument();
  expect(screen.queryByRole('table')).not.toBeInTheDocument();
  expect(screen.queryByRole('img')).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: /编辑器|保存|运行|部署|YAML/ })).not.toBeInTheDocument();
  await user.click(selector);
  expect(screen.getByRole('listbox', { name: '当前用户可执行的工作流' })).toBeInTheDocument();
  expect(screen.getAllByRole('option')).toHaveLength(2);
  expect(screen.getByRole('option', { name: /真实后端发布审批/ })).toHaveTextContent('live-release');
  await user.click(screen.getByRole('option', { name: /真实后端发布审批/ }));
  expect(selector).toHaveTextContent('真实后端发布审批');
  expect(selector).toHaveTextContent('live-release');
  expect(selector).not.toHaveTextContent('真实后端攻防流程');
  expect(screen.getByText('运行数据暂未接入')).toBeInTheDocument();
  expect(mockFetchWorkflows).toHaveBeenCalledTimes(1);
});

it('has explicit loading and empty states without a fixture fallback', async () => {
  const request = deferred<WorkflowListItem[]>();
  mockFetchWorkflows.mockReturnValue(request.promise);
  await openPanel();
  expect(screen.getByRole('status')).toHaveTextContent('正在加载工作流');
  expect(screen.getByRole('combobox', { name: '切换工作流' })).toBeDisabled();
  await act(async () => request.resolve([]));
  expect(screen.getByText('暂无可执行的工作流')).toBeInTheDocument();
  expect(screen.queryByText('运行数据暂未接入')).not.toBeInTheDocument();
  expect(screen.queryByText(/OneModel|演示|DEMO/)).not.toBeInTheDocument();
});

it('shows request failure separately from empty and retries the same context', async () => {
  mockFetchWorkflows.mockRejectedValueOnce(new Error('当前请求失败')).mockResolvedValueOnce([item('retry-live', '重试后的工作流')]);
  const { user } = await openPanel();
  expect(await screen.findByRole('alert')).toHaveTextContent('工作流加载失败');
  expect(screen.queryByText('暂无可执行的工作流')).not.toBeInTheDocument();
  expect(screen.queryByText(/OneModel|演示|DEMO/)).not.toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: '重试加载工作流' }));
  await waitFor(() => expect(screen.getByRole('combobox', { name: '切换工作流' })).toHaveTextContent('重试后的工作流'));
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  expect(mockFetchWorkflows).toHaveBeenNthCalledWith(2, context.ownerUserId, context.ownerBotId);
});

it('never requests or substitutes fixtures when composer context is missing', async () => {
  await openPanel(null);
  expect(screen.getByText('缺少工作流上下文')).toBeInTheDocument();
  expect(screen.getByRole('combobox', { name: '切换工作流' })).toBeDisabled();
  expect(mockFetchWorkflows).not.toHaveBeenCalled();
  expect(screen.queryByText(/OneModel|演示|DEMO/)).not.toBeInTheDocument();
});

it('clears the old selection on reopen and refreshes changed backend permissions before displaying anything', async () => {
  const refresh = deferred<WorkflowListItem[]>();
  mockFetchWorkflows.mockResolvedValueOnce([item('first', '原第一项'), item('revoked', '已撤销执行权限')])
    .mockReturnValueOnce(refresh.promise).mockResolvedValueOnce([]);
  const { user } = await openPanel();
  const selector = screen.getByRole('combobox', { name: '切换工作流' });
  await waitFor(() => expect(selector).toHaveTextContent('原第一项'));
  await user.click(selector);
  await user.click(screen.getByRole('option', { name: /已撤销执行权限/ }));
  await user.click(screen.getByRole('button', { name: '关闭任务护航' }));
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  expect(mockFetchWorkflows).toHaveBeenCalledTimes(1);
  await user.click(screen.getByRole('button', { name: '任务护航' }));
  expect(mockFetchWorkflows).toHaveBeenCalledTimes(2);
  expect(screen.getByRole('combobox', { name: '切换工作流' })).not.toHaveTextContent(/原第一项|已撤销执行权限/);
  expect(screen.getByRole('status')).toHaveTextContent('正在加载工作流');
  await act(async () => refresh.resolve([item('permitted', '新获执行权限')]));
  expect(screen.getByRole('combobox', { name: '切换工作流' })).toHaveTextContent('新获执行权限');
  await user.click(screen.getByRole('combobox', { name: '切换工作流' }));
  expect(screen.getAllByRole('option')).toHaveLength(1);
  expect(screen.queryByRole('option', { name: /已撤销执行权限/ })).not.toBeInTheDocument();
  await user.keyboard('{Escape}');
  await user.click(screen.getByRole('button', { name: '关闭任务护航' }));
  await user.click(screen.getByRole('button', { name: '任务护航' }));
  expect(await screen.findByText('暂无可执行的工作流')).toBeInTheDocument();
  expect(screen.getByRole('combobox', { name: '切换工作流' })).not.toHaveTextContent('新获执行权限');
});

it.each([
  { ...context, ownerUserId: 'viewer-303' },
  { ...context, ownerBotId: 'bot-b:asset-owner-404' },
])('hides old names in the very first commit after an owner/Bot/null switch: %j', async (nextContext) => {
  const next = deferred<WorkflowListItem[]>();
  mockFetchWorkflows.mockResolvedValueOnce([item('old', '旧上下文绝不可见')]).mockReturnValueOnce(next.promise);
  const { user, rerender } = await openPanel();
  await waitFor(() => expect(screen.getByRole('combobox', { name: '切换工作流' })).toHaveTextContent('旧上下文绝不可见'));
  await user.click(screen.getByRole('combobox', { name: '切换工作流' }));
  const commits: string[] = [];
  rerender(<Host taskContext={nextContext} onCommit={(text) => commits.push(text)} />);
  expect(commits.length).toBeGreaterThan(0);
  expect(commits.every((text) => !text.includes('旧上下文绝不可见'))).toBe(true);
  expect(screen.queryByRole('option', { name: /旧上下文绝不可见/ })).not.toBeInTheDocument();
  await waitFor(() => expect(mockFetchWorkflows).toHaveBeenLastCalledWith(nextContext.ownerUserId, nextContext.ownerBotId));
  await act(async () => next.resolve([item('next', '新上下文流程')]));
  expect(screen.getByRole('combobox', { name: '切换工作流' })).toHaveTextContent('新上下文流程');
});

it('clears a loaded list in the first commit after losing composer context', async () => {
  mockFetchWorkflows.mockResolvedValue([item('old', '旧上下文绝不可见')]);
  const { user, rerender } = await openPanel();
  await waitFor(() => expect(screen.getByRole('combobox', { name: '切换工作流' })).toHaveTextContent('旧上下文绝不可见'));
  await user.click(screen.getByRole('combobox', { name: '切换工作流' }));
  const commits: string[] = [];
  rerender(<Host taskContext={null} onCommit={(text) => commits.push(text)} />);
  expect(commits.length).toBeGreaterThan(0);
  expect(commits.every((text) => !text.includes('旧上下文绝不可见'))).toBe(true);
  expect(screen.queryByRole('option', { name: /旧上下文绝不可见/ })).not.toBeInTheDocument();
  expect(mockFetchWorkflows).toHaveBeenCalledTimes(1);
  expect(screen.getByText('缺少工作流上下文')).toBeInTheDocument();
});

it('ignores a late old owner response after switching Bot', async () => {
  const old = deferred<WorkflowListItem[]>();
  const current = deferred<WorkflowListItem[]>();
  mockFetchWorkflows.mockReturnValueOnce(old.promise).mockReturnValueOnce(current.promise);
  const { rerender } = await openPanel();
  rerender(<Host taskContext={{ ...context, ownerBotId: 'bot-b:owner-b' }} />);
  await act(async () => current.resolve([item('new', '当前 Bot 实时流程')]));
  await act(async () => old.resolve([item('stale', '迟到的旧流程')]));
  expect(screen.getByRole('combobox', { name: '切换工作流' })).toHaveTextContent('当前 Bot 实时流程');
  expect(screen.queryByText('迟到的旧流程')).not.toBeInTheDocument();
});

it('does not reuse an in-flight request across close/reopen of the same context', async () => {
  const old = deferred<WorkflowListItem[]>();
  const current = deferred<WorkflowListItem[]>();
  mockFetchWorkflows.mockReturnValueOnce(old.promise).mockReturnValueOnce(current.promise);
  const { user } = await openPanel();
  await user.click(screen.getByRole('button', { name: '关闭任务护航' }));
  await user.click(screen.getByRole('button', { name: '任务护航' }));
  expect(mockFetchWorkflows).toHaveBeenCalledTimes(2);
  await act(async () => old.resolve([item('stale', '关闭前旧请求')]));
  expect(screen.getByRole('combobox', { name: '切换工作流' })).not.toHaveTextContent('关闭前旧请求');
  expect(screen.getByRole('status')).toBeInTheDocument();
  await act(async () => current.resolve([item('fresh', '重开后的实时流程')]));
  expect(screen.getByRole('combobox', { name: '切换工作流' })).toHaveTextContent('重开后的实时流程');
});

it('keeps a late failure from an earlier open out of a successful reopened drawer', async () => {
  const old = deferred<WorkflowListItem[]>();
  mockFetchWorkflows.mockReturnValueOnce(old.promise).mockResolvedValueOnce([item('fresh', '新请求成功')]);
  const { user } = await openPanel();
  await user.click(screen.getByRole('button', { name: '关闭任务护航' }));
  await user.click(screen.getByRole('button', { name: '任务护航' }));
  await waitFor(() => expect(screen.getByRole('combobox', { name: '切换工作流' })).toHaveTextContent('新请求成功'));
  await act(async () => old.reject(new Error('旧请求失败')));
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  expect(screen.getByRole('combobox', { name: '切换工作流' })).toHaveTextContent('新请求成功');
});

it('preserves drawer focus/expand/Escape behavior and leaves chat interactive', async () => {
  mockFetchWorkflows.mockResolvedValue([item('a', '键盘工作流 A'), item('b', '键盘工作流 B')]);
  const { user } = await openPanel();
  const dialog = screen.getByRole('dialog', { name: '任务护航' });
  expect(dialog).not.toHaveAttribute('aria-modal', 'true');
  expect(dialog).toHaveClass('task-escort-demo-panel', 'workspace-overlay-panel');
  expect(screen.getByRole('button', { name: '关闭任务护航' })).toHaveFocus();
  await user.click(screen.getByRole('button', { name: '展开抽屉' }));
  expect(dialog).toHaveAttribute('data-expanded', 'true');
  await user.click(screen.getByRole('button', { name: '还原抽屉' }));
  expect(dialog).toHaveAttribute('data-expanded', 'false');
  await user.click(screen.getByRole('button', { name: '聊天背景 0' }));
  expect(screen.getByRole('button', { name: '聊天背景 1' })).toBeInTheDocument();
  expect(dialog).toBeInTheDocument();
  const selector = screen.getByRole('combobox', { name: '切换工作流' });
  await user.click(selector);
  await user.keyboard('{Home}{ArrowDown}{Enter}');
  expect(selector).toHaveTextContent('键盘工作流 B');
  await user.click(selector);
  await user.keyboard('{Escape}');
  expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
  expect(dialog).toBeInTheDocument();
  expect(selector).toHaveFocus();
  fireEvent.keyDown(screen.getByRole('button', { name: '关闭任务护航' }), { key: 'Escape' });
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  expect(screen.getByRole('button', { name: '任务护航' })).toHaveFocus();
  await user.click(screen.getByRole('button', { name: '任务护航' }));
  await user.click(screen.getByRole('button', { name: '关闭任务护航' }));
  await waitFor(() => expect(screen.getByRole('button', { name: '任务护航' })).toHaveFocus());
});
