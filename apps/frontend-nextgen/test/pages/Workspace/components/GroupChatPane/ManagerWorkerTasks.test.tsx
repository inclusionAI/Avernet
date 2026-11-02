/** @jest-environment jsdom */
import { GroupChatCollabPanel } from '@/pages/Workspace/components/GroupChatPane/GroupChatCollabPanel';
import type { DeliveryStatusView, SessionView } from '@/domain/collaboration';
import { ManagerWorkerTasks } from '@/pages/Workspace/components/GroupChatPane/ManagerWorkerTasks';
import { cancelQueuedDelivery, queryTaskDeliveries, queryWorkerTasks, cancelWorkerTask } from '@/services/workspace/messageDeliveryService';
import { useMessageDeliveryStore } from '@/stores/messageDeliveryStore';
import '@testing-library/jest-dom';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';

jest.mock('@/services/workspace/messageDeliveryService', () => ({
  cancelQueuedDelivery: jest.fn(),
  queryTaskDeliveries: jest.fn(),
  queryWorkerTasks: jest.fn(),
  cancelWorkerTask: jest.fn(),
}));
const query = jest.mocked(queryTaskDeliveries);
const cancel = jest.mocked(cancelQueuedDelivery);
const session = { sessionId: 's1', participants: [] } as unknown as SessionView;
const row = (ref: string, status: DeliveryStatusView['status']): DeliveryStatusView => ({
  delivery_id: `d-${ref}`,
  message_id: `m-${ref}`,
  target_bot_id: 'worker',
  flow_kind: 'task',
  kind: 'send',
  status,
  state_version: 1,
  run_id: `run-${ref}`,
  wait_reason: null,
  admission_error: null,
  task: {
    task_ref: ref,
    summary: `日志分析 ${ref}`,
    manager_id: 'manager',
    worker_id: 'worker',
    worker_name: '日志 Bot',
    queued_at: Date.now() - 10000,
    started_at: Date.now() - 5000,
    last_activity_at: Date.now() - 2000,
    finished_at: status === 'completed' ? Date.now() : null,
    timed_out: false,
  },
});
const insert = (...rows: DeliveryStatusView[]) =>
  act(() => {
    rows.forEach((r) => useMessageDeliveryStore.getState().upsertDelivery('s1', r));
  });
const open = () => fireEvent.click(screen.getByRole('button', { name: /查看任务/ }));
beforeEach(() => {
  useMessageDeliveryStore.getState().clearAll();
  query.mockResolvedValue([]);
  jest.mocked(queryWorkerTasks).mockResolvedValue([]);
  cancel.mockReset();
});

it('shows task counts, separate cards for the same worker, and clears terminal unread on opening', async () => {
  insert(row('T-1', 'running'), row('T-2', 'queued'), row('T-3', 'completed'));
  render(<ManagerWorkerTasks session={session} messages={[]} />);
  expect(screen.getByRole('button', { name: /1 个任务执行中 · 1 个排队中/ })).toBeInTheDocument();
  expect(screen.getByText('1 个任务有新结果')).toBeInTheDocument();
  open();
  const panel = screen.getByRole('dialog');
  expect(within(panel).getAllByRole('article')).toHaveLength(3);
  expect(within(panel).getAllByText('日志 Bot')).toHaveLength(3);
  await waitFor(() => expect(screen.queryByText('1 个任务有新结果')).not.toBeInTheDocument());
  expect(within(panel).getByRole('button', { name: '停止' })).toBeInTheDocument();
  expect(within(panel).getByRole('button', { name: '取消' })).toBeInTheDocument();
  fireEvent.click(within(panel).getByRole('button', { name: '关闭任务面板' }));
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
});

it('cancels the selected delivery only and waits for server confirmation', async () => {
  const running = row('T-1', 'running');
  insert(running, row('T-2', 'running'));
  let resolve!: (result: Awaited<ReturnType<typeof cancelQueuedDelivery>>) => void;
  cancel.mockImplementation(
    () =>
      new Promise((done) => {
        resolve = done;
      }),
  );
  render(<ManagerWorkerTasks session={session} messages={[]} />);
  open();
  fireEvent.click(screen.getAllByRole('button', { name: '停止' })[0]);
  expect(cancel).toHaveBeenCalledTimes(1);
  const selectedId = cancel.mock.calls[0][1];
  expect(['d-T-1', 'd-T-2']).toContain(selectedId);
  expect(screen.getByText('正在确认…')).toBeDisabled();
  const selected = selectedId === running.delivery_id ? running : row('T-2', 'running');
  await act(async () =>
    resolve({
      results: [{ delivery: { ...selected, status: 'cancel_unknown', state_version: 2 }, error: null }],
      firstError: null,
    }),
  );
  expect(screen.queryByText('已取消')).not.toBeInTheDocument();
  expect(screen.getAllByText('状态待确认').length).toBeGreaterThan(0);
  expect(screen.getAllByRole('button', { name: '停止' })).toHaveLength(1);
  insert({ ...selected, status: 'cancelled', state_version: 3 });
  expect(screen.getByText('已取消')).toBeInTheDocument();
});

