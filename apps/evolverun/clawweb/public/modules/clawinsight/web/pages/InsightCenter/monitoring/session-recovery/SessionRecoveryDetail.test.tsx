import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import userEvent from '@testing-library/user-event';
import SessionRecoveryDetail from './SessionRecoveryDetail';
import { sessionRecoveryApi } from '../../../../api/session-recovery';
vi.mock('../../../../api/session-recovery', () => ({ sessionRecoveryApi: { detail: vi.fn(), decide: vi.fn(), annotate: vi.fn(), toolCall: vi.fn() } }));
const task = { taskId: 'task', status: 'waiting_owner', phase: 'apply', request: { diagnosis: '原任务失败', target: { bot_id: 'default' }, session_key: 'original' },
  judgment: { effect: 'not_recovered', summary: '原问题仍存在' }, plan: { digest: 'plan-digest', approvedAt: null, summary: '完整修复方案', basis: '原失败证据', scope: '当前目标', verification: '原操作成功', actions: [{ actionId: 'fix', summary: '修正配置', risk: '局部变更', verification: '读取配置', command: 'printf fixture', rollback: '恢复原值' }], owner_cooperation: [{ request: '确认权限已开放', basis: '权限缺失' }] },
  timeline: [{ event_id: 'event', at_ms: 1, actor: 'agent', summary: '提出方案', payload: { evidence: '原证据' } }], evidence: [{ evidence_id: 'evidence', tool_call_id: 'call', source: 'session', location: 'line:1', excerpt: '原失败消息' }] };
beforeEach(() => { vi.clearAllMocks(); vi.mocked(sessionRecoveryApi.detail).mockResolvedValue(task as never); vi.mocked(sessionRecoveryApi.decide).mockResolvedValue({ ok: true }); vi.mocked(sessionRecoveryApi.annotate).mockResolvedValue({ ok: true }); });
it('shows full plan and keeps approval disabled until Owner cooperation is confirmed', async () => {
  const user = userEvent.setup();
  render(<SessionRecoveryDetail taskId="task" onBack={() => {}} />); await screen.findByText('确认权限已开放');
  expect(screen.getByRole('button', { name: '确认完整方案' })).toBeDisabled(); await user.click(screen.getByLabelText('我已处理方案中的配合事项'));
  await user.type(screen.getByLabelText('补充信息或处理说明'), '权限已开放');
  expect(screen.getByRole('button', { name: '确认完整方案' })).toBeEnabled();
  await user.click(screen.getByRole('button', { name: '确认完整方案' }));
  await waitFor(() => expect(sessionRecoveryApi.decide).toHaveBeenCalledWith('task', 'plan-digest', true, true, '权限已开放'));
  expect(screen.getByText('完整任务时间线')).toBeVisible(); expect(screen.getByText('原失败消息')).toBeVisible();
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
  expect(await screen.findByText('修正配置')).toBeVisible();
  expect(screen.getByText('调整定时任务配置')).toBeVisible();
  expect(screen.getByText('确认权限已开放')).toBeVisible();
  expect(screen.getByText('printf fixture')).not.toBeVisible();
  expect(screen.getByText(/"path": "\/api\/cron\/daily"/)).not.toBeVisible();
  expect(screen.getByText('原失败证据')).not.toBeVisible();
  await user.click(screen.getAllByText('查看操作细节')[0]);
  expect(screen.getByText('printf fixture')).toBeVisible();
  await user.click(screen.getByText('查看方案依据与验收细节'));
  expect(screen.getByText('原失败证据')).toBeVisible();
});
