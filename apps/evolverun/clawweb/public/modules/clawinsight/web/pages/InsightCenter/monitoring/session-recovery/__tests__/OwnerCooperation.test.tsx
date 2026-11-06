import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import userEvent from '@testing-library/user-event';
import SessionRecoveryDetail from '../SessionRecoveryDetail';
import { sessionRecoveryApi } from '../../../../../api/session-recovery';

vi.mock('@avernet/clawweb-shared/web/api/client', () => ({ api: { evolve: { listTaskLogArchives: vi.fn().mockResolvedValue({ items: [] }) } } }));
vi.mock('../../../../../api/session-recovery', () => ({ sessionRecoveryApi: {
  detail: vi.fn(), decide: vi.fn(), confirmOwnerRequest: vi.fn(), annotate: vi.fn(), toolCall: vi.fn(), terminate: vi.fn(),
} }));

const request = {
  requestId: 'request/current', revision: 2, status: 'pending', reason: 'permission',
  summary: 'Bot 缺少目标文档的读取权限',
  instructions: '请为 `bot-reader` 开通[目标文档](https://docs.example.test/doc)的只读权限。\n\n完成后返回此处继续核验。',
  evidence_ids: ['permission-check'], resumePhase: 'observe', createdAtMs: 100,
};
const task = {
  taskId: 'task', shared: false, canOperate: true, canManageShare: false, level: 'automatic',
  status: 'waiting_owner', phase: 'observe', ownerRequest: request,
  request: { diagnosis: '原任务读取文档失败', target: { bot_id: 'default', entity_id: 'owner', env: 'prod', engine: 'OC' }, session_key: 'original', event_id: 'event', delivery_key: 'delivery', tc_fault_label: null },
  plan: { digest: 'historical-plan', approvedAt: null, summary: '历史环境方案', basis: '原失败证据', scope: '当前目标', verification: '原操作成功', actions: [], owner_cooperation: [{ request: '历史配合事项', basis: '旧证据' }] },
  judgment: null, evidence: [{ evidence_id: 'permission-check', tool_call_id: 'read', source: 'session_read', location: 'line:1', excerpt: '目标文档拒绝访问' }], timeline: [],
};

function notification(state: string, requestId = request.requestId, atMs = 10, channel = 'owner_card') {
  return { event_id: `${requestId}-${state}-${atMs}`, at_ms: atMs, kind: 'notification', actor: 'service', summary: '通知记录', payload: { channel, kind: 'owner_request', owner_request_id: requestId, state } };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(complete => { resolve = complete; });
  return { promise, resolve };
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(sessionRecoveryApi.detail).mockResolvedValue(task as never);
  vi.mocked(sessionRecoveryApi.confirmOwnerRequest).mockResolvedValue({ ok: true });
  Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', { configurable: true, value: vi.fn() });
});

it('shows the exact missing permission and actionable instructions without approving a retained plan', async () => {
  render(<SessionRecoveryDetail taskId="task" onBack={() => {}} />);
  const card = within(await screen.findByRole('region', { name: 'Owner 配合' }));
  expect(card.getByText(request.summary)).toBeVisible();
  expect(card.getByText('bot-reader')).toBeVisible();
  expect(card.getByRole('link', { name: '目标文档' })).toHaveAttribute('href', 'https://docs.example.test/doc');
  expect(card.getByText('完成后返回此处继续核验。')).toBeVisible();
  expect(card.getByText('等待 Owner 配合')).toBeVisible();
  expect(card.getByText('确认处理后，Agent 将重新核验权限，并继续原任务；恢复效果以核验结果为准。')).toBeVisible();
  expect(screen.queryByText('需要确认修复方案')).toBeNull();
  await userEvent.click(screen.getByRole('tab', { name: /形成修复方案/ }));
  expect(screen.getByText('历史环境方案')).toBeVisible();
  expect(screen.queryByRole('button', { name: '确认完整方案' })).toBeNull();
  expect(screen.queryByRole('button', { name: '拒绝方案' })).toBeNull();
  expect(screen.queryByRole('checkbox')).toBeNull();
  expect(sessionRecoveryApi.decide).not.toHaveBeenCalled();
});

it('confirms the current request with optional feedback and reloads the same task for verification', async () => {
  const feedback = '已为 bot-reader 开通只读权限';
  const confirmed = { ...task, status: 'running', phase: 'assess', ownerRequest: { ...request, status: 'confirmed', feedback: { userId: 'owner', text: feedback, atMs: 200 } } };
  vi.mocked(sessionRecoveryApi.detail).mockResolvedValueOnce(task as never).mockResolvedValue(confirmed as never);
  render(<SessionRecoveryDetail taskId="task" onBack={() => {}} />);
  const card = within(await screen.findByRole('region', { name: 'Owner 配合' }));
  await userEvent.type(card.getByRole('textbox', { name: '处理说明（可选）' }), feedback);
  await userEvent.click(card.getByRole('button', { name: '已处理，继续核验' }));
  await waitFor(() => expect(sessionRecoveryApi.confirmOwnerRequest).toHaveBeenCalledWith('task', 'request/current', feedback));
  await waitFor(() => expect(sessionRecoveryApi.detail).toHaveBeenCalledTimes(2));
  expect(await card.findByText('已反馈，正在核验')).toBeVisible();
  expect(card.getByText(feedback)).toBeVisible();
  expect(card.queryByRole('button', { name: '已处理，继续核验' })).toBeNull();
  expect(sessionRecoveryApi.decide).not.toHaveBeenCalled();
  expect(screen.queryByText('已恢复')).toBeNull();
});

