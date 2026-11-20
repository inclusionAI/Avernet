/** @jest-environment jsdom */
import { RepairTaskWorkspace } from '@/pages/TaskEscort/parity/evolution/RepairTaskWorkspace';
import { backendRequest, BackendRequestError } from '@/services/backendApi/httpClient';
import { repairService } from '@/services/taskEscortParity/repairService';
import '@testing-library/jest-dom';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { createHash, webcrypto } from 'node:crypto';
import { TextEncoder } from 'node:util';
jest.mock('@/services/backendApi/httpClient', () => ({
  ...jest.requireActual('@/services/backendApi/httpClient'),
  backendRequest: jest.fn(),
}));
jest.mock('@/services/taskEscortParity/repairService', () => ({
  repairService: { task: jest.fn(), diff: jest.fn(), material: jest.fn(), trials: jest.fn(), verification: jest.fn() },
}));
const context = {
  scopeKey: 'u:wf',
  ready: true,
  userId: 'u',
  workflowId: 'wf',
  workflow: null,
  query: { repairTaskId: 't' },
  navigate: jest.fn(),
  refreshWorkflows: jest.fn(),
};
const item = {
  itemId: 'i',
  groupKey: 'g',
  proposalKey: 'p',
  contentRevision: 1,
  previousItemId: null,
  proposal: { summary: '真实优化目标' },
  instruction: 'frozen',
  sources: [{ kind: 'suggestion', suggestionId: 's' }],
};
const commit = 'c'.repeat(40);
const packDigest = 'd'.repeat(64);
const revision = {
  workflowId: 'wf',
  taskId: 't',
  revision: 2,
  phase: 'review',
  stateVersion: 1,
  input: { items: [item], baseline: { packCommit: 'base' }, materials: { recordId: 'mat' } },
  draft: { candidateCommit: commit, packDigest },
  checks: { static: { status: 'passed' }, uncoveredChecks: ['business'] },
  error: null,
  createdAtMs: 1,
  updatedAtMs: 1,
};
const task = {
  workflowId: 'wf',
  taskId: 't',
  latestAttempt: revision,
  latestSuccessful: revision,
  revisions: [revision],
  capabilities: { generation: true, diff: true, publication: true, reason: null },
  release: { approval: null, deployments: [] },
  revisionPage: { page: 1, pageSize: 20, total: 21, totalPages: 2 },
};
const diff = {
  baseCommit: 'base',
  candidateCommit: commit,
  files: [
    {
      path: 'flow.yaml',
      status: 'modified',
      before: 'a',
      after: 'b',
      binary: false,
      truncated: false,
      patch: '@@ -1 +1 @@\n-a\n+b',
    },
  ],
  truncated: false,
};
const base = '/openapi/v1/harnessflow/api/workflow-repairs/t';
const target = { id: 'server-selected-target', scope: 'workflow', label: '工单工作流', environment: 'pre' };
const deployment = {
  id: 'deployment-1',
  workflowId: 'wf',
  taskId: 't',
  revision: 2,
  candidateCommit: commit,
  packDigest,
  target,
  status: 'running',
  requestId: 'existing-deployment-request',
  receipt: null,
  error: null,
};
const request = jest.mocked(backendRequest);
const posts = () => request.mock.calls.filter(([, options]) => options?.method === 'POST');
async function enterChanges(canEdit = false) {
  const view = render(<RepairTaskWorkspace context={context} taskId="t" canEdit={canEdit} />);
  fireEvent.click(await screen.findByRole('tab', { name: '改动与发布' }));
  await screen.findByText('flow.yaml');
  return view;
}
async function openReview() {
  fireEvent.click(screen.getByRole('button', { name: '发布此版本' }));
  return screen.findByRole('dialog', { name: '确认发布 v2' });
}

