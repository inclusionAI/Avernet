import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import userEvent from '@testing-library/user-event';
import SessionRecoveryDetail from '../SessionRecoveryDetail';
import { sessionRecoveryApi } from '../../../../../api/session-recovery';
vi.mock('../../../../../api/session-recovery', () => ({ sessionRecoveryApi: { detail: vi.fn(), decide: vi.fn(), annotate: vi.fn(), toolCall: vi.fn() } }));
const task = { taskId: 'task', status: 'waiting_owner', phase: 'apply', request: { diagnosis: '原任务失败', target: { bot_id: 'default' }, session_key: 'original' },
  judgment: { effect: 'not_recovered', summary: '原问题仍存在' }, plan: { digest: 'plan-digest', approvedAt: null, summary: '完整修复方案', basis: '原失败证据', scope: '当前目标', verification: '原操作成功', actions: [{ actionId: 'fix', summary: '修正配置', risk: '局部变更', verification: '读取配置', command: 'printf fixture', rollback: '恢复原值' }], owner_cooperation: [{ request: '确认权限已开放', basis: '权限缺失' }] },
  timeline: [{ event_id: 'event', at_ms: 1, actor: 'agent', summary: '提出方案', payload: { evidence: '原证据' } }], evidence: [{ evidence_id: 'evidence', tool_call_id: 'call', source: 'session', location: 'line:1', excerpt: '原失败消息' }] };
beforeEach(() => {
  vi.clearAllMocks();
  // jsdom does not implement browser layout or scrolling.
  Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', { configurable: true, value: vi.fn() });
  vi.mocked(sessionRecoveryApi.detail).mockResolvedValue(task as never);
  vi.mocked(sessionRecoveryApi.decide).mockResolvedValue({ ok: true });
  vi.mocked(sessionRecoveryApi.annotate).mockResolvedValue({ ok: true });
});
it('keeps the plan, Owner cooperation and approval together in stage three', async () => {
  const user = userEvent.setup();
  render(<SessionRecoveryDetail taskId="task" onBack={() => {}} />);
  await screen.findByRole('button', { name: '确认完整方案' });
  expect(screen.getByRole('tab', { name: /形成修复方案/ })).toHaveAttribute('aria-selected', 'true');
  expect(screen.getByRole('tab', { name: /形成修复方案/ })).toHaveAttribute('aria-current', 'step');
  expect(screen.getByText('完整修复方案')).toBeVisible();
  expect(screen.getByText('确认权限已开放')).toBeVisible();
  expect(screen.getByRole('heading', { name: '方案结论' })).toBeVisible();
  expect(screen.getByRole('heading', { name: '诊断依据' })).toBeVisible();
  expect(screen.getByRole('heading', { name: '影响范围' })).toBeVisible();
  expect(screen.getByRole('heading', { name: '验收方法' })).toBeVisible();
  expect(screen.getByRole('button', { name: '确认完整方案' })).toBeDisabled();
  await user.type(screen.getByLabelText('补充信息或处理说明'), '权限已开放');
  await user.click(screen.getByRole('tab', { name: /执行环境修复/ }));
  expect(screen.queryByText('完整修复方案')).toBeNull();
  expect(screen.queryByText('确认权限已开放')).toBeNull();
  expect(screen.queryByRole('button', { name: '确认完整方案' })).toBeNull();
  expect(screen.queryByLabelText('补充信息或处理说明')).toBeNull();
  await user.click(screen.getByRole('button', { name: '查看方案' }));
  expect(screen.getByRole('tab', { name: /形成修复方案/ })).toHaveAttribute('aria-selected', 'true');
  expect(screen.getByLabelText('补充信息或处理说明')).toHaveValue('权限已开放');
  await waitFor(() => expect(HTMLElement.prototype.scrollIntoView).toHaveBeenCalledWith({ behavior: 'smooth', block: 'start' }));
  expect(screen.getByRole('button', { name: '确认完整方案' })).toBeDisabled(); await user.click(screen.getByLabelText('我已处理方案中的配合事项'));
  expect(screen.getByRole('button', { name: '确认完整方案' })).toBeEnabled();
  await user.click(screen.getByRole('button', { name: '确认完整方案' }));
  await waitFor(() => expect(sessionRecoveryApi.decide).toHaveBeenCalledWith('task', 'plan-digest', true, true, '权限已开放'));
  expect(screen.queryByText('完整任务时间线')).toBeNull(); expect(screen.getByText('原失败消息')).toBeVisible();
});
it('loads full audited evidence on demand and retains manual judgment', async () => {
  vi.mocked(sessionRecoveryApi.toolCall).mockResolvedValue({ result: { stdout: '完整原始回执' } });
  render(<SessionRecoveryDetail taskId="task" onBack={() => {}} />); await screen.findByText('原失败消息');
  fireEvent.click(screen.getByText('查看完整操作原文与回执')); await waitFor(() => expect(sessionRecoveryApi.toolCall).toHaveBeenCalledWith('task', 'call'));
  await screen.findByText(/完整原始回执/);
  fireEvent.change(screen.getByLabelText('理由'), { target: { value: '前后原文已核对' } }); fireEvent.click(screen.getByLabelText('evidence'));
  fireEvent.click(screen.getByRole('button', { name: '保存标注' })); await waitFor(() => expect(sessionRecoveryApi.annotate).toHaveBeenCalledWith('task', { layer: 'effect', correct: true, reason: '前后原文已核对', evidence_ids: ['evidence'] }));
});
it('renders an explicit error without silently presenting an empty timeline', async () => {
  vi.mocked(sessionRecoveryApi.detail).mockRejectedValue(new Error('fixture offline')); render(<SessionRecoveryDetail taskId="task" onBack={() => {}} />);
  expect(await screen.findByRole('alert')).toBeVisible(); expect(screen.getByRole('button', { name: '重新加载' })).toBeVisible();
});

