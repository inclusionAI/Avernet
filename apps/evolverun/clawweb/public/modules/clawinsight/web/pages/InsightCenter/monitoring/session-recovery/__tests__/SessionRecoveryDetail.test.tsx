import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import userEvent from '@testing-library/user-event';
import SessionRecoveryDetail from '../SessionRecoveryDetail';
import { MemoryRouter } from 'react-router-dom';
import { RecoveryTaskLinks } from '../RecoveryTaskLinks';
import { api } from '@avernet/clawweb-shared/web/api/client';
vi.mock('@avernet/clawweb-shared/web/api/client', () => ({ api: { evolve: { listTaskLogArchives: vi.fn(), getTaskLogArchiveDownloadUrl: vi.fn() } } }));
import { sessionRecoveryApi } from '../../../../../api/session-recovery';
vi.mock('../../../../../api/session-recovery', () => ({ sessionRecoveryApi: { detail: vi.fn(), list: vi.fn(), decide: vi.fn(), annotate: vi.fn(), toolCall: vi.fn(), setTaskShared: vi.fn(), terminate: vi.fn() } }));
const task = { taskId: 'task', shared: false, canOperate: true, canManageShare: true, status: 'waiting_owner', phase: 'apply', request: { diagnosis: '原任务失败', target: { bot_id: 'default' }, session_key: 'original' },
  judgment: { effect: 'not_recovered', summary: '原问题仍存在' }, plan: { digest: 'plan-digest', approvedAt: null, summary: '完整修复方案', basis: '原失败证据', scope: '当前目标', verification: '原操作成功', actions: [{ actionId: 'fix', summary: '修正配置', risk: '局部变更', verification: '读取配置', command: 'printf fixture', rollback: '恢复原值' }], owner_cooperation: [{ request: '确认权限已开放', basis: '权限缺失' }] },
  timeline: [{ event_id: 'event', at_ms: 1, kind: 'plan', actor: 'agent', summary: '提出方案', payload: { evidence_ids: ['evidence'] } }], evidence: [{ evidence_id: 'evidence', tool_call_id: 'call', source: 'session', location: 'line:1', excerpt: '原失败消息' }] };
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.evolve.listTaskLogArchives).mockResolvedValue({ items: [] });
  // jsdom does not implement browser layout or scrolling.
  Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', { configurable: true, value: vi.fn() });
  vi.mocked(sessionRecoveryApi.detail).mockResolvedValue(task as never);
  vi.mocked(sessionRecoveryApi.decide).mockResolvedValue({ ok: true });
  vi.mocked(sessionRecoveryApi.annotate).mockResolvedValue({ ok: true });
  vi.mocked(sessionRecoveryApi.setTaskShared).mockResolvedValue(task as never);
  vi.mocked(sessionRecoveryApi.terminate).mockResolvedValue({ ...task, status: 'canceled', termination: { status: 'remote_stopped', aisJobId: 'job' } } as never);
});
it('confirms stopping the task and then removes all continuation controls', async () => {
  const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
  try {
    render(<SessionRecoveryDetail taskId="task" onBack={() => {}} />);
    const stop = await screen.findByRole('button', { name: '停止任务' });
    fireEvent.click(stop); expect(sessionRecoveryApi.terminate).not.toHaveBeenCalled();
    confirm.mockReturnValue(true);
    vi.mocked(sessionRecoveryApi.detail).mockResolvedValue({ ...task, status: 'canceled', termination: { status: 'remote_stopped', aisJobId: 'job' } } as never);
    fireEvent.click(stop);
    await waitFor(() => expect(sessionRecoveryApi.terminate).toHaveBeenCalledWith('task', '用户手动停止任务'));
    await screen.findByText('本次自愈已停止');
    expect(screen.getByText(/AIS 已停止/)).toBeVisible();
    expect(screen.queryByRole('button', { name: /停止任务|确认完整方案|拒绝方案|重试停止 AIS/ })).toBeNull();
  } finally { confirm.mockRestore(); }
});
it('keeps a failed AIS stop visible and lets the Owner retry stopping the same task', async () => {
  const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
  vi.mocked(sessionRecoveryApi.detail).mockResolvedValueOnce({ ...task, status: 'canceled', termination: { status: 'remote_stop_failed', aisJobId: 'job' } } as never)
    .mockResolvedValue({ ...task, status: 'canceled', termination: { status: 'remote_stopped', aisJobId: 'job' } } as never);
  try {
    render(<SessionRecoveryDetail taskId="task" onBack={() => {}} />);
    expect(await screen.findByRole('alert')).toHaveTextContent('AIS 停止请求未成功');
    fireEvent.click(screen.getByRole('button', { name: '重试停止 AIS' }));
    await waitFor(() => expect(sessionRecoveryApi.terminate).toHaveBeenCalledOnce());
    await screen.findByText(/AIS 已停止/);
  } finally { confirm.mockRestore(); }
});
it('lets the Owner open and close sharing using the existing task controls', async () => {
  vi.mocked(sessionRecoveryApi.detail).mockResolvedValueOnce(task as never).mockResolvedValue({ ...task, shared: true } as never);
  render(<SessionRecoveryDetail taskId="task" onBack={() => {}} />);
  fireEvent.click(await screen.findByRole('button', { name: '分享' }));
  await waitFor(() => expect(sessionRecoveryApi.setTaskShared).toHaveBeenCalledWith('task', true));
  await screen.findByText('已公开分享');
  vi.mocked(sessionRecoveryApi.detail).mockResolvedValue(task as never);
  fireEvent.click(screen.getByRole('button', { name: '关闭分享' }));
  await waitFor(() => expect(sessionRecoveryApi.setTaskShared).toHaveBeenLastCalledWith('task', false));
  await screen.findByRole('button', { name: '分享' });
});
it('shows shared tasks read-only without approval, Owner cooperation, annotations or Agent log downloads', async () => {
  vi.mocked(sessionRecoveryApi.detail).mockResolvedValue({ ...task, shared: true, canOperate: false, canManageShare: false } as never);
  render(<SessionRecoveryDetail taskId="task" onBack={() => {}} />);
  await screen.findByText('共享的只读自愈任务');
  expect(screen.getByText('完整修复方案')).toBeVisible();
  expect(screen.queryByRole('button', { name: /确认完整方案|拒绝方案|关闭分享|^分享$|停止任务|重试停止 AIS/ })).not.toBeInTheDocument();
  expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
  expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
  expect(api.evolve.listTaskLogArchives).not.toHaveBeenCalled();
  expect(sessionRecoveryApi.decide).not.toHaveBeenCalled();
  expect(sessionRecoveryApi.annotate).not.toHaveBeenCalled();
});
it('clears shared task contents when the Owner revokes sharing', async () => {
  vi.mocked(sessionRecoveryApi.detail).mockResolvedValue({ ...task, shared: true, canOperate: false, canManageShare: false } as never);
  render(<SessionRecoveryDetail taskId="task" onBack={() => {}} />);
  await screen.findByText('完整修复方案');
  vi.mocked(sessionRecoveryApi.detail).mockRejectedValue({ status: 403 });
  fireEvent.click(screen.getByRole('button', { name: '刷新任务' }));
  await screen.findByText('权限不足，请联系任务 Owner 开启分享。');
  expect(screen.queryByText('完整修复方案')).not.toBeInTheDocument();
  expect(screen.queryByText('共享的只读自愈任务')).not.toBeInTheDocument();
});
it('keeps the plan, Owner cooperation and approval together in stage three', async () => {
  const user = userEvent.setup();
  render(<SessionRecoveryDetail taskId="task" onBack={() => {}} />);
  await screen.findByRole('button', { name: '确认完整方案' });
  expect(screen.getByRole('tab', { name: /形成修复方案/ })).toHaveAttribute('aria-selected', 'true');
  expect(screen.getByRole('tab', { name: /形成修复方案/ })).toHaveAttribute('aria-current', 'step');
  expect(screen.getByText('完整修复方案')).toBeVisible();
  expect(screen.getByText('确认权限已开放')).toBeVisible();
  expect(screen.getByRole('heading', { name: '问题与处理思路' })).toBeVisible();
  const arrangement = screen.getByRole('region', { name: '完整执行安排' });
  expect(within(arrangement).getByText('确认权限已开放')).toBeVisible();
  expect(within(arrangement).getByText('修正配置')).toBeVisible();
  expect(arrangement.querySelectorAll('.sr-plan-step')).toHaveLength(2);
  expect(screen.getByText('查看诊断依据与处理范围')).toBeVisible();
  expect(screen.getByRole('heading', { name: '验收与确认' })).toBeVisible();
  expect(screen.queryByRole('heading', { name: /主要调整动作|需要 Owner 配合/ })).toBeNull();
  expect(screen.queryByText('人工复核')).toBeNull();
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
  expect(screen.queryByText('完整任务时间线')).toBeNull(); await user.click(screen.getByText('展开详情')); expect(screen.getByText('原失败消息')).toBeVisible();
});
it('retains audited evidence and shows a simple collapsed review only after task completion', async () => {
  vi.mocked(sessionRecoveryApi.detail).mockResolvedValue({ ...task, status: 'completed', phase: 'verify', plan: { ...task.plan, approvedAt: 2 } } as never);
  vi.mocked(sessionRecoveryApi.toolCall).mockResolvedValue({ result: { stdout: '完整原始回执' } });
  render(<SessionRecoveryDetail taskId="task" onBack={() => {}} />);
  fireEvent.click(await screen.findByRole('tab', { name: /形成修复方案/ }));
  await screen.findByText('原失败消息');
  fireEvent.click(screen.getByText('展开详情')); fireEvent.click(screen.getByText('查看完整操作回执')); await waitFor(() => expect(sessionRecoveryApi.toolCall).toHaveBeenCalledWith('task', 'call'));
  await screen.findByText(/完整原始回执/);
  const review = screen.getByText('人工复核').closest('details')!;
  expect(review).not.toHaveAttribute('open');
  expect(review.closest('[role="tabpanel"]')).toBeNull();
  expect(screen.getByRole('region', { name: '任务概览' }).nextElementSibling).toBe(review.closest('section'));
  fireEvent.click(screen.getByText('人工复核'));
  expect(screen.queryByText('引用证据')).toBeNull();
  fireEvent.change(screen.getByLabelText('理由'), { target: { value: '前后原文已核对' } });
  fireEvent.click(screen.getByRole('tab', { name: /投递恢复消息/ }));
  expect(screen.getByLabelText('理由')).toHaveValue('前后原文已核对');
  fireEvent.click(screen.getByRole('button', { name: '保存标注' }));
  await waitFor(() => expect(sessionRecoveryApi.annotate).toHaveBeenCalledWith('task', { layer: 'effect', correct: true, reason: '前后原文已核对', evidence_ids: [] }));
  expect(await screen.findByRole('status')).toHaveTextContent('人工复核已保存');
  fireEvent.change(screen.getByLabelText('理由'), { target: { value: '补充核对结果' } });
  expect(screen.queryByText('人工复核已保存，Agent 原判断保留。')).toBeNull();
});
it('renders an explicit error without silently presenting an empty timeline', async () => {
  vi.mocked(sessionRecoveryApi.detail).mockRejectedValue(new Error('fixture offline')); render(<SessionRecoveryDetail taskId="task" onBack={() => {}} />);
  expect(await screen.findByRole('alert')).toBeVisible(); expect(screen.getByRole('button', { name: '重新加载' })).toBeVisible();
});

