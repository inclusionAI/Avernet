/** @jest-environment jsdom */
import { useManagerWorkerTasks } from '@/pages/Workspace/hooks/useManagerWorkerTasks';
import { GroupTaskRow } from '@/pages/Workspace/components/GroupChatPane/GroupTaskRow';
import { GroupChatCollabPanel } from '@/pages/Workspace/components/GroupChatPane/GroupChatCollabPanel';
import type { DeliveryStatusView, SessionView } from '@/domain/collaboration';
import { ManagerWorkerTasks } from '@/pages/Workspace/components/GroupChatPane/ManagerWorkerTasks';
import { cancelQueuedDelivery, queryTaskDeliveries, queryWorkerTasks, cancelWorkerTask } from '@/services/workspace/messageDeliveryService';
import { useMessageDeliveryStore } from '@/stores/messageDeliveryStore';
import '@testing-library/jest-dom';
import { act, fireEvent, render, renderHook, screen, waitFor, within } from '@testing-library/react';

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
  jest.mocked(cancelWorkerTask).mockReset();
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
  expect(within(panel).queryByRole('button', { name: '停止' })).not.toBeInTheDocument();
  expect(within(panel).queryByRole('button', { name: '取消' })).not.toBeInTheDocument();
  fireEvent.click(within(panel).getByRole('button', { name: '关闭任务面板' }));
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
});

it.each(['network error', 'unknown response'])('recovers managed cancellation after %s without a version change', async (failure) => {
  jest.useFakeTimers();
  try {
    const intentId = `bcs_intent_${'a'.repeat(32)}`;
    const delivery = row('T-aaaaaaaa', 'running');
    delivery.task!.intent_id = intentId;
    insert(delivery);
    const messages = [{ id: 'manager', role: 'assistant', content: '', createdAt: Date.now(),
      blocks: [{ type: 'tool_execution', steps: [{ id: 'tool', tool: 'bcs_assign_task', status: 'success',
        input: JSON.stringify({ target_bot: 'worker', message: '检查日志' }),
        output: JSON.stringify({ __bcs_coordination__: true, v: 2, tool: 'bcs_assign_task', status: 'stored', intent_id: intentId }),
      }] }],
    }] as never;
    let recover!: (rows: DeliveryStatusView[]) => void;
    query.mockImplementationOnce(() => new Promise((resolve) => { recover = resolve; }));
    query.mockResolvedValue([delivery]);
    if (failure === 'network error') jest.mocked(cancelWorkerTask).mockRejectedValueOnce(new Error('temporary failure'));
    else jest.mocked(cancelWorkerTask).mockResolvedValueOnce({ ref: intentId, status: 'unknown' });
    const view = renderHook(() => useManagerWorkerTasks('s1', messages, []));
    await act(async () => { await Promise.resolve(); });
    await act(async () => { await view.result.current.cancelTask(view.result.current.tasks[0]); });
    expect(cancelWorkerTask).toHaveBeenCalledWith(intentId);
    expect(cancel).not.toHaveBeenCalled();
    expect(view.result.current.tasks[0].status).toBe('unknown');
    // This read started before cancellation and cannot confirm its outcome.
    await act(async () => { recover([delivery]); });
    expect(view.result.current.tasks[0].status).toBe('unknown');
    await act(async () => { jest.advanceTimersByTime(10_000); });
    expect(view.result.current.tasks[0].status).toBe('running');
    expect(view.result.current.tasks[0].version).toBe(1);
    jest.mocked(cancelWorkerTask).mockResolvedValueOnce({ ref: intentId, status: 'cancelled' });
    await act(async () => { await view.result.current.cancelTask(view.result.current.tasks[0]); });
    expect(view.result.current.tasks[0].status).toBe('cancelled');
    view.unmount();
  } finally { jest.useRealTimers(); }
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
  insert(row('T-active', 'running'), row('T-unknown', 'cancel_unknown'), row('T-expired', 'expired'));
  for (let index = 0; index < 110; index++) insert(row(`T-done-${index}`, 'completed'));
  const rows = Object.values(useMessageDeliveryStore.getState().deliveriesBySessionId.s1);
  expect(rows.filter((r) => r.status === 'completed')).toHaveLength(100);
  expect(rows.some((r) => r.status === 'running')).toBe(true);
  expect(rows.some((r) => r.status === 'cancel_unknown')).toBe(true);
  expect(rows.some((r) => r.status === 'expired')).toBe(true);
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

it('keeps the Manager reply stop control beside tasks even when the control panel is hidden', async () => {
  const abortBot = jest.fn().mockResolvedValue(undefined);
  const participants = [
    { actorId: 'manager', kind: 'bot', name: 'Manager', role: 'manager', mode: 'auto' },
    { actorId: 'worker', kind: 'bot', name: 'Worker', role: 'member', mode: 'auto' },
  ];
  const messages = ['manager', 'worker'].map((botUuid) => ({
    id: botUuid, role: 'assistant', status: 'streaming', content: '处理中', extra: { botUuid, runId: botUuid },
  }));
  render(<GroupChatCollabPanel panel={{ visible: false } as never} session={{ ...session, participants } as never}
    group={{ kind: 'task_master_slave' } as never} messages={messages as never}
    abortingBotIds={new Set()} abortBot={abortBot} />);
  expect(screen.getByRole('button', { name: /暂无任务，查看任务/ })).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '查看处理列表' })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '停止 Manager 的当前回复' }));
  await waitFor(() => expect(abortBot).toHaveBeenCalledWith('manager'));
  expect(screen.queryByRole('button', { name: '停止 Worker 的当前回复' })).not.toBeInTheDocument();
});