it('recovers delivery state from source messages and shows an empty task panel without legacy guessing', async () => {
  query.mockResolvedValue([row('T-1', 'queued')]);
  render(<ManagerWorkerTasks session={session} messages={[{ id: 'source', role: 'assistant' } as never]} />);
  await waitFor(() => expect(query).toHaveBeenCalledWith('s1', ['source']));
  await waitFor(() => expect(screen.getByRole('button', { name: /1 个排队中/ })).toBeInTheDocument());
});

it('refreshes durations each second from original server timestamps', () => {
  jest.useFakeTimers();
  try {
    insert(row('T-1', 'running'));
    const view = render(<ManagerWorkerTasks session={session} messages={[]} />);
    open();
    expect(screen.getByText('运行 00:05')).toBeInTheDocument();
    act(() => {
      jest.advanceTimersByTime(2000);
    });
    expect(screen.getByText('运行 00:07')).toBeInTheDocument();
    view.unmount();
  } finally {
    jest.useRealTimers();
  }
});

it('bounds recent terminal history without evicting active or uncertain tasks', () => {
  insert(row('T-active', 'running'), row('T-unknown', 'cancel_unknown'));
  for (let index = 0; index < 110; index++) insert(row(`T-done-${index}`, 'completed'));
  const rows = Object.values(useMessageDeliveryStore.getState().deliveriesBySessionId.s1);
  expect(rows.filter((r) => r.status === 'completed')).toHaveLength(100);
  expect(rows.some((r) => r.status === 'running')).toBe(true);
  expect(rows.some((r) => r.status === 'cancel_unknown')).toBe(true);
});

it('updates recent activity when another streaming segment arrives without restarting task duration', () => {
  jest.useFakeTimers();
  try {
    const running = row('T-1', 'running'); running.run_id = 'run-live'; insert(running);
    const message = { id: 'segment', role: 'assistant', status: 'streaming', content: '正在分析', createdAt: Date.now() - 2000, extra: { runId: 'run-live' } } as const;
    const view = render(<ManagerWorkerTasks session={session} messages={[message as never]} />); open();
    act(() => { jest.advanceTimersByTime(3000); });
    view.rerender(<ManagerWorkerTasks session={session} messages={[{ ...message, content: '正在分析，发现三类错误' } as never]} />);
    fireEvent.click(screen.getByRole('button', { name: /展开任务：/ }));
    expect(screen.getByText('最近活动：0 秒前')).toBeInTheDocument();
    expect(screen.getByText('运行 00:08')).toBeInTheDocument();
    view.unmount();
  } finally { jest.useRealTimers(); }
});

jest.mock('@/pages/Workspace/components/GroupChatPane/CollabPanel', () => ({
  CollabPanel: ({ panel, activeRuns }: any) => panel.visible ? activeRuns ?? null : null,
}));
it('gates the new task bar by group kind and keeps it visible outside Bot control panels', () => {
  const props = { panel: { visible: false } as never, session, messages: [], abortingBotIds: new Set<string>(), abortBot: async () => undefined };
  const view = render(<GroupChatCollabPanel {...props} group={{ kind: 'task_master_slave' } as never} />);
  expect(screen.getByRole('button', { name: /暂无任务，查看任务/ })).toBeInTheDocument();
  expect(screen.getAllByRole('button')).toHaveLength(1);
  view.rerender(<GroupChatCollabPanel {...props} panel={{ visible: true } as never}
    session={{ ...session, participants: [{ actorId: 'worker', kind: 'bot', name: '日志 Bot', role: 'member', mode: 'auto' }] }}
    messages={[{ id: 'stream', role: 'assistant', status: 'streaming', content: '分析中', extra: { botUuid: 'worker', runId: 'run' } } as never]}
    group={{ kind: 'free_chat' } as never} />);
  expect(screen.getByText('1 个 Bot 处理中 · 0 项待处理')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '查看处理列表' })).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: /查看任务/ })).not.toBeInTheDocument();
});

