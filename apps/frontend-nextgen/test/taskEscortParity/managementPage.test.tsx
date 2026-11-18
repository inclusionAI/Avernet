/** @jest-environment jsdom */
import type { TaskEscortParityContext } from '@/domain/taskEscortParity/types';
import { ManagementPage } from '@/pages/TaskEscort/parity/management/ManagementPage';
import { managementService } from '@/services/taskEscortParity/managementService';
import '@testing-library/jest-dom';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';

jest.mock('@/services/taskEscortParity/managementService', () => ({
  managementService: Object.fromEntries(
    [
      'access',
      'history',
      'version',
      'deploy',
      'permissions',
      'facades',
      'notification',
      'callbacks',
      'policy',
      'status',
      'records',
    ].map((key) => [key, jest.fn()]),
  ),
}));
const service = jest.mocked(managementService);
beforeAll(() => {
  HTMLElement.prototype.scrollIntoView = jest.fn();
});
const context: TaskEscortParityContext = {
  scopeKey: 'space:user',
  ready: true,
  userId: 'actor',
  workflowId: 'wf',
  workflow: { id: 'wf', title: 'Workflow', runCount: 1, lastStatus: 'done', ownerId: null },
  query: {},
  navigate: jest.fn(),
  refreshWorkflows: jest.fn(),
};
const history = Array.from({ length: 7 }, (_, i) => ({
  version: 7 - i,
  deployNumber: 10 - i,
  tagName: null,
  action: 'deploy',
  fromDeployNumber: null,
  note: `release-${i}`,
  botId: null,
  ownerId: null,
  isActive: i === 1,
  createdAtMs: 1794873600000,
  source: null,
}));
const job = {
  id: 'job-1',
  kind: 'auto_run',
  status: 'done',
  attempts: 2,
  reason: 'PARTIAL_ANALYSIS',
  createdAtMs: 1794873600000,
  flowId: 'run/1',
  analysisId: 'analysis/1',
  taskId: null,
  complete: false,
};
beforeEach(() => {
  jest.clearAllMocks();
  service.access.mockResolvedValue({ canView: true, canEdit: true });
  service.history.mockResolvedValue({ history, active: history[1] });
  service.permissions.mockResolvedValue([
    { id: 1, botOwnerId: '*', botId: '*', canView: true, canExecute: false, canEdit: false },
  ]);
  service.facades.mockResolvedValue([{ workflowId: 'wf', command: '/run', packId: 'pack', remark: 'Run workflow' }]);
  service.notification.mockResolvedValue({
    workflowId: 'wf',
    robotCode: 'robot',
    hasSecret: true,
    onFailureUsers: [{ userId: 'u', name: 'User' }],
    onFailureGroups: [{ openConversationId: 'g', name: 'Group' }],
    onFailureMessageTitle: 'Failed',
    onFailureMessageIncludeRunLink: true,
    notifyOn: ['node_failed'],
    eventOptions: {
      node_failed: { title: 'Node error', includeRunLink: true, includeNodeOutput: false, maxOutputFields: 3 },
    },
  });
  service.callbacks.mockResolvedValue([
    {
      configId: 'cb',
      workflowId: 'wf',
      name: 'Callback A',
      url: 'https://example.test/hook',
      hasSecret: true,
      enabled: true,
      notifyOn: ['workflow_failed'],
      timeoutMs: 5000,
      maxRetries: 2,
      retryDelayMs: 1000,
      includeNodeOutput: true,
      createdAtMs: 1794873600000,
      modifiedAtMs: 1794873600000,
    },
  ]);
  service.policy.mockResolvedValue({
    workflowId: 'wf',
    enabled: false,
    autoDraft: true,
    dailyTime: '10:30',
    timezone: 'Asia/Shanghai',
    executionEnvironment: 'pre',
    minimumImpact: 'high',
    notificationRecipients: ['recipient'],
    notificationVersion: 2,
    version: 4,
    updatedAtMs: 1794873600000,
    dailyConfigured: true,
    source: 'database',
  });
  service.status.mockResolvedValue({
    workflowId: 'wf',
    lastScannedAtMs: 1794873600000,
    pauseReason: 'OWNER_MISSING',
    coverage: { total: 10, pending: 2, failed: 1, partial: 3, unknown: 4 },
    jobs: [job],
    retryableJobs: [job],
    round: null,
  });
  service.records.mockResolvedValue({ jobs: [job], nextCursor: 'next-page' });
  service.version.mockResolvedValue({
    workflowId: 'wf',
    version: 7,
    deployNumber: 10,
    tagName: null,
    action: 'deploy',
    note: 'Snapshot',
    createdAtMs: 1794873600000,
    specJson: '{"nodes":[]}',
  });
  service.deploy.mockResolvedValue({
    workflowId: 'wf',
    version: 7,
    deployNumber: 10,
    tagName: null,
    action: 'deploy',
    note: 'Deploy snapshot',
    createdAtMs: 1794873600000,
    specJson: '{"nodes":[]}',
  });
});