it('prevents repeated submissions while confirmation is in flight', async () => {
  let resolve!: (result: { ok: true }) => void;
  vi.mocked(sessionRecoveryApi.confirmOwnerRequest).mockReturnValue(new Promise(result => { resolve = result; }));
  render(<SessionRecoveryDetail taskId="task" onBack={() => {}} />);
  const button = await screen.findByRole('button', { name: '已处理，继续核验' });
  fireEvent.click(button);
  expect(button).toBeDisabled();
  fireEvent.click(button);
  expect(sessionRecoveryApi.confirmOwnerRequest).toHaveBeenCalledOnce();
  vi.mocked(sessionRecoveryApi.detail).mockResolvedValue({ ...task, status: 'running', ownerRequest: { ...request, status: 'confirmed' } } as never);
  resolve({ ok: true });
  await screen.findByText('已反馈，正在核验');
});

it('ignores an older pending poll that finishes after the confirmation reload', async () => {
  const stalePoll = deferred<typeof task>();
  const confirmed = { ...task, status: 'running', ownerRequest: { ...request, status: 'confirmed' } };
  vi.mocked(sessionRecoveryApi.detail).mockResolvedValueOnce(task as never)
    .mockReturnValueOnce(stalePoll.promise as never).mockResolvedValue(confirmed as never);
  render(<SessionRecoveryDetail taskId="task" onBack={() => {}} />);
  const button = await screen.findByRole('button', { name: '已处理，继续核验' });
  fireEvent(document, new Event('visibilitychange'));
  await waitFor(() => expect(sessionRecoveryApi.detail).toHaveBeenCalledTimes(2));
  await userEvent.click(button);
  await screen.findByText('已反馈，正在核验');
  // The transport can finish even after its AbortSignal is canceled.
  await act(async () => { stalePoll.resolve(task); });
  expect(screen.getByText('已反馈，正在核验')).toBeVisible();
  expect(screen.queryByRole('button', { name: '已处理，继续核验' })).toBeNull();
});

it('retains the newly selected task when an earlier confirmation reload finishes', async () => {
  const lateReload = deferred<typeof task>();
  const nextTask = { ...task, taskId: 'next-task', request: { ...task.request, target: { ...task.request.target, bot_id: 'next-bot' } } };
  vi.mocked(sessionRecoveryApi.detail).mockResolvedValueOnce(task as never)
    .mockReturnValueOnce(lateReload.promise as never).mockResolvedValue(nextTask as never);
  const view = render(<SessionRecoveryDetail taskId="task" onBack={() => {}} />);
  await userEvent.click(await screen.findByRole('button', { name: '已处理，继续核验' }));
  await waitFor(() => expect(sessionRecoveryApi.detail).toHaveBeenCalledTimes(2));
  view.rerender(<SessionRecoveryDetail taskId="next-task" onBack={() => {}} />);
  await screen.findAllByText('next-bot');
  await act(async () => { lateReload.resolve({ ...task, status: 'running', ownerRequest: { ...request, status: 'confirmed' } }); });
  expect(screen.getAllByText('next-bot')[0]).toBeVisible();
  expect(screen.queryByText('正在读取任务…')).toBeNull();
});

it('does not reload a previous task when its confirmation finishes after navigation', async () => {
  const confirmation = deferred<{ ok: true }>();
  vi.mocked(sessionRecoveryApi.confirmOwnerRequest).mockReturnValueOnce(confirmation.promise);
  const nextTask = { ...task, taskId: 'next-task', request: { ...task.request, target: { ...task.request.target, bot_id: 'next-bot' } } };
  vi.mocked(sessionRecoveryApi.detail).mockResolvedValueOnce(task as never).mockResolvedValue(nextTask as never);
  const view = render(<SessionRecoveryDetail taskId="task" onBack={() => {}} />);
  await userEvent.click(await screen.findByRole('button', { name: '已处理，继续核验' }));
  view.rerender(<SessionRecoveryDetail taskId="next-task" onBack={() => {}} />);
  await screen.findAllByText('next-bot');
  await act(async () => { confirmation.resolve({ ok: true }); });
  expect(sessionRecoveryApi.detail).toHaveBeenCalledTimes(2);
  expect(screen.getAllByText('next-bot')[0]).toBeVisible();
});