beforeAll(() => {
  Object.defineProperty(globalThis, 'crypto', { configurable: true, value: webcrypto });
  Object.defineProperty(globalThis, 'TextEncoder', { configurable: true, value: TextEncoder });
});
beforeEach(() => {
  jest.clearAllMocks();
  sessionStorage.clear();
  request.mockReset();
  request.mockImplementation(async (url, options) => {
    if (url === `${base}/deployment-targets` && options?.method === 'GET') return { targets: [target] };
    if (url === `${base}/publish` && options?.method === 'POST') {
      const { requestId } = options.data as { requestId: string };
      return {
        ...deployment,
        requestId: createHash('sha256')
          .update(JSON.stringify(['publish', requestId]))
          .digest('hex'),
      };
    }
    if (url === `${base}/deployment/${deployment.id}/reconcile` && options?.method === 'POST') return deployment;
    throw new Error(`Unexpected request: ${options?.method} ${url}`);
  });
  jest.mocked(repairService.task).mockResolvedValue(task);
  jest.mocked(repairService.trials).mockResolvedValue({ configured: false, trials: [], nextOffset: null });
  jest.mocked(repairService.diff).mockResolvedValue(diff);
});
it('preserves exact tabs and history and reads real patches and checks without automatic actions', async () => {
  render(<RepairTaskWorkspace context={context} taskId="t" canEdit />);
  expect(await screen.findByRole('tab', { name: '优化目标' })).toBeInTheDocument();
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('tab', { name: '改动与发布' }));
  expect(await screen.findByText('flow.yaml')).toBeInTheDocument();
  expect(await screen.findByText(/@@ -1/)).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '发布此版本' })).toBeEnabled();
  fireEvent.click(screen.getByRole('tab', { name: '检查详情' }));
  expect(screen.getByText(/uncoveredChecks/)).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '发布此版本' })).toBeDisabled();
  expect(request).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: '下一页版本历史' }));
  await waitFor(() => expect(repairService.task).toHaveBeenLastCalledWith('wf', 't', 'u', 2, expect.any(AbortSignal)));
});
it('reads revision frozen material only and preserves 404, no live original fallback', async () => {
  (repairService.material as jest.Mock).mockRejectedValue(new Error('404：冻结材料不存在'));
  render(<RepairTaskWorkspace context={context} taskId="t" canEdit={false} />);
  fireEvent.click(await screen.findByRole('button', { name: '读取冻结材料 i' }));
  expect(await screen.findByText('404：冻结材料不存在')).toBeInTheDocument();
  expect(repairService.material).toHaveBeenCalledWith('t', 2, 'i', 'u', expect.any(AbortSignal));
});
it('does not read effect or claim success without a successful release receipt', async () => {
  render(<RepairTaskWorkspace context={context} taskId="t" canEdit />);
  fireEvent.click(await screen.findByRole('tab', { name: '发布后效果' }));
  expect(await screen.findByText(/暂无成功发布回执/)).toBeInTheDocument();
  expect(repairService.verification).not.toHaveBeenCalled();
});
it('does not read diff when server capability is false', async () => {
  const d = await (repairService.task as jest.Mock)();
  (repairService.task as jest.Mock).mockResolvedValue({
    ...d,
    capabilities: { generation: false, diff: false, publication: false, reason: '未配置' },
  });
  render(<RepairTaskWorkspace context={context} taskId="t" canEdit={false} />);
  fireEvent.click(await screen.findByRole('tab', { name: '改动与发布' }));
  expect(await screen.findByText(/差异能力不可用/)).toBeInTheDocument();
  expect(repairService.diff).not.toHaveBeenCalled();
});

it('uses an authorized API target and explicit acknowledgment to publish the exact reviewed version once', async () => {
  await enterChanges(false); // Manual publication uses the task capability, not the Editor grant.
  expect(request).not.toHaveBeenCalled();
  const dialog = within(await openReview());
  expect(await dialog.findByText('工单工作流 · pre')).toBeInTheDocument();
  expect(request).toHaveBeenLastCalledWith(`${base}/deployment-targets`, {
    method: 'GET',
    params: { user_id: 'u' },
    injectUserId: false,
    retryOnTransient: false,
    suppressErrorNotification: true,
    signal: expect.any(AbortSignal),
  });
  expect(dialog.getByRole('button', { name: '确认发布' })).toBeDisabled();
  expect(posts()).toHaveLength(0);
  fireEvent.click(dialog.getByRole('checkbox', { name: /接受未覆盖检查的风险/ }));
  const confirm = dialog.getByRole('button', { name: '确认发布' });
  jest
    .mocked(repairService.task)
    .mockResolvedValue({ ...task, release: { approval: null, deployments: [deployment] } });
  fireEvent.click(confirm);
  fireEvent.click(confirm);
  await waitFor(() => expect(posts()).toHaveLength(1));
  expect(posts()).toEqual([
    [
      `${base}/publish`,
      {
        method: 'POST',
        params: { user_id: 'u' },
        injectUserId: false,
        retryOnTransient: false,
        suppressErrorNotification: true,
        data: {
          revision: 2,
          expectedStateVersion: 1,
          targetId: target.id,
          requestId: expect.any(String),
          acknowledgeUncoveredChecks: true,
        },
      },
    ],
  ]);
  expect(await screen.findByText(/已有发布记录，结果未确认前不可再次发布/)).toBeInTheDocument();
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '发布此版本' })).not.toBeInTheDocument();
  expect(posts()).toHaveLength(1);
});