async function settings(label: string) {
  await screen.findByRole('group', { name: '工作流设置页签' });
  fireEvent.click(screen.getByRole('button', { name: label }));
}

it('waits for explicit EDIT, hides settings/advanced for viewers and never requests privileged configs', async () => {
  let resolve!: (value: { canView: boolean; canEdit: boolean }) => void;
  service.access.mockReturnValue(
    new Promise((yes) => {
      resolve = yes;
    }),
  );
  render(<ManagementPage context={context} />);
  expect(screen.queryByText('工作流设置')).not.toBeInTheDocument();
  expect(service.permissions).not.toHaveBeenCalled();
  await act(async () => resolve({ canView: true, canEdit: false }));
  await screen.findByText('release-0');
  expect(screen.queryByText('工作流设置')).not.toBeInTheDocument();
  expect(screen.queryByText('高级操作')).not.toBeInTheDocument();
  for (const method of ['permissions', 'facades', 'notification', 'callbacks', 'policy', 'status', 'records'] as const)
    expect(service[method]).not.toHaveBeenCalled();
});

it('shows five history rows, truthful active version, expands and reads both snapshot routes with focus restored', async () => {
  render(<ManagementPage context={context} />);
  await screen.findByText('release-0');
  expect(screen.queryByText('release-5')).not.toBeInTheDocument();
  expect(screen.getByText('当前').closest('li')).toHaveTextContent('deploy #9');
  fireEvent.click(screen.getByRole('button', { name: '展开其余 2 个版本' }));
  expect(screen.getByText('release-6')).toBeInTheDocument();
  const trigger = screen.getAllByRole('button', { name: '版本快照' })[0];
  trigger.focus();
  fireEvent.click(trigger);
  const dialog = await screen.findByRole('dialog');
  await within(dialog).findByText('Snapshot');
  expect(service.version).toHaveBeenCalledWith('wf', 'actor', 7, expect.any(AbortSignal));
  fireEvent.click(within(dialog).getByRole('button', { name: '关闭弹窗' }));
  await waitFor(() => expect(trigger).toHaveFocus());
  fireEvent.click(screen.getAllByRole('button', { name: '部署快照' })[0]);
  await screen.findByText('Deploy snapshot');
  expect(service.deploy).toHaveBeenCalledWith('wf', 'actor', 10, expect.any(AbortSignal));
});

it('renders complete permission and command panels with disabled writes and collapsed delete', async () => {
  render(<ManagementPage context={context} />);
  await screen.findByRole('heading', { name: 'Bot 权限' });
  expect(await screen.findByText('所有人')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '添加权限' })).toBeDisabled();
  const advanced = screen.getByText('高级操作').closest('details');
  expect(advanced).not.toHaveAttribute('open');
  await settings('命令');
  await screen.findByText('/run');
  expect(screen.getByText('Run workflow')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '绑定命令' })).toBeDisabled();
  expect(screen.getAllByText('未接入写入契约').length).toBeGreaterThan(0);
});

it('renders notification and callback details without putting any secret into DOM or an editable field', async () => {
  render(<ManagementPage context={context} />);
  await settings('通知');
  await screen.findByText('robot');
  expect(screen.getByLabelText('appSecret')).toHaveAttribute('type', 'password');
  expect(screen.getByLabelText('appSecret')).toHaveValue('');
  expect(screen.getByText('Node error')).toBeInTheDocument();
  expect(screen.getByText('User · u')).toBeInTheDocument();
  expect(screen.getByText('Group · g')).toBeInTheDocument();
  await settings('回调');
  await screen.findByText('Callback A');
  fireEvent.click(screen.getByText('回调详情'));
  expect(screen.getByText('5000 ms')).toBeInTheDocument();
  expect(screen.getByLabelText('回调签名密钥')).toHaveValue('');
  expect(screen.getByText(/掩码不可作为密钥复用/)).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '保存回调' })).toBeDisabled();
});

