/** @jest-environment jsdom */
import { IssuePage } from '@/pages/TaskEscort/parity/evolution/IssuePage';
import { RepairSuggestions } from '@/pages/TaskEscort/parity/evolution/RepairSuggestions';
import { backendRequest } from '@/services/backendApi/httpClient';
import { evolutionService } from '@/services/taskEscortParity/evolutionService';
import '@testing-library/jest-dom';
import { act, fireEvent, render, screen, within } from '@testing-library/react';

jest.mock('@/services/backendApi/httpClient', () => ({ backendRequest: jest.fn() }));
jest.mock('@/services/taskEscortParity/evolutionService', () => ({
  ...jest.requireActual('@/services/taskEscortParity/evolutionService'),
  evolutionService: { access: jest.fn(), groups: jest.fn() },
}));

const base = '/openapi/v1/harnessflow/api/workflow-repairs';
const context = {
  scopeKey: 'viewer:wf',
  userId: 'viewer',
  workflowId: 'wf',
  ready: true,
  workflow: null,
  query: {},
  navigate: jest.fn(),
  refreshWorkflows: jest.fn(),
};
const activeSnapshot = {
  status: 'resolved',
  snapshot: {
    kind: 'deployment',
    workflowId: 'wf',
    workflowVersion: 2,
    deployNumber: 11,
    configDigest: 'config',
  },
};
const item = {
  workflowId: 'wf',
  itemId: 'original',
  groupKey: 'g',
  proposalKey: 'p',
  contentRevision: 1,
  previousItemId: null,
  proposal: { summary: '修复建议摘要' },
  instruction: '',
  sources: [],
  sourceAvailable: true,
  state: 'pending',
  stateVersion: 0,
  context: { sourceSnapshot: activeSnapshot },
};
const candidates = {
  schemaVersion: 'workflow-repair/v2',
  workflowId: 'wf',
  inputDigest: 'a'.repeat(64),
  items: [item],
  tasks: [],
  capabilities: { generation: true, diff: true, publication: true, reason: null },
  activeSnapshot,
  includeHistorical: false,
  facets: { nodes: [], failureModes: [] },
  counts: {},
  page: { page: 1, pageSize: 20, total: 1, totalPages: 1 },
  limits: { maxItems: 100, maxRequestBytes: 65536 },
  canEdit: false,
  canOptimize: true,
};
const post = jest.fn();
const request = backendRequest as jest.Mock;
function setup(response: unknown = candidates) {
  request.mockImplementation(async (url, options) => {
    if (url === `${base}/candidates` && options.method === 'GET') return response;
    if (url === base && options.method === 'POST') return post(options.data);
    throw new Error(`Unexpected request: ${options.method} ${url}`);
  });
}
function renderSuggestions() {
  return render(<RepairSuggestions context={context} includeHistorical={false} />);
}
function generate() {
  return screen.getByRole('button', { name: '生成优化任务' });
}
async function selectOriginal() {
  fireEvent.click(await screen.findByRole('checkbox', { name: '选择建议 original' }));
}

beforeEach(() => {
  jest.clearAllMocks();
  post.mockReset();
  setup();
  (evolutionService.access as jest.Mock).mockResolvedValue({ workflowId: 'wf', canView: true, canEdit: false });
  (evolutionService.groups as jest.Mock).mockResolvedValue({
    groups: [],
    facets: { nodes: [], modes: [] },
    page: { page: 1, pageSize: 10, total: 0, totalPages: 1 },
  });
});

it('lets a viewer generate from the existing Issue page using only the scoped manual POST', async () => {
  let accepted!: (value: unknown) => void;
  post.mockImplementation(
    () =>
      new Promise((resolve) => {
        accepted = resolve;
      }),
  );
  render(<IssuePage context={context} />);
  fireEvent.click(await screen.findByRole('button', { name: '查找全部建议' }));
  const dialog = await screen.findByRole('dialog', { name: '查找全部建议' });
  const checkbox = await within(dialog).findByRole('checkbox', { name: '选择建议 original' });
  expect(generate()).toBeDisabled();
  fireEvent.click(checkbox);
  fireEvent.change(within(dialog).getByRole('textbox', { name: '补充要求（可选）' }), {
    target: { value: '保留原有约束' },
  });
  fireEvent.click(generate());
  expect(within(dialog).getByRole('button', { name: '正在提交…' })).toBeDisabled();
  expect(within(dialog).getByRole('button', { name: '应用建议' })).toBeDisabled();
  expect(post).toHaveBeenCalledTimes(1);
  expect(request).toHaveBeenLastCalledWith(
    base,
    expect.objectContaining({
      method: 'POST',
      params: { user_id: 'viewer' },
      injectUserId: false,
      suppressErrorNotification: true,
      data: {
        workflowId: 'wf',
        itemIds: ['original'],
        inputDigest: candidates.inputDigest,
        instructions: '保留原有约束',
        includeHistorical: false,
        targetSnapshotKey: '["deployment","wf",2,11]',
        requestId: expect.any(String),
      },
    }),
  );
  await act(async () => accepted({ workflowId: 'wf', taskId: 'repair-1', revision: 1 }));
  expect(context.navigate).toHaveBeenCalledWith('issues', {
    repairTaskId: 'repair-1',
    issueSignature: undefined,
    analysisId: undefined,
    runId: undefined,
  });
});