it('shows the business adjustment and defaults all technical execution detail to collapsed', async () => {
  const user = userEvent.setup();
  const value = { ...task, plan: { ...task.plan, actions: [task.plan.actions[0], { actionId: 'api', summary: '调整定时任务配置', risk: '保留正常任务', verification: '核对任务列表', rollback: '恢复原配置', engineOperation: { method: 'PUT', path: '/api/cron/daily', body: { enabled: false } } }] } };
  vi.mocked(sessionRecoveryApi.detail).mockResolvedValue(value as never);
  render(<SessionRecoveryDetail taskId="task" onBack={() => {}} />);
  await user.click(await screen.findByRole('tab', { name: /形成修复方案/ }));
  expect(await screen.findByText('修正配置')).toBeVisible();
  expect(screen.getByText('调整定时任务配置')).toBeVisible();
  expect(screen.getByText('确认权限已开放')).toBeVisible();
  expect(screen.getByText('printf fixture')).not.toBeVisible();
  expect(screen.getByText(/"path": "\/api\/cron\/daily"/)).not.toBeVisible();
  expect(screen.getByText('原失败证据')).toBeVisible();
  expect(screen.getByText('原操作成功')).toBeVisible();
  expect(screen.getByText('恢复原值', { exact: false })).toBeVisible();
  await user.click(screen.getAllByText('查看操作细节')[0]);
  expect(screen.getByText('printf fixture')).toBeVisible();
});