it('has nested analysis/optimization/notification history, details, real cursor and period/status filters', async () => {
  render(<ManagementPage context={context} />);
  await settings('自动化设置');
  await screen.findByText('10:30');
  expect(screen.getByText('数据库')).toBeInTheDocument();
  expect(screen.getByText('预发')).toBeInTheDocument();
  expect(service.records).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: '查看自动分析记录' }));
  const analysisRecords = await screen.findByRole('region', { name: '自动分析记录' });
  await within(analysisRecords).findByRole('link', { name: '查看分析' });
  expect(service.records).toHaveBeenLastCalledWith(
    'wf',
    'actor',
    { kind: 'analysis', period: 'all', status: 'all', limit: 20 },
    expect.any(AbortSignal),
  );
  expect(within(analysisRecords).getByRole('link', { name: '查看分析' })).toHaveAttribute(
    'href',
    expect.stringContaining('runId=run%2F1'),
  );
  expect(screen.getAllByText('分析结束，证据不完整').length).toBeGreaterThan(0);
  fireEvent.click(screen.getByRole('button', { name: '下一页' }));
  await waitFor(() =>
    expect(service.records).toHaveBeenLastCalledWith(
      'wf',
      'actor',
      expect.objectContaining({ cursor: 'next-page' }),
      expect.any(AbortSignal),
    ),
  );
  fireEvent.click(await screen.findByRole('button', { name: '上一页' }));
  await waitFor(() =>
    expect(service.records).toHaveBeenLastCalledWith(
      'wf',
      'actor',
      expect.not.objectContaining({ cursor: expect.anything() }),
      expect.any(AbortSignal),
    ),
  );
  fireEvent.keyDown(screen.getByRole('combobox', { name: '执行记录时间范围' }), { key: 'ArrowDown' });
  fireEvent.click(await screen.findByRole('option', { name: '近 7 天' }));
  await waitFor(() =>
    expect(service.records).toHaveBeenLastCalledWith(
      'wf',
      'actor',
      { kind: 'analysis', period: '7d', status: 'all', limit: 20 },
      expect.any(AbortSignal),
    ),
  );
  fireEvent.keyDown(screen.getByRole('combobox', { name: '执行记录状态' }), { key: 'ArrowDown' });
  fireEvent.click(await screen.findByRole('option', { name: '需要处理' }));
  await waitFor(() =>
    expect(service.records).toHaveBeenLastCalledWith(
      'wf',
      'actor',
      { kind: 'analysis', period: '7d', status: 'blocked', limit: 20 },
      expect.any(AbortSignal),
    ),
  );
  fireEvent.click(screen.getByRole('button', { name: '查看自动优化记录' }));
  await waitFor(() =>
    expect(service.records).toHaveBeenLastCalledWith(
      'wf',
      'actor',
      expect.objectContaining({ kind: 'optimization', limit: 20 }),
      expect.any(AbortSignal),
    ),
  );
  fireEvent.click(screen.getByRole('button', { name: '查看通知异常' }));
  await waitFor(() =>
    expect(service.records).toHaveBeenLastCalledWith(
      'wf',
      'actor',
      expect.objectContaining({ kind: 'notification', status: 'attention', limit: 8 }),
      expect.any(AbortSignal),
    ),
  );
});

it.each([false, true])(
  'fails closed for no VIEW / access 403=%s and never loads history or settings',
  async (denied) => {
    if (denied) service.access.mockRejectedValue(new Error('403：无权查看'));
    else service.access.mockResolvedValue({ canView: false, canEdit: true });
    render(<ManagementPage context={context} />);
    await screen.findByText(denied ? '403：无权查看' : '当前账号无权查看此工作流');
    expect(service.history).not.toHaveBeenCalled();
    expect(service.permissions).not.toHaveBeenCalled();
  },
);

it('renders empty/error/retry instead of a generic unavailable and clears privileged evidence on identity change', async () => {
  service.history.mockResolvedValue({ history: [], active: null });
  service.permissions.mockRejectedValueOnce(new Error('读取失败，请重试')).mockResolvedValue([]);
  const { rerender } = render(<ManagementPage context={context} />);
  await screen.findByText('暂无版本历史');
  await screen.findByText('读取失败，请重试');
  fireEvent.click(screen.getByRole('button', { name: '重试读取' }));
  await screen.findByText('暂无 Bot 权限');
  await settings('通知');
  await screen.findByText('robot');
  service.access.mockReturnValue(new Promise(() => {}));
  rerender(<ManagementPage context={{ ...context, userId: 'new-user' }} />);
  expect(screen.queryByText('robot')).not.toBeInTheDocument();
  expect(screen.queryByText('工作流设置')).not.toBeInTheDocument();
});

it('keeps true empty configs and empty automation records, and never enables any write control', async () => {
  service.notification.mockResolvedValue(null);
  service.callbacks.mockResolvedValue([]);
  service.facades.mockResolvedValue([]);
  service.records.mockResolvedValue({ jobs: [], nextCursor: null });
  const { rerender } = render(<ManagementPage context={{ ...context, userId: undefined }} />);
  expect(service.access).not.toHaveBeenCalled();
  rerender(<ManagementPage context={context} />);
  await settings('通知');
  await screen.findByText('尚未配置工作流通知');
  expect(screen.queryByLabelText('appSecret')).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: '保存通知配置' })).toBeDisabled();
  await settings('回调');
  await screen.findByText('暂无 HTTP 回调配置');
  await settings('命令');
  await screen.findByText('暂无命令绑定');
  await settings('自动化设置');
  await screen.findByText('10:30');
  fireEvent.click(screen.getByRole('button', { name: '查看通知异常' }));
  await screen.findByText('暂无通知发送异常');
  expect(screen.getByRole('button', { name: '下一页' })).toBeDisabled();
  expect(screen.getByRole('button', { name: '上一页' })).toBeDisabled();
  expect(screen.getByRole('switch', { name: '自动分析' })).toBeDisabled();
  expect(screen.getByRole('switch', { name: '自动分析' })).toHaveAttribute('aria-checked', 'false');
  fireEvent.click(screen.getByRole('button', { name: '收起记录' }));
  expect(screen.queryByRole('region', { name: '通知发送异常' })).not.toBeInTheDocument();
});