it('separates historical acknowledgements from task counts and shows their source', () => {
  const messages = ['62a50241', '89951a2a', '04485427'].map((ref, index) => ({
    id: `source-${index}`, role: 'assistant', status: 'history', content: '', createdAt: 1700000000000 + index,
    blocks: [{ type: 'tool_execution', steps: [{ id: `tool-${index}`, tool: 'bcs_assign_task', title: '派发', status: 'success',
      input: JSON.stringify({ target_bot: 'ray', message: `任务 ${index}` }),
      output: JSON.stringify({ __bcs_coordination__: true, v: 2, tool: 'bcs_assign_task', status: 'stored', intent_id: `bcs_intent_${ref}${'0'.repeat(24)}` }) }] }],
  } as never));
  messages.splice(2, 0, { id: 'current-request', role: 'user', status: 'history', content: '问一下 worker', createdAt: 1700000000001 } as never);
  render(<ManagerWorkerTasks session={session} messages={messages} />);
  expect(screen.getByRole('button', { name: /任务状态尚未同步 · 当前 1 条派发回执/ })).toBeInTheDocument();
  expect(screen.queryByText('3 个任务状态待确认')).not.toBeInTheDocument();
  open();
  expect(within(screen.getByRole('region', { name: '本次派发回执' })).getAllByRole('article')).toHaveLength(1);
  const history = screen.getByRole('region', { name: '历史派发回执' });
  expect(within(history).queryByRole('article')).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '查看来源消息' })).not.toBeInTheDocument();
  const currentToggle = screen.getByRole('button', { name: /展开任务：/ });
  fireEvent.click(currentToggle);
  expect(screen.getByRole('button', { name: '查看来源消息' })).toBeInTheDocument();
  expect(screen.getByText('任务引用')).toBeInTheDocument();
  fireEvent.click(currentToggle);
  expect(screen.queryByRole('button', { name: '查看来源消息' })).not.toBeInTheDocument();
  fireEvent.click(within(history).getByRole('button', { name: /历史派发/ }));
  expect(within(history).getAllByRole('article')).toHaveLength(2);
  expect(within(history).queryByText('任务引用')).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '停止' })).not.toBeInTheDocument();
});


it('queries full assignment intent and replaces the receipt with one confirmed task', async () => {
  const intentId = `bcs_intent_${'04485427'.repeat(4)}`;
  jest.mocked(queryWorkerTasks).mockResolvedValue([{ ref: intentId, status: 'completed' }]);
  const messages = [{ id: 'request', role: 'user', content: '派发任务' }, {
    id: 'manager', role: 'assistant', content: '', createdAt: Date.now(),
    blocks: [{ type: 'tool_execution', steps: [{ id: 'tool', tool: 'bcs_assign_task', status: 'success',
      input: JSON.stringify({ target_bot: 'ray', message: '检查日志' }),
      output: JSON.stringify({ __bcs_coordination__: true, v: 2, tool: 'bcs_assign_task', status: 'stored', intent_id: intentId }),
    }] }],
  }] as never;
  render(<ManagerWorkerTasks session={session} messages={messages} />);
  await waitFor(() => expect(queryWorkerTasks).toHaveBeenCalledWith([intentId]));
  await waitFor(() => expect(screen.getByRole('button', { name: /1 个任务已完成/ })).toBeInTheDocument());
  open();
  expect(screen.getAllByRole('article')).toHaveLength(1);
  expect(screen.queryByText('本次派发回执')).not.toBeInTheDocument();
  expect(screen.getByText('已完成')).toBeInTheDocument();
});


it('stops a task resolved from a receipt with its complete intent instead of a Bot abort', async () => {
  const intentId = `bcs_intent_${'12345678'.repeat(4)}`;
  jest.mocked(queryWorkerTasks).mockResolvedValue([{ ref: intentId, status: 'running', running_s: 2 }]);
  jest.mocked(cancelWorkerTask).mockResolvedValue({ ref: intentId, status: 'cancelled' });
  const messages = [{ id: 'request', role: 'user', content: '派发' }, {
    id: 'manager', role: 'assistant', content: '', createdAt: Date.now(),
    blocks: [{ type: 'tool_execution', steps: [{ id: 'tool', tool: 'bcs_assign_task', status: 'success',
      input: JSON.stringify({ target_bot: 'ray', message: '检查日志' }),
      output: JSON.stringify({ __bcs_coordination__: true, v: 2, tool: 'bcs_assign_task', status: 'stored', intent_id: intentId }),
    }] }],
  }] as never;
  render(<ManagerWorkerTasks session={session} messages={messages} />);
  await waitFor(() => expect(screen.getByRole('button', { name: /1 个任务执行中/ })).toBeInTheDocument());
  open();
  fireEvent.click(screen.getByRole('button', { name: '停止' }));
  await waitFor(() => expect(cancelWorkerTask).toHaveBeenCalledWith(intentId));
  await waitFor(() => expect(screen.getByText('已取消')).toBeInTheDocument());
  expect(cancel).not.toHaveBeenCalled();
});