it('switches stages and shows every returned action with its status while retaining selection on refresh', async () => {
  const user = userEvent.setup();
  vi.mocked(sessionRecoveryApi.detail).mockResolvedValue({ ...task, status: 'running', plan: { ...task.plan, approvedAt: 4 }, timeline: [
    { event_id: 'read', at_ms: 1, kind: 'operation', actor: 'agent', summary: '读取原会话', payload: { status: 'succeeded', raw_tool_call_id: 'read', result: { summary: '定位到失败消息' } } },
    { event_id: 'assess', at_ms: 2, kind: 'assessment', actor: 'agent', summary: '需要修复环境', payload: { decision: 'environment' } },
    { event_id: 'check', at_ms: 3, kind: 'operation', actor: 'agent', summary: '核对配置失败', payload: { status: 'failed', result: { summary: '请求未完成', error: { message: '连接被拒绝' } } } },
    { event_id: 'plan', at_ms: 4, kind: 'plan', actor: 'agent', summary: '提出完整方案', payload: {} },
    { event_id: 'notify', at_ms: 4.1, kind: 'notification', actor: 'service', summary: '通知 Owner 确认', payload: { kind: 'plan' } },
    { event_id: 'approval', at_ms: 4.5, kind: 'owner_decision', actor: 'owner', summary: 'Owner 确认完整方案', payload: { approved: true } },
    { event_id: 'pending', at_ms: 5, kind: 'operation', actor: 'agent', summary: '等待执行配置修改', payload: { status: 'pending' } },
    { event_id: 'running', at_ms: 6, kind: 'operation', actor: 'agent', summary: '正在修改配置', payload: { status: 'running', request: { actionId: 'fix', purpose: '恢复正常的任务配置' } } },
  ] } as never);
  vi.mocked(sessionRecoveryApi.toolCall).mockResolvedValue({ result: { stdout: '原会话详细回执' } });
  render(<SessionRecoveryDetail taskId="task" onBack={() => {}} />);
  const tabs = await screen.findByRole('tablist');
  const panel = () => within(screen.getByRole('tabpanel'));
  expect(within(tabs).getByRole('tab', { name: /执行环境修复/ })).toHaveAttribute('aria-selected', 'true');
  expect(panel().getByText('等待执行配置修改')).toBeVisible();
  expect(panel().getByText('正在修改配置')).toBeVisible();
  expect(panel().getByText('等待执行')).toBeVisible();
  expect(panel().getByText('执行中')).toBeVisible();
  expect(panel().getByText('恢复正常的任务配置', { exact: false, selector: 'p' })).toBeVisible();
  expect(panel().queryByRole('button', { name: '确认完整方案' })).toBeNull();
  expect(panel().queryByText('修正配置')).toBeNull();
  expect(screen.queryByText('原会话响应')).toBeNull();
  expect(screen.queryByText('页面状态')).toBeNull();
  expect(screen.queryByText('完整任务时间线')).toBeNull();
  await user.click(panel().getByRole('button', { name: '方案动作：修正配置 →' }));
  expect(panel().getByText('修正配置')).toBeVisible();
  expect(panel().queryByRole('button', { name: '确认完整方案' })).toBeNull();
  expect(panel().getByText('核对配置失败')).toBeVisible();
  expect(panel().getByText('连接被拒绝', { exact: false, selector: 'p' })).toBeVisible();
  expect(panel().getByText('提出完整方案')).toBeVisible();
  expect(panel().getByText('通知 Owner 确认')).toBeVisible();
  expect(panel().getByText('Owner 确认完整方案')).toBeVisible();
  expect(panel().queryByText('读取原会话')).toBeNull();
  await user.click(within(tabs).getByRole('tab', { name: /判断干预方式/ }));
  expect(screen.queryByText('完整修复方案')).toBeNull();
  expect(panel().getByText('读取原会话')).toBeVisible();
  expect(panel().getByText('定位到失败消息')).toBeVisible();
  expect(sessionRecoveryApi.toolCall).not.toHaveBeenCalled();
  await user.click(panel().getAllByText('查看操作详情')[0]);
  await waitFor(() => expect(sessionRecoveryApi.toolCall).toHaveBeenCalledWith('task', 'read'));
  expect(await panel().findByText(/原会话详细回执/)).toBeVisible();
  await user.click(screen.getByRole('button', { name: '刷新任务' }));
  await waitFor(() => expect(sessionRecoveryApi.detail).toHaveBeenCalledTimes(2));
  expect(within(tabs).getByRole('tab', { name: /判断干预方式/ })).toHaveAttribute('aria-selected', 'true');
  await user.click(within(tabs).getByRole('tab', { name: /验证恢复效果/ }));
  expect(panel().getByText('此阶段暂无动作记录。')).toBeVisible();
  expect(screen.queryByText('完整修复方案')).toBeNull();
  await user.click(within(tabs).getByRole('tab', { name: /形成修复方案/ }));
  expect(within(tabs).getByRole('tab', { name: /形成修复方案/ })).toHaveAttribute('aria-selected', 'true');
  expect(panel().getByText('修正配置')).toBeVisible();
  expect(panel().queryByRole('button', { name: '确认完整方案' })).toBeNull();
  await user.click(within(tabs).getByRole('tab', { name: /执行环境修复/ }));
  expect(panel().queryByRole('button', { name: '确认完整方案' })).toBeNull();
  expect(panel().queryByText('Owner 确认完整方案')).toBeNull();
});