it('isolates cancellation, pending state and unread results for colliding display refs', async () => {
  const ids = ['1', '2'].map((suffix) => `bcs_intent_12345678${suffix.repeat(24)}`);
  insert(...ids.map((intent_id, index) => ({ ...row('T-12345678', 'running'), delivery_id: `collision-${index}`,
    task: { ...row('T-12345678', 'running').task!, intent_id, summary: `Assignment ${index}` } })));
  const messages: never[] = [];
  const participants: never[] = [];
  const view = renderHook(() => useManagerWorkerTasks('s1', messages, participants));
  await act(async () => { await Promise.resolve(); });
  expect(view.result.current.tasks).toHaveLength(2);
  expect(queryWorkerTasks).toHaveBeenCalledWith(expect.arrayContaining(ids));
  let finish!: (value: { ref: string; status: 'cancelled' }) => void;
  jest.mocked(cancelWorkerTask).mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
  let first!: Promise<void>;
  act(() => { first = view.result.current.cancelTask(view.result.current.tasks.find((t) => t.intentId === ids[0])!); });
  expect(view.result.current.pending.has(ids[0])).toBe(true);
  expect(view.result.current.pending.has(ids[1])).toBe(false);
  expect(view.result.current.tasks.find((t) => t.intentId === ids[1])!.status).toBe('running');
  jest.mocked(cancelWorkerTask).mockResolvedValueOnce({ ref: ids[1], status: 'cancelled' });
  await act(async () => { await view.result.current.cancelTask(view.result.current.tasks.find((t) => t.intentId === ids[1])!); });
  expect(cancelWorkerTask).toHaveBeenNthCalledWith(1, ids[0]);
  expect(cancelWorkerTask).toHaveBeenNthCalledWith(2, ids[1]);
  act(() => { view.result.current.markRead(); });
  expect(view.result.current.unread).toHaveLength(0);
  await act(async () => { finish({ ref: ids[0], status: 'cancelled' }); await first; });
  expect(view.result.current.unread.map((t) => t.intentId)).toEqual([ids[0]]);
  view.unmount();
});

it('retains ordinary Manager queue controls and processing activity in task sessions', async () => {
  const onCancelDelivery = jest.fn().mockResolvedValue(undefined);
  const queued = { ...row('manager-queue', 'queued'), flow_kind: 'group', task: undefined, target_bot_id: 'manager', content_preview: 'Next request' } as DeliveryStatusView;
  const processing = { ...queued, delivery_id: 'processing', status: 'running' } as DeliveryStatusView;
  const props = {
    panel: { visible: false } as never,
    session: { ...session, participants: [{ actorId: 'manager', kind: 'bot', name: 'Manager', role: 'manager' }] } as never,
    group: { kind: 'task_master_slave' } as never, messages: [], abortingBotIds: new Set<string>(), abortBot: jest.fn(),
    queuedDeliveries: [queued, { ...queued, delivery_id: 'worker-queue', target_bot_id: 'worker' }, { ...queued, delivery_id: 'task-queue', flow_kind: 'task' } as DeliveryStatusView],
    processingDeliveries: [processing], onCancelDelivery,
  };
  const view = render(<GroupChatCollabPanel {...props} />);
  expect(screen.getByText('Manager 处理中')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '待处理 1 条' }));
  const button = screen.getByRole('button', { name: /取消发往 Manager 的排队消息/ });
  fireEvent.click(button);
  await waitFor(() => expect(onCancelDelivery).toHaveBeenCalledWith(queued.message_id, queued.delivery_id));
  view.rerender(<GroupChatCollabPanel {...props} cancellingDeliveryIds={new Set([queued.delivery_id])} />);
  expect(button).toBeDisabled();
});