it('shows each retry reason beside the AIS link for that exact execution', async () => {
  vi.mocked(sessionRecoveryApi.detail).mockResolvedValue({ ...task, status: 'running', phase: 'assess', plan: null, evidence: [], executions: [
    { executionId: 'exec-1', attempt: 1, status: 'failed', jobId: '101', jobUrl: 'https://ais.example.test/jobs/101' },
    { executionId: 'exec-2', attempt: 2, status: 'failed', jobId: '202', jobUrl: 'https://ais.example.test/jobs/202' },
    { executionId: 'exec-3', attempt: 3, status: 'dispatched', jobId: '303', jobUrl: 'https://ais.example.test/jobs/303' },
  ], timeline: [
    { event_id: 'retry-1', at_ms: 1, kind: 'execution', actor: 'service', summary: '容器创建失败，20 秒后重试', payload: { execution_id: 'exec-1' } },
    { event_id: 'retry-2', at_ms: 2, kind: 'execution', actor: 'service', summary: '启动超时，60 秒后重试', payload: { execution_id: 'exec-2' } },
    { event_id: 'start-3', at_ms: 3, kind: 'execution', actor: 'service', summary: '启动自愈 Agent', payload: { execution_id: 'exec-3' } },
  ] } as never);
  render(<SessionRecoveryDetail taskId="task" onBack={() => {}} />);
  const first = (await screen.findByRole('heading', { name: '容器创建失败，20 秒后重试' })).closest('article')!;
  const second = screen.getByRole('heading', { name: '启动超时，60 秒后重试' }).closest('article')!;
  expect(within(first).getByRole('link', { name: '查看 AIS 作业 101' })).toHaveAttribute('href', 'https://ais.example.test/jobs/101');
  expect(within(second).getByRole('link', { name: '查看 AIS 作业 202' })).toHaveAttribute('href', 'https://ais.example.test/jobs/202');
  expect(screen.getByRole('link', { name: '查看 AIS 作业 303' })).toHaveAttribute('target', '_blank');
  expect(within(first).queryByRole('link', { name: /202|303/ })).toBeNull();
});