it('shows cooperation content read-only to shared viewers', async () => {
  vi.mocked(sessionRecoveryApi.detail).mockResolvedValue({ ...task, shared: true, canOperate: false } as never);
  render(<SessionRecoveryDetail taskId="task" onBack={() => {}} />);
  const card = within(await screen.findByRole('region', { name: 'Owner 配合' }));
  expect(card.getByText(request.summary)).toBeVisible();
  expect(card.getByRole('link', { name: '目标文档' })).toBeVisible();
  expect(card.queryByRole('textbox')).toBeNull();
  expect(card.queryByRole('button')).toBeNull();
  expect(sessionRecoveryApi.confirmOwnerRequest).not.toHaveBeenCalled();
});

it.each([
  [undefined, 'Owner 通知：发送未确认'],
  ['sending', 'Owner 通知：正在发送'],
  ['sent', 'Owner 通知：已发送'],
  ['unconfirmed', 'Owner 通知：发送未确认'],
  ['disabled', 'Owner 通知：已关闭'],
  ['failed', 'Owner 通知：发送未确认'],
])('reports the current request notification fact for %s without inferring delivery', async (state, label) => {
  const timeline = [notification('sent', 'old-request', 30), notification('sent', request.requestId, 40, 'group_webhook')];
  if (state) timeline.push(notification(state, request.requestId, 20));
  vi.mocked(sessionRecoveryApi.detail).mockResolvedValue({ ...task, timeline } as never);
  render(<SessionRecoveryDetail taskId="task" onBack={() => {}} />);
  const card = within(await screen.findByRole('region', { name: 'Owner 配合' }));
  expect(card.getByText(label)).toBeVisible();
  if (state !== 'sent') expect(card.queryByText('Owner 通知：已发送')).toBeNull();
});

it('uses the latest matching notification result instead of a prior send attempt', async () => {
  vi.mocked(sessionRecoveryApi.detail).mockResolvedValue({ ...task, timeline: [notification('unconfirmed', request.requestId, 20), notification('sending', request.requestId, 10)] } as never);
  render(<SessionRecoveryDetail taskId="task" onBack={() => {}} />);
  expect(await screen.findByText('Owner 通知：发送未确认')).toBeVisible();
  expect(screen.queryByText('Owner 通知：正在发送')).toBeNull();
});

it.each([403, 409])('reloads a rejected confirmation after HTTP %s and explains the stale permission or request', async status => {
  vi.mocked(sessionRecoveryApi.confirmOwnerRequest).mockRejectedValue({ status });
  vi.mocked(sessionRecoveryApi.detail).mockResolvedValueOnce(task as never).mockResolvedValue({ ...task, canOperate: false, shared: true } as never);
  render(<SessionRecoveryDetail taskId="task" onBack={() => {}} />);
  await userEvent.click(await screen.findByRole('button', { name: '已处理，继续核验' }));
  await waitFor(() => expect(sessionRecoveryApi.detail).toHaveBeenCalledTimes(2));
  expect(await screen.findByRole('alert')).toHaveTextContent(status === 403 ? '当前账号无法确认此事项' : '处理事项已更新或结束');
  expect(screen.queryByRole('button', { name: '已处理，继续核验' })).toBeNull();
  expect(sessionRecoveryApi.decide).not.toHaveBeenCalled();
});

it('disables cooperation confirmation while stopping the task', async () => {
  const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
  vi.mocked(sessionRecoveryApi.terminate).mockReturnValue(new Promise(() => {}));
  try {
    render(<SessionRecoveryDetail taskId="task" onBack={() => {}} />);
    fireEvent.click(await screen.findByRole('button', { name: '停止任务' }));
    expect(screen.getByRole('button', { name: '已处理，继续核验' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: '已处理，继续核验' }));
    expect(sessionRecoveryApi.confirmOwnerRequest).not.toHaveBeenCalled();
  } finally { confirm.mockRestore(); }
});

it.each(['completed', 'failed', 'canceled'])('retains the cooperation record without continuation controls after %s', async status => {
  vi.mocked(sessionRecoveryApi.detail).mockResolvedValue({ ...task, status, phase: 'verify', ownerRequest: { ...request, status: 'confirmed', feedback: { userId: 'owner', text: '已处理权限', atMs: 200 } }, judgment: { effect: 'unknown', summary: '等待后续人工处理', evidence_ids: [] } } as never);
  render(<SessionRecoveryDetail taskId="task" onBack={() => {}} />);
  const card = within(await screen.findByRole('region', { name: 'Owner 配合' }));
  expect(card.getByText('配合处理已结束')).toBeVisible();
  expect(card.getByText('已处理权限')).toBeVisible();
  expect(card.queryByRole('button')).toBeNull();
  expect(card.queryByText('已反馈，正在核验')).toBeNull();
});