it.each(['late status', 'already_finished', 'network failure'])('preserves a completion event through %s', async (outcome) => {
  const intentId = `bcs_intent_${'c'.repeat(32)}`;
  const running = row('T-cccccccc', 'running');
  running.task!.intent_id = intentId;
  insert(running);
  let resolveStatus!: (value: { ref: string; status: 'running' }[]) => void;
  jest.mocked(queryWorkerTasks).mockImplementationOnce(() => new Promise((resolve) => { resolveStatus = resolve; }));
  const messages: never[] = [];
  const participants: never[] = [];
  const view = renderHook(() => useManagerWorkerTasks('s1', messages, participants));
  await act(async () => { await Promise.resolve(); });
  let cancelRequest: Promise<void> | undefined;
  let settle!: () => void;
  if (outcome !== 'late status') {
    jest.mocked(cancelWorkerTask).mockImplementationOnce(() => new Promise((resolve, reject) => {
      settle = () => outcome === 'network failure' ? reject(new Error('offline')) : resolve({ ref: intentId, status: 'already_finished' });
    }));
    act(() => { cancelRequest = view.result.current.cancelTask(view.result.current.tasks[0]); });
  }
  const completed = { ...running, status: 'completed', state_version: 2,
    task: { ...running.task!, finished_at: Date.now() - 1, last_activity_at: Date.now() - 1 } } as DeliveryStatusView;
  insert(completed);
  expect(view.result.current.tasks[0].status).toBe('completed');
  const rendered = render(<GroupTaskRow task={view.result.current.tasks[0]} now={Date.now()} unread pending={outcome !== 'late status'} onCancel={jest.fn()} onViewSource={jest.fn()} />);
  expect(screen.queryByRole('button', { name: /^(停止|取消|正在确认…)$/ })).not.toBeInTheDocument();
  rendered.unmount();
  await act(async () => {
    resolveStatus([{ ref: intentId, status: 'running' }]);
    if (cancelRequest) { settle(); await cancelRequest; }
  });
  expect(view.result.current.tasks[0]).toMatchObject({ status: 'completed', finishedAt: completed.task!.finished_at });
  expect(view.result.current.unread).toHaveLength(1);
  view.unmount();
});


it('recovers history once, reads newly loaded sources, and recovers again after reconnect', async () => {
  jest.useFakeTimers();
  try {
    query.mockClear();
    const messages = Array.from({ length: 100 }, (_, index) => ({ id: `history-${index}`, role: 'user', content: '', status: 'done' })) as never[];
    const participants: never[] = [];
    const view = renderHook(({ history, connected }) => useManagerWorkerTasks('s1', history, participants, connected),
      { initialProps: { history: messages, connected: true } });
    await act(async () => { await Promise.resolve(); });
    expect(query).toHaveBeenCalledTimes(1);
    expect(query.mock.calls[0][1]).toHaveLength(100);
    await act(async () => { jest.advanceTimersByTime(60_000); });
    expect(query).toHaveBeenCalledTimes(1);
    const older = { id: 'older', role: 'user', content: '', status: 'done' } as never;
    await act(async () => { view.rerender({ history: [older, ...messages], connected: true }); });
    expect(query).toHaveBeenLastCalledWith('s1', ['older']);
    await act(async () => { view.rerender({ history: [older, ...messages], connected: false }); });
    await act(async () => { jest.advanceTimersByTime(30_000); });
    expect(query).toHaveBeenCalledTimes(2);
    await act(async () => { view.rerender({ history: [older, ...messages], connected: true }); });
    expect(query).toHaveBeenCalledTimes(3);
    expect(query.mock.calls[2][1]).toHaveLength(101);
    view.unmount();
  } finally { jest.useRealTimers(); }
});

it('keeps recovering an old uncertain expired task and stops after confirmed completion', async () => {
  jest.useFakeTimers();
  try {
    const expired = row('old-expired', 'expired');
    insert(expired);
    for (let index = 0; index < 110; index++) insert(row(`done-${index}`, 'completed'));
    query.mockResolvedValue([expired]);
    query.mockClear();
    const messages = [{ id: 'recent-message', role: 'user', content: '', status: 'done' }] as never[];
    const participants: never[] = [];
    const view = renderHook(() => useManagerWorkerTasks('s1', messages, participants));
    await act(async () => { await Promise.resolve(); });
    expect(view.result.current.tasks.find((task) => task.deliveryId === expired.delivery_id)?.status).toBe('unknown');
    await act(async () => { jest.advanceTimersByTime(10_000); });
    expect(query).toHaveBeenLastCalledWith('s1', [expired.message_id]);
    query.mockResolvedValue([]);
    insert({ ...expired, status: 'completed', state_version: 2 });
    await act(async () => { await Promise.resolve(); });
    const calls = query.mock.calls.length;
    await act(async () => { jest.advanceTimersByTime(60_000); });
    expect(query).toHaveBeenCalledTimes(calls);
    view.unmount();
  } finally { jest.useRealTimers(); }
});

it('retries a failed initial history recovery before marking the source recovered', async () => {
  jest.useFakeTimers();
  try {
    query.mockClear();
    query.mockRejectedValueOnce(new Error('temporary failure')).mockResolvedValue([]);
    const messages = [{ id: 'retry-source', role: 'user', content: '', status: 'done' }] as never[];
    const participants: never[] = [];
    const view = renderHook(() => useManagerWorkerTasks('s1', messages, participants));
    await act(async () => { await Promise.resolve(); });
    await act(async () => { jest.advanceTimersByTime(10_000); });
    expect(query).toHaveBeenCalledTimes(2);
    expect(query).toHaveBeenLastCalledWith('s1', ['retry-source']);
    expect(view.result.current.error).toBeUndefined();
    await act(async () => { jest.advanceTimersByTime(60_000); });
    expect(query).toHaveBeenCalledTimes(2);
    view.unmount();
  } finally { jest.useRealTimers(); }
});