it.each([undefined, false, 'true'])(
  'keeps reads and blocks generation for canOptimize=%s even with canEdit',
  async (canOptimize) => {
    setup({ ...candidates, canEdit: true, canOptimize });
    renderSuggestions();
    fireEvent.click(await screen.findByRole('checkbox', { name: '预览选择建议 original' }));
    expect(screen.getByText('仅本地只读选择预览，不会生成或应用。')).toBeInTheDocument();
    expect(generate()).toBeDisabled();
    expect(post).not.toHaveBeenCalled();
  },
);

it('reports unavailable providers and ineligible selections without submitting', async () => {
  setup({
    ...candidates,
    capabilities: { ...candidates.capabilities, generation: false, reason: '生成服务未配置' },
    items: [{ ...item, state: 'processing' }],
  });
  renderSuggestions();
  expect(await screen.findByText('生成服务未配置')).toBeInTheDocument();
  expect(screen.getByRole('checkbox', { name: '选择建议 original' })).toBeDisabled();
  expect(generate()).toBeDisabled();
  expect(post).not.toHaveBeenCalled();
});

it.each([
  [503, 'CAPABILITY_UNAVAILABLE', '无法读取当前生效版本的部署包'],
  [404, 'ITEM_NOT_FOUND', 'Selected source is unavailable in this workflow'],
  [409, 'STATE_CONFLICT', 'Repair attempt changed; refresh before submitting feedback'],
])('renders server %s inline and keeps the task view closed', async (status, code, error) => {
  post.mockRejectedValueOnce(Object.assign(new Error('request failed'), { status, data: { code, error } }));
  renderSuggestions();
  await selectOriginal();
  fireEvent.click(generate());
  expect(await screen.findByRole('alert')).toHaveTextContent(error);
  expect(context.navigate).not.toHaveBeenCalled();
});

it('keeps the original request id on an explicit retry of the same submission', async () => {
  post
    .mockRejectedValueOnce(new Error('Failed to fetch'))
    .mockResolvedValueOnce({ workflowId: 'wf', taskId: 'repair-1', revision: 1 });
  renderSuggestions();
  await selectOriginal();
  fireEvent.click(generate());
  await screen.findByRole('alert');
  fireEvent.click(generate());
  expect(post).toHaveBeenCalledTimes(2);
  expect(post.mock.calls[1][0]).toEqual(post.mock.calls[0][0]);
});

it('does not navigate after a late response when the suggestions scope has closed', async () => {
  let accepted!: (value: unknown) => void;
  post.mockImplementation(
    () =>
      new Promise((resolve) => {
        accepted = resolve;
      }),
  );
  const view = renderSuggestions();
  await selectOriginal();
  fireEvent.click(generate());
  const signal = request.mock.calls.at(-1)?.[1].signal;
  view.unmount();
  expect(signal.aborted).toBe(true);
  await act(async () => accepted({ workflowId: 'wf', taskId: 'repair-1', revision: 1 }));
  expect(context.navigate).not.toHaveBeenCalled();
});

it('uses the server originals for an exact fold and does not submit an unreviewed merge', async () => {
  const second = { ...item, itemId: 'second' };
  const exact = {
    ...item,
    aggregation: { status: 'ready', relation: 'exact', itemIds: ['original', 'second'], originals: [item, second] },
  };
  const merge = { ...second, aggregation: { ...exact.aggregation, relation: 'complementary' } };
  setup({ ...candidates, items: [exact, merge] });
  post.mockResolvedValueOnce({ workflowId: 'wf', taskId: 'repair-1', revision: 1 });
  renderSuggestions();
  await selectOriginal();
  expect(screen.getByText('选择此条建议包含 2 条等价原始建议。')).toBeInTheDocument();
  expect(screen.getByRole('checkbox', { name: '选择建议 second' })).toBeDisabled();
  await act(async () => fireEvent.click(generate()));
  expect(post).toHaveBeenCalledWith(expect.objectContaining({ itemIds: ['original', 'second'] }));
});

it('allows an original suggestion when only its proposal aggregation is unavailable', async () => {
  setup({
    ...candidates,
    items: [
      { ...item, aggregation: { status: 'unavailable', relation: 'exact', itemIds: [item.itemId], originals: [item] } },
    ],
  });
  renderSuggestions();
  await selectOriginal();
  expect(generate()).toBeEnabled();
});

it('keeps historical snapshots read-only even when the user can optimize', async () => {
  render(<RepairSuggestions context={context} versionScope="historical" snapshotKey="older" includeHistorical />);
  fireEvent.click(await screen.findByRole('checkbox', { name: '预览选择建议 original' }));
  expect(generate()).toBeDisabled();
  expect(screen.getByRole('button', { name: '应用建议' })).toBeDisabled();
  expect(post).not.toHaveBeenCalled();
});

it('requires a refreshed candidates read before resubmitting stale selections', async () => {
  post.mockRejectedValueOnce(Object.assign(new Error('items changed'), { status: 409 }));
  renderSuggestions();
  await selectOriginal();
  fireEvent.click(generate());
  expect(await screen.findByRole('alert')).toHaveTextContent('items changed');
  expect(generate()).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: '重试读取' }));
  expect(await screen.findByRole('checkbox', { name: '选择建议 original' })).not.toBeChecked();
  expect(request.mock.calls.filter(([, options]) => options.method === 'GET')).toHaveLength(2);
  expect(post).toHaveBeenCalledTimes(1);
});

it('does not open a task when the create response has a different workflow', async () => {
  post.mockResolvedValueOnce({ workflowId: 'other', taskId: 'repair-1', revision: 1 });
  renderSuggestions();
  await selectOriginal();
  fireEvent.click(generate());
  expect(await screen.findByRole('alert')).toHaveTextContent('无法确认生成结果');
  expect(context.navigate).not.toHaveBeenCalled();
});