it('distinguishes a failed dispatch from a pending allocation and never renders an unsafe job URL', async () => {
  vi.mocked(sessionRecoveryApi.detail).mockResolvedValue({ ...task, status: 'running', phase: 'assess', plan: null, evidence: [], executions: [
    { executionId: 'failed', attempt: 1, status: 'failed', jobId: null, jobUrl: null },
    { executionId: 'pending', attempt: 2, status: 'created', jobId: null, jobUrl: null },
    { executionId: 'unsafe', attempt: 3, status: 'dispatched', jobId: '303', jobUrl: 'javascript:alert(1)' },
  ], timeline: ['failed', 'pending', 'unsafe'].map((executionId, index) => ({ event_id: executionId, at_ms: index, kind: 'execution', actor: 'service', summary: `执行 ${index + 1}`, payload: { execution_id: executionId } })) } as never);
  render(<SessionRecoveryDetail taskId="task" onBack={() => {}} />);
  expect(await screen.findByText('未取得 AIS 作业编号')).toBeVisible();
  expect(screen.getByText('AIS 作业分配中')).toBeVisible();
  expect(screen.queryByRole('link', { name: /查看 AIS 作业/ })).toBeNull();
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
  expect(screen.getByText('原失败证据')).not.toBeVisible();
  await user.click(screen.getByText('查看诊断依据与处理范围'));
  expect(screen.getByText('原失败证据')).toBeVisible();
  expect(screen.getByText('原操作成功')).toBeVisible();
  await user.click(screen.getAllByText('查看风险、验证与回退')[0]);
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
  expect(panel().getByRole('heading', { name: '等待执行配置修改' })).toBeVisible();
  expect(panel().getByRole('heading', { name: '恢复正常的任务配置' })).toBeVisible();
  expect(panel().getByText('等待执行')).toBeVisible();
  expect(panel().getByText('执行中')).toBeVisible();
  await user.click(within(panel().getByRole('heading', { name: '恢复正常的任务配置' }).closest('article')!).getByText('展开详情'));
  expect(panel().queryByRole('button', { name: '确认完整方案' })).toBeNull();
  expect(panel().queryByText('修正配置')).toBeNull();
  expect(screen.queryByText('原会话响应')).toBeNull();
  expect(screen.queryByText('页面状态')).toBeNull();
  expect(screen.queryByText('完整任务时间线')).toBeNull();
  await user.click(panel().getByRole('button', { name: '方案动作：修正配置 →' }));
  expect(panel().getByText('修正配置')).toBeVisible();
  expect(panel().queryByRole('button', { name: '确认完整方案' })).toBeNull();
  const failedCall = panel().getByRole('heading', { name: '核对配置失败' }).closest('article')!;
  expect(within(failedCall).getByText('连接被拒绝', { exact: false, selector: 'p' })).toBeVisible();
  await user.click(within(failedCall).getByText('展开详情'));
  expect(panel().getByRole('heading', { name: '提交修复方案' })).toBeVisible();
  expect(panel().getByRole('heading', { name: '发送通知' })).toBeVisible();
  expect(panel().getByRole('heading', { name: '记录 Owner 的方案决定' })).toBeVisible();
  expect(panel().queryByText('读取原会话')).toBeNull();
  await user.click(within(tabs).getByRole('tab', { name: /判断干预方式/ }));
  expect(screen.queryByText('完整修复方案')).toBeNull();
  expect(panel().getByText('读取原会话')).toBeVisible();
  expect(panel().getByText('定位到失败消息')).toBeVisible();
  expect(panel().getByText('制定修复方案')).toBeVisible();
  expect(sessionRecoveryApi.toolCall).not.toHaveBeenCalled();
  await user.click(panel().getAllByText('展开详情')[0]);
  await waitFor(() => expect(sessionRecoveryApi.toolCall).toHaveBeenCalledWith('task', 'read'));
  await user.click(within(panel().getByRole('heading', { name: '读取原会话', exact: true }).closest('article')!).getByText('技术信息')); expect(within(within(panel().getByRole('heading', { name: '读取原会话', exact: true }).closest('article')!).getByText('技术信息').closest('details')!).getByText(/原会话详细回执/)).toBeVisible();
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


it('keeps long reasoning out of action headings and shows the evidence beside the judgment', async () => {
  const user = userEvent.setup();
  const reasoning = '原 Bot 已自行完成权限诊断，通过工具确认目标知识库不在可访问列表，并确认文档读取权限不足。'.repeat(5);
  vi.mocked(sessionRecoveryApi.detail).mockResolvedValue({ ...task, status: 'running', phase: 'observe', plan: null, timeline: [
    { event_id: 'read', at_ms: 1, kind: 'operation', actor: 'agent', summary: '读取原会话', payload: { raw_tool_call_id: 'call', status: 'succeeded', request: { purpose: '读取原会话' } } },
    { event_id: 'assess', at_ms: 2, kind: 'assessment', actor: 'agent', summary: reasoning, payload: { decision: 'observe', evidence_ids: ['evidence'] } },
  ] } as never);
  render(<SessionRecoveryDetail taskId="task" onBack={() => {}} />);
  await user.click(await screen.findByRole('tab', { name: /判断干预方式/ }));
  const panel = within(screen.getByRole('tabpanel'));
  expect(panel.getByRole('heading', { name: '判断干预方式', exact: true })).toBeVisible();
  expect(panel.queryByRole('heading', { name: reasoning })).toBeNull();
  expect(screen.queryByRole('heading', { name: '故障与恢复证据' })).toBeNull();
  const judgment = panel.getByRole('heading', { name: '判断干预方式', exact: true }).closest('article')!;
  expect(within(judgment).getByText('处理方式：')).toBeVisible();
  expect(within(judgment).getByText('继续观察')).toBeVisible();
  expect(judgment.querySelector('.sr-long-text')).not.toBeVisible();
  await user.click(within(judgment).getByText('展开详情'));
  expect(judgment.querySelector('.sr-long-text')).toBeVisible();
  expect(judgment.querySelector('.sr-long-text')?.textContent?.replace(/\s/g, '')).toBe(reasoning.replace(/\s/g, ''));
  expect(within(judgment).getByText('原失败消息')).toBeVisible();
  expect(within(judgment).getByText('line:1')).not.toBeVisible();
});

it('rejects non-task responses without crashing and retains the last successful task', async () => {
  const user = userEvent.setup();
  vi.mocked(sessionRecoveryApi.detail).mockResolvedValueOnce({ error: 'unavailable' } as never).mockResolvedValueOnce(task as never);
  render(<SessionRecoveryDetail taskId="task" onBack={() => {}} />);
  expect(await screen.findByRole('alert')).toHaveTextContent('自愈任务详情响应不完整');
  await user.click(screen.getByRole('button', { name: '重新加载' }));
  expect(await screen.findByRole('button', { name: '确认完整方案' })).toBeVisible();
  vi.mocked(sessionRecoveryApi.detail).mockResolvedValue({ ...task, timeline: null } as never);
  await user.click(screen.getByRole('button', { name: '刷新任务' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('保留上次成功读取的数据');
  expect(screen.getByRole('button', { name: '确认完整方案' })).toBeVisible();
});

it('renders an actionable Owner request with a document link and collapses the technical basis', async () => {
  const user = userEvent.setup();
  const request = '**请为 Bot 申请目标文档的只读权限**\n\n- 目标文档：[打开目标文档](https://docs.example.com/team/book/doc)\n- 联系人：文档所有者 `document-owner`\n- 授权账号：`bot-reader`（Bot 使用的账号）\n\n完成后，勾选配合事项并确认方案。';
  vi.mocked(sessionRecoveryApi.detail).mockResolvedValue({ ...task, plan: { ...task.plan, owner_cooperation: [{ request, basis: '**权限检查**\n\n- 当前账号没有读取权限。' }] } } as never);
  render(<SessionRecoveryDetail taskId="task" onBack={() => {}} />);
  expect(await screen.findByText('请为 Bot 申请目标文档的只读权限')).toBeVisible();
  const link = screen.getByRole('link', { name: '打开目标文档' });
  expect(link).toHaveAttribute('href', 'https://docs.example.com/team/book/doc');
  expect(link).toHaveAttribute('target', '_blank');
  expect(screen.getByText('bot-reader')).toBeVisible();
  expect(screen.getByText('当前账号没有读取权限。')).not.toBeVisible();
  await user.click(screen.getByText('查看这项安排的依据'));
  expect(screen.getByText('当前账号没有读取权限。').closest('li')).toBeVisible();
  expect(screen.getByText('权限检查').tagName).toBe('STRONG');
  expect(screen.getByRole('button', { name: '确认完整方案' })).toBeDisabled();
  await user.click(screen.getByLabelText('我已处理方案中的配合事项'));
  expect(screen.getByRole('button', { name: '确认完整方案' })).toBeEnabled();
});

it('separates plan conclusions, action titles, execution detail and evidence using the existing strings', async () => {
  const user = userEvent.setup();
  const summary = '### 问题\n\n目标文档缺少读取权限。\n\n### 处理\n\n申请访问权限后重试原任务。';
  const operation = '**引导 Bot 申请文档读取权限**\n\n**执行安排**\n\n1. 检查是否有可用申请能力。\n2. 没有申请能力时由 Owner 联系资源所有者。';
  vi.mocked(sessionRecoveryApi.detail).mockResolvedValue({ ...task, plan: { ...task.plan, summary, basis: '**调查结果**\n\n- 已确认当前账号不能读取目标文档。', verification: '1. 原会话实际取得文档正文。\n2. 回答依据该文档完成原始要求。', actions: [{ ...task.plan.actions[0], summary: operation }] } } as never);
  render(<SessionRecoveryDetail taskId="task" onBack={() => {}} />);
  expect(await screen.findByRole('heading', { name: '问题', exact: true })).toBeVisible();
  expect(screen.getByRole('heading', { name: '处理', exact: true })).toBeVisible();
  expect(screen.getByText('引导 Bot 申请文档读取权限')).toBeVisible();
  expect(screen.getByText('检查是否有可用申请能力。')).toBeVisible();
  await user.click(screen.getByText('查看诊断依据与处理范围'));
  expect(screen.getByText('已确认当前账号不能读取目标文档。')).toBeVisible();
  expect(screen.getByText('原会话实际取得文档正文。')).toBeVisible();
  expect(screen.getByRole('button', { name: '确认完整方案' })).toBeDisabled();
  await user.click(screen.getByLabelText('我已处理方案中的配合事项'));
  await user.click(screen.getByRole('button', { name: '确认完整方案' }));
  await waitFor(() => expect(sessionRecoveryApi.decide).toHaveBeenCalledWith('task', 'plan-digest', true, true, ''));
});


it('retains long purposes and separates linked conclusions from results and actual requests', async () => {
  const user = userEvent.setup();
  const purpose = '读取目标 Bot 当前的 MCP 配置和服务状态，核对原会话中语雀文档读取失败所使用的实际配置';
  const conclusion = '**配置核查**：服务 `enabled`，文档访问权限仍需单独核对。';
  vi.mocked(sessionRecoveryApi.detail).mockResolvedValue({ ...task, status: 'running', phase: 'assess', plan: null, timeline: [
    { event_id: 'read', at_ms: 1, kind: 'operation', actor: 'agent', summary: purpose, payload: { phase: 'assess', raw_tool_call_id: 'call', status: 'succeeded', request: { purpose, operation: 'engine_read', request: { method: 'GET', path: '/api/mcp' } }, result: { summary: '配置读取成功', httpStatus: 200 } } },
    { event_id: 'conclusion', at_ms: 2, kind: 'observation', actor: 'agent', summary: conclusion, payload: { phase: 'assess', sourceToolCallId: 'call', evidenceToolCallIds: ['call'], conclusionZh: conclusion, nextAction: '继续核对文档权限' } },
  ] } as never);
  vi.mocked(sessionRecoveryApi.toolCall).mockResolvedValue({ status: 'succeeded', request: { purpose, operation: 'engine_read', request: { method: 'GET', path: '/api/mcp' } }, result: { httpStatus: 200 } });
  render(<SessionRecoveryDetail taskId="task" onBack={() => {}} />);
  const card = (await screen.findByRole('heading', { name: purpose })).closest('article')!;
  expect(card.querySelector('.sr-result-preview')).toHaveTextContent('配置读取成功');
  expect(card.querySelector('.sr-result-preview')).not.toHaveTextContent('文档访问权限');
  expect(card.querySelector('.sr-process-conclusion')).toBeVisible();
  expect(card.querySelector('.sr-process-conclusion')).toHaveTextContent('文档访问权限仍需单独核对');
  expect(within(card).getByText('继续核对文档权限')).toBeVisible();
  await user.click(within(card).getByText('展开详情'));
  expect(within(card).getByRole('heading', { name: '结论' })).toBeVisible();
  expect(within(card).getByText('Bot default')).toBeVisible();
  const fullConclusion = within(card.querySelector('.sr-operation-body')! as HTMLElement);
  expect(fullConclusion.getByText('配置核查').tagName).toBe('STRONG');
  expect(fullConclusion.getByText('enabled').tagName).toBe('CODE');
  expect(fullConclusion.getByText(/文档访问权限仍需单独核对/)).toBeVisible();
  await user.click(within(card).getByText('查看本次调用的接口请求'));
  expect(within(within(card).getByText('查看本次调用的接口请求').closest('details')!).getByText(/"path": "\/api\/mcp"/)).toBeVisible();
  expect(within(card).queryByText('查看本次调用的 Shell 命令')).toBeNull();
});


it('preserves raw evidence including angle brackets and Markdown punctuation', async () => {
  const excerpt = '<tool_result>**原始返回** & `literal`</tool_result>\n第二行原文';
  vi.mocked(sessionRecoveryApi.detail).mockResolvedValue({ ...task, evidence: [{ ...task.evidence[0], excerpt }] } as never);
  render(<SessionRecoveryDetail taskId="task" onBack={() => {}} />);
  fireEvent.click(await screen.findByText('展开详情'));
  const evidence = document.querySelector('.sr-evidence-excerpt')!;
  expect(evidence).toBeVisible();
  expect(evidence.textContent).toBe(excerpt);
  expect(evidence.children).toHaveLength(0);
});

it('downloads an archived self-healing Agent session using the existing log archive API', async () => {
  vi.mocked(api.evolve.listTaskLogArchives).mockResolvedValue({ items: [{ archiveId: 'AIS-7', status: 'succeeded',
    metadata: { source: 'ais', stepId: 'task-AIS-2', executionId: 'exec-2' }, artifact: { size: 110183 } }] } as never);
  vi.mocked(api.evolve.getTaskLogArchiveDownloadUrl).mockResolvedValue({ url: 'https://oss.example.test/session.tar.gz', filename: 'agent-session.tar.gz' });
  const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
  const user = userEvent.setup();
  render(<SessionRecoveryDetail taskId="task" onBack={() => {}} />);
  await user.click(await screen.findByRole('button', { name: '下载 Agent 会话日志 · task-AIS-2' }));
  await waitFor(() => expect(api.evolve.getTaskLogArchiveDownloadUrl).toHaveBeenCalledWith('task', 'AIS-7'));
  expect(click).toHaveBeenCalledTimes(1);
  vi.mocked(api.evolve.getTaskLogArchiveDownloadUrl).mockRejectedValue({ status: 503 });
  await user.click(screen.getByRole('button', { name: '下载 Agent 会话日志 · task-AIS-2' }));
  expect(await screen.findByText('监控服务暂不可用，请稍后重试。')).toBeVisible();
  expect(click).toHaveBeenCalledTimes(1);
  click.mockRestore();
});

it('keeps task details usable when log downloads are forbidden', async () => {
  vi.mocked(api.evolve.listTaskLogArchives).mockRejectedValue({ status: 403 });
  render(<SessionRecoveryDetail taskId="task" onBack={() => {}} />);
  expect(await screen.findByText('只有任务 Owner 或管理员可下载 Agent 会话日志。')).toBeVisible();
  expect(screen.getByText('完整修复方案')).toBeVisible();
  expect(screen.queryByRole('button', { name: /下载 Agent 会话日志/ })).toBeNull();
});

it('shows an Owner-only plan as one complete arrangement without fabricating an Agent action', async () => {
  vi.mocked(sessionRecoveryApi.detail).mockResolvedValue({ ...task, plan: { ...task.plan, actions: [] } } as never);
  render(<SessionRecoveryDetail taskId="task" onBack={() => {}} />);
  const arrangement = await screen.findByRole('region', { name: '完整执行安排' });
  expect(arrangement.querySelectorAll('.sr-plan-step')).toHaveLength(1);
  expect(within(arrangement).getByText('确认权限已开放')).toBeVisible();
  expect(within(arrangement).queryByText('自愈 Agent')).toBeNull();
  expect(screen.queryByText('方案未列出具体操作。')).toBeNull();
  expect(screen.getByRole('button', { name: '确认完整方案' })).toBeDisabled();
});


it('shows normal user handoff without a recovery claim or continuation controls', async () => {
  vi.mocked(sessionRecoveryApi.detail).mockResolvedValue({ ...task, status: 'completed', completionReason: 'user_continued',
    judgment: { effect: 'unknown', summary: '用户已继续对话，本次已让路', evidence_ids: [] } } as never);
  render(<SessionRecoveryDetail taskId="task" onBack={() => {}} />);
  await screen.findAllByText('已让路');
  expect(screen.getByText('用户已继续对话，本次自愈已让路，不再自动干预。')).toBeVisible();
  expect(screen.queryByText('原任务已恢复，可查看下方处理过程和判断依据。')).toBeNull();
  expect(screen.queryByRole('button', { name: /停止任务|确认完整方案|拒绝方案/ })).toBeNull();
});


it('labels the diagnosis task link as a normal handoff', async () => {
  vi.mocked(sessionRecoveryApi.list).mockResolvedValue({ items: [{ task_id: 'task', status: 'completed', phase: 'assess', effect: 'unknown', completion_reason: 'user_continued' }] });
  render(<MemoryRouter><RecoveryTaskLinks eventId="event" /></MemoryRouter>);
  expect(await screen.findByRole('link', { name: '查看自愈任务 已让路 task' })).toHaveAttribute('href', '/insight?module=monitoring&recoveryTaskId=task');
  expect(screen.getByText('已让路')).toBeVisible();
});

it('shows simulation decisions and the complete proposed message with only assess/plan stages', async () => {
  vi.mocked(sessionRecoveryApi.detail).mockResolvedValue({ ...task, status: 'completed', phase: 'assess', plan: null,
    simulation: { batchId: 'fixture', cutoffAtMs: 10000, cutoffNode: { messageId: 'M100' }, inputDigest: 'digest' },
    timeline: [{ event_id: 'decision', at_ms: 1, kind: 'assessment', actor: 'agent', summary: '原任务可从失败步骤继续',
      payload: { phase: 'assess', decision: 'message', message: '请核对原调用参数并继续原任务，避免重复已完成操作。' } }] } as never);
  render(<SessionRecoveryDetail taskId="task" onBack={() => {}} />);
  await screen.findByRole('heading', { name: '自愈离线仿真详情' });
  expect(screen.getAllByRole('tab')).toHaveLength(2);
  expect(screen.getByText('拟发送的干预消息（未发送）')).toBeVisible();
  expect(screen.getByText('请核对原调用参数并继续原任务，避免重复已完成操作。')).toBeVisible();
  expect(screen.queryByRole('button', { name: /确认完整方案|拒绝方案|保存标注|创建仿真/ })).toBeNull();
});


it('shows target unavailability as an ended workflow with unknown recovery effect', async () => {
  vi.mocked(sessionRecoveryApi.detail).mockResolvedValue({ ...task, status: 'completed', completionReason: 'target_unavailable',
    judgment: { effect: 'unknown', summary: 'Provider确认目标沙箱已销毁', evidence_ids: [] } } as never);
  render(<SessionRecoveryDetail taskId="task" onBack={() => {}} />);
  await screen.findAllByText('目标不可用');
  expect(screen.getByText('目标运行环境已不可用，本次自愈已结束；')).toBeVisible();
  expect(screen.getByText('原任务恢复效果未知。')).toBeVisible();
  expect(screen.queryByText('原任务已恢复，可查看下方处理过程和判断依据。')).toBeNull();
  expect(screen.queryByRole('button', { name: /停止任务|确认完整方案|拒绝方案/ })).toBeNull();
});


it.each([true, false])('shows Owner permission instructions without continuing or claiming an unconfirmed notification: %s', async sent => {
  vi.mocked(sessionRecoveryApi.detail).mockResolvedValue({ ...task, status: 'completed', phase: 'assess', completionReason: 'mcp_permission_required', plan: null,
    judgment: { delivery: 'unconfirmed', response: 'unknown', effect: 'not_recovered', summary: '请在能力集中申请目标 MCP 服务权限，获批后自行继续原任务。', evidence_ids: ['evidence'] },
    timeline: [{ event_id: 'notice', at_ms: 1, kind: 'notification', actor: 'service', summary: sent ? '钉钉通知已发送' : '钉钉通知未确认', payload: { kind: 'result', sent } }],
  } as never);
  render(<SessionRecoveryDetail taskId="task" onBack={() => {}} />);
  await screen.findByRole('region', { name: 'Owner 处理指引' });
  expect(within(screen.getByRole('region', { name: 'Owner 处理指引' })).getByText(/请在能力集中申请目标 MCP 服务权限/)).toBeVisible();
  if (sent) expect(screen.getAllByText('已通知 Owner 自行处理').length).toBeGreaterThan(0);
  else expect(screen.queryByText('已通知 Owner 自行处理')).toBeNull();
  expect(screen.queryByRole('button', { name: /确认完整方案|拒绝方案|停止任务|查看方案/ })).toBeNull();
  expect(sessionRecoveryApi.decide).not.toHaveBeenCalled();
  expect(screen.queryByText('人工复核')).toBeNull();
});