it('reopens after a lost response and retries the entire frozen request even after the approval bumped stateVersion', async () => {
  request.mockImplementationOnce(async () => ({ targets: [target] })).mockRejectedValueOnce(new Error('response lost'));
  const first = await enterChanges();
  const dialog = within(await openReview());
  await dialog.findByText('工单工作流 · pre');
  fireEvent.click(dialog.getByRole('checkbox'));
  fireEvent.click(dialog.getByRole('button', { name: '确认发布' }));
  expect(await dialog.findByRole('alert')).toHaveTextContent('未能确认发布结果');
  expect(posts()).toHaveLength(1);
  first.unmount();
  const bumped = { ...revision, stateVersion: 2 };
  jest
    .mocked(repairService.task)
    .mockResolvedValue({ ...task, latestAttempt: bumped, latestSuccessful: bumped, revisions: [bumped] });
  await enterChanges();
  const retry = within(await openReview());
  await retry.findByText('工单工作流 · pre');
  expect(posts()).toHaveLength(1);
  expect(retry.getByRole('button', { name: '重试原发布请求' })).toBeDisabled();
  fireEvent.click(retry.getByRole('checkbox'));
  fireEvent.click(retry.getByRole('button', { name: '重试原发布请求' }));
  await waitFor(() => expect(retry.queryByRole('button', { name: '重试原发布请求' })).not.toBeInTheDocument());
  expect(posts()).toHaveLength(2);
  expect(posts()[1]).toEqual(posts()[0]);
});

it('cannot substitute a new target when reopening an uncertain publish', async () => {
  request.mockImplementationOnce(async () => ({ targets: [target] })).mockRejectedValueOnce(new Error('response lost'));
  await enterChanges();
  const first = within(await openReview());
  await first.findByText('工单工作流 · pre');
  fireEvent.click(first.getByRole('checkbox'));
  fireEvent.click(first.getByRole('button', { name: '确认发布' }));
  await first.findByRole('alert');
  fireEvent.click(first.getByRole('button', { name: '返回审阅' }));
  request.mockImplementationOnce(async () => ({ targets: [{ ...target, id: 'different' }] }));
  const retry = within(await openReview());
  expect(await retry.findByRole('alert')).toHaveTextContent('原发布范围已变化');
  expect(retry.getByRole('button', { name: '重试原发布请求' })).toBeDisabled();
  expect(posts()).toHaveLength(1);
});

it.each([[{ ...target, scope: undefined }], [target, { ...target, id: 'other' }], [{ ...target, id: '' }]])(
  'fails closed on missing, ambiguous or malformed authorized workflow targets (%j)',
  async (...targets) => {
    request.mockImplementationOnce(async () => ({ targets }));
    await enterChanges();
    const dialog = within(await openReview());
    expect(await dialog.findByRole('alert')).toHaveTextContent('尚未提交新的发布请求');
    expect(dialog.getByRole('button', { name: '确认发布' })).toBeDisabled();
    expect(posts()).toHaveLength(0);
  },
);

it('does not offer publication for a historical selection, while drafting, or without a complete baseline diff', async () => {
  const prior = { ...revision, revision: 1 };
  jest.mocked(repairService.task).mockResolvedValue({ ...task, revisions: [revision, prior] });
  const first = await enterChanges(true);
  fireEvent.click(screen.getByRole('button', { name: '选择版本 v1' }));
  expect(screen.getByRole('button', { name: '发布此版本' })).toBeDisabled();
  first.unmount();
  jest.mocked(repairService.task).mockResolvedValue({
    ...task,
    latestAttempt: { ...revision, revision: 3, phase: 'drafting' },
    revisions: [revision],
  });
  const second = await enterChanges(true);
  fireEvent.click(screen.getByRole('button', { name: '选择版本 v2' }));
  expect(screen.getByRole('button', { name: '发布此版本' })).toBeDisabled();
  second.unmount();
  jest.mocked(repairService.task).mockResolvedValue(task);
  jest.mocked(repairService.diff).mockResolvedValue({ ...diff, truncated: true });
  await enterChanges(true);
  expect(screen.getByRole('button', { name: '发布此版本' })).toBeDisabled();
  expect(request).not.toHaveBeenCalled();
});

it('reconciles only the existing workflow deployment on an explicit click, without publishing or starting a trial', async () => {
  jest
    .mocked(repairService.task)
    .mockResolvedValue({ ...task, release: { approval: null, deployments: [deployment] } });
  jest.mocked(repairService.trials).mockResolvedValue({ configured: true, trials: [], nextOffset: null });
  await enterChanges();
  expect(request).not.toHaveBeenCalled();
  expect(screen.queryByRole('button', { name: '发布此版本' })).not.toBeInTheDocument();
  await act(async () => fireEvent.click(screen.getByRole('button', { name: '核对发布状态' })));
  expect(posts()).toEqual([[`${base}/deployment/deployment-1/reconcile`, expect.objectContaining({ data: {} })]]);
});

it('shows server authorization rejection inline and makes no automatic retry', async () => {
  request
    .mockImplementationOnce(async () => ({ targets: [target] }))
    .mockRejectedValueOnce(new BackendRequestError('denied', { apiPath: `${base}/publish`, status: 403 }));
  await enterChanges(true);
  const dialog = within(await openReview());
  await dialog.findByText('工单工作流 · pre');
  fireEvent.click(dialog.getByRole('checkbox'));
  fireEvent.click(dialog.getByRole('button', { name: '确认发布' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('服务器拒绝发布（HTTP 403）');
  expect(posts()).toHaveLength(1);
});

it('drops the old target response when the workflow principal changes', async () => {
  let resolveTarget!: (value: unknown) => void;
  request.mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        resolveTarget = resolve;
      }),
  );
  const view = await enterChanges();
  const dialog = within(await openReview());
  expect(await dialog.findByRole('status')).toHaveTextContent('正在核对');
  const signal = request.mock.calls[0][1]?.signal;
  view.rerender(
    <RepairTaskWorkspace context={{ ...context, userId: 'other', scopeKey: 'other:wf' }} taskId="t" canEdit={false} />,
  );
  expect(signal?.aborted).toBe(true);
  await act(async () => resolveTarget({ targets: [target] }));
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(posts()).toHaveLength(0);
});

it.each(['target', 'environment', 'requestId', 'receiptRequestId'])(
  'does not treat a mismatched deployment %s as confirmation',
  async (mismatch) => {
    request
      .mockImplementationOnce(async () => ({ targets: [target] }))
      .mockImplementationOnce(async (_url, options) => {
        const { requestId } = options?.data as { requestId: string };
        const expected = createHash('sha256')
          .update(JSON.stringify(['publish', requestId]))
          .digest('hex');
        const returnedId = mismatch === 'requestId' ? 'f'.repeat(64) : expected;
        return {
          ...deployment,
          status: 'succeeded',
          requestId: returnedId,
          target: {
            ...target,
            id: mismatch === 'target' ? 'wrong-target' : target.id,
            environment: mismatch === 'environment' ? 'prod' : target.environment,
          },
          receipt: {
            requestId: mismatch === 'receiptRequestId' ? '0'.repeat(64) : returnedId,
            candidateCommit: commit,
            packDigest,
            targetId: target.id,
            deployNumber: 7,
          },
        };
      });
    await enterChanges();
    const dialog = within(await openReview());
    await dialog.findByText('工单工作流 · pre');
    fireEvent.click(dialog.getByRole('checkbox'));
    fireEvent.click(dialog.getByRole('button', { name: '确认发布' }));
    expect(await dialog.findByRole('alert')).toHaveTextContent('未能确认发布结果');
    expect(dialog.getByRole('button', { name: '重试原发布请求' })).toBeDisabled();
    expect(repairService.task).toHaveBeenCalledTimes(1);
    expect(posts()).toHaveLength(1);
  },
);
