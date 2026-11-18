/** @jest-environment jsdom */
import type { TaskEscortParityContext } from '@/domain/taskEscortParity/types';
import { WorkflowEditorPage } from '@/pages/TaskEscort/parity/editor/WorkflowEditorPage';
import { backendRequest } from '@/services/backendApi/httpClient';
import '@testing-library/jest-dom';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';

jest.mock('@/services/backendApi/httpClient', () => ({ backendRequest: jest.fn() }));
const request = backendRequest as jest.Mock;
const spec = {
  id: 'wf',
  title: '真实护航',
  version: 6,
  input: { schema: { real: 'string' } },
  extension: { keep: true },
  nodes: [
    {
      id: 'a',
      title: '准备数据',
      executor: { type: 'cli-script', command: 'scripts/a.sh', timeoutMs: 2500 },
      retry: { maxAttempts: 2 },
      vendorExtension: { keep: false },
    },
    { id: 'b', title: '结果审核', dependsOn: ['a'], executor: { type: 'approval' } },
  ],
};
const releases = [7, 6].map((deployNumber) => ({
  deployNumber,
  version: 3,
  action: 'deploy',
  isActive: deployNumber === 6,
  gmtCreate: 1794902400,
  note: `发布-${deployNumber}`,
}));
const context: TaskEscortParityContext = {
  scopeKey: 's1',
  ready: true,
  userId: 'alice',
  workflowId: 'wf',
  workflow: { id: 'wf', title: '真实护航', runCount: null, lastStatus: null, ownerId: null },
  query: {},
  navigate: jest.fn(),
  refreshWorkflows: jest.fn(),
};
function respond(url: string, options: { params: Record<string, unknown> }) {
  if (url.endsWith('/access')) return Promise.resolve({ workflowId: 'wf', canView: true, canEdit: true });
  if (url.endsWith('/history')) return Promise.resolve({ workflowId: 'wf', history: releases, active: releases[1] });
  if (url.includes('/history/by-deploy/'))
    return Promise.resolve({
      workflowId: 'wf',
      ...releases[0],
      deployNumber: Number(url.split('/').pop()),
      specJson: JSON.stringify(spec),
    });
  if (url.endsWith('/history/diff'))
    return Promise.resolve({
      workflowId: 'wf',
      from: { ...releases[0], deployNumber: options.params.fromDeploy, specJson: '{"title":"旧标题"}' },
      to: { ...releases[0], deployNumber: options.params.toDeploy, specJson: '{"title":"新标题"}' },
    });
  if (url.endsWith('/pack-scripts'))
    return Promise.resolve({
      workflowId: 'wf',
      packId: 'real-pack',
      baseRef: 'origin/wf/wf',
      baseCommit: 'a'.repeat(40),
      files: [
        { path: 'scripts/a.sh', content: 'echo actual-content', mode: '100755' },
        { path: 'scripts/b.py', content: 'print("real-python")', mode: '100644' },
      ],
    });
  return Promise.resolve(spec);
}
beforeEach(() => {
  request.mockReset().mockImplementation(respond);
  jest.mocked(context.navigate).mockClear();
});

it('renders real graph, node selections and preserved advanced/raw fields; all mutations are disabled', async () => {
  render(<WorkflowEditorPage context={context} />);
  fireEvent.click(await screen.findByRole('button', { name: /准备数据.*cli-script/ }));
  const inspector = screen.getByRole('complementary', { name: '节点属性' });
  expect(within(inspector).getByText('scripts/a.sh')).toBeInTheDocument();
  fireEvent.click(within(inspector).getByText('高级配置'));
  expect(inspector).toHaveTextContent('timeoutMs');
  expect(inspector).toHaveTextContent('2500');
  fireEvent.click(within(inspector).getByText('完整节点 JSON'));
  expect(inspector).toHaveTextContent('vendorExtension');
  expect(context.navigate).toHaveBeenCalledWith('editor', expect.objectContaining({ nodeId: 'a' }));
  fireEvent.click(screen.getByRole('button', { name: /结果审核.*approval/ }));
  expect(inspector).toHaveTextContent('结果审核');
  for (const name of ['保存工作流', '发布工作流', '添加节点'])
    expect(screen.getByRole('button', { name })).toBeDisabled();
  expect(screen.getByText(/未接入.*写入/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'YAML' }));
  expect(screen.getByLabelText('工作流 YAML')).toHaveTextContent('vendorExtension:');
  fireEvent.click(screen.getByRole('button', { name: '对照' }));
  expect(screen.getByLabelText('工作流画布')).toBeInTheDocument();
  expect(screen.getByLabelText('工作流 YAML')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '流程设置' }));
  const dialog = screen.getByRole('dialog', { name: '流程设置' });
  expect(dialog).toHaveTextContent('schema');
  expect(dialog).toHaveTextContent('extension');
  fireEvent.click(within(dialog).getByRole('button', { name: '关闭弹窗' }));
  expect(request.mock.calls.every(([, options]) => options.method === 'GET')).toBe(true);
});

it('loads actual scripts in three panes with provenance, selections and no editor write handlers', async () => {
  render(<WorkflowEditorPage context={context} />);
  await screen.findByRole('button', { name: /准备数据.*cli-script/ });
  expect(request.mock.calls.some(([url]) => url.endsWith('/pack-scripts'))).toBe(false);
  fireEvent.click(screen.getByRole('button', { name: '脚本文件' }));
  fireEvent.click(await screen.findByRole('button', { name: 'scripts/a.sh' }));
  expect(screen.getByLabelText('脚本内容')).toHaveTextContent('echo actual-content');
  expect(screen.getByRole('complementary', { name: '脚本信息' })).toHaveTextContent('real-pack');
  expect(screen.getByRole('complementary', { name: '脚本信息' })).toHaveTextContent('100755');
  fireEvent.click(screen.getByRole('button', { name: 'scripts/b.py' }));
  expect(screen.getByLabelText('脚本内容')).toHaveTextContent('real-python');
  expect(screen.getByRole('button', { name: '保存脚本' })).toBeDisabled();
});

it('opens release history, exact snapshots and explicitly selected deployment diff', async () => {
  render(<WorkflowEditorPage context={context} />);
  await screen.findByRole('button', { name: /准备数据.*cli-script/ });
  fireEvent.click(screen.getByRole('button', { name: '发布历史' }));
  expect(await screen.findByText('生效发布 #6')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '查看快照 #7' }));
  expect(await screen.findByLabelText('发布快照 #7')).toHaveTextContent('真实护航');
  fireEvent.click(screen.getByRole('button', { name: '选择对比 #6' }));
  fireEvent.click(screen.getByRole('button', { name: '选择对比 #7' }));
  fireEvent.click(screen.getByRole('button', { name: '对比所选发布' }));
  expect(await screen.findByLabelText('版本差异')).toHaveTextContent('旧标题');
  expect(screen.getByLabelText('版本差异')).toHaveTextContent('新标题');
  expect(screen.getByRole('button', { name: '激活版本' })).toBeDisabled();
});

it('preserves 403/malformed states, prevents scripts reads without edit access, supports retry', async () => {
  request.mockImplementation((url: string, options: { params: Record<string, unknown> }) =>
    url.endsWith('/access')
      ? Promise.resolve({ workflowId: 'wf', canView: true, canEdit: false })
      : respond(url, options),
  );
  render(<WorkflowEditorPage context={context} />);
  await screen.findByRole('button', { name: /准备数据.*cli-script/ });
  fireEvent.click(screen.getByRole('button', { name: '脚本文件' }));
  expect(await screen.findByText(/脚本读取需要工作流编辑权限和 Pack/)).toBeInTheDocument();
  expect(request.mock.calls.some(([url]) => url.endsWith('/pack-scripts'))).toBe(false);
});

it('restores valid node deep link and resets evidence immediately across identity/workflow changes', async () => {
  const { rerender } = render(<WorkflowEditorPage context={{ ...context, query: { nodeId: 'b' } }} />);
  expect(await screen.findByRole('complementary', { name: '节点属性' })).toHaveTextContent('结果审核');
  let resolveNext: (value: unknown) => void = () => {
    throw new Error('deferred not initialized');
  };
  request.mockImplementation(
    () =>
      new Promise((resolve) => {
        resolveNext = resolve;
      }),
  );
  rerender(
    <WorkflowEditorPage context={{ ...context, scopeKey: 's2', userId: 'bob', workflowId: 'other', query: {} }} />,
  );
  expect(screen.queryByText('scripts/a.sh')).not.toBeInTheDocument();
  expect(screen.queryByRole('complementary', { name: '节点属性' })).not.toBeInTheDocument();
  await waitFor(() => expect(request.mock.calls.some(([, options]) => options.params.user_id === 'bob')).toBe(true));
  expect(request.mock.calls[0][1].signal.aborted).toBe(true);
  expect(resolveNext).toEqual(expect.any(Function));
});

it('clears node focus when browser query removes the deep link, without refetching the spec', async () => {
  const { rerender } = render(<WorkflowEditorPage context={{ ...context, query: { nodeId: 'b' } }} />);
  const inspector = await screen.findByRole('complementary', { name: '节点属性' });
  expect(inspector).toHaveTextContent('结果审核');
  const calls = request.mock.calls.length;
  rerender(<WorkflowEditorPage context={{ ...context, query: {} }} />);
  expect(inspector).toHaveTextContent('选择节点查看配置');
  expect(request).toHaveBeenCalledTimes(calls);
});

it('shows malformed spec error with retry, and does not create fallback graph or nodes', async () => {
  request.mockImplementation((url: string, options: { params: Record<string, unknown> }) =>
    url.endsWith('/workflows/wf') ? Promise.resolve({ ...spec, nodes: null }) : respond(url, options),
  );
  render(<WorkflowEditorPage context={context} />);
  expect(await screen.findByRole('alert')).toHaveTextContent('节点列表');
  expect(screen.queryByLabelText('工作流画布')).not.toBeInTheDocument();
  request.mockImplementation(respond);
  fireEvent.click(screen.getByRole('button', { name: '重试读取' }));
  expect(await screen.findByRole('button', { name: /准备数据.*cli-script/ })).toBeInTheDocument();
});

it('shows server Pack denial honestly and retries only that read', async () => {
  request.mockImplementation((url: string, options: { params: Record<string, unknown> }) =>
    url.endsWith('/pack-scripts')
      ? Promise.reject(Object.assign(new Error('Forbidden'), { status: 403 }))
      : respond(url, options),
  );
  render(<WorkflowEditorPage context={context} />);
  await screen.findByRole('button', { name: /准备数据.*cli-script/ });
  fireEvent.click(screen.getByRole('button', { name: '脚本文件' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('Pack 读取权限');
  expect(screen.queryByLabelText('脚本内容')).not.toBeInTheDocument();
  const workflowCalls = request.mock.calls.filter(([url]) => url.endsWith('/workflows/wf')).length;
  request.mockImplementation(respond);
  fireEvent.click(screen.getByRole('button', { name: '重试读取' }));
  expect(await screen.findByLabelText('脚本内容')).toHaveTextContent('echo actual-content');
  expect(request.mock.calls.filter(([url]) => url.endsWith('/workflows/wf'))).toHaveLength(workflowCalls);
});

it('never reads spec, history or scripts when view permission is explicitly denied', async () => {
  request.mockResolvedValue({ workflowId: 'wf', canView: false, canEdit: false });
  render(<WorkflowEditorPage context={context} />);
  expect(await screen.findByText('没有工作流查看权限')).toBeInTheDocument();
  expect(request).toHaveBeenCalledTimes(1);
  expect(request.mock.calls[0][0]).toMatch(/\/access$/);
});

it('uses the source two-workspace structure, toolbar history dialog and desktop side inspector', async () => {
  render(<WorkflowEditorPage context={context} />);
  await screen.findByRole('button', { name: /准备数据.*cli-script/ });
  const inspector = screen.getByRole('complementary', { name: '节点属性' });
  expect(inspector.parentElement).toHaveClass('xl:grid-cols-[minmax(0,1fr)_320px]');
  const workspace = screen.getByRole('group', { name: '编辑器工作区' });
  expect(within(workspace).getAllByRole('button')).toHaveLength(2);
  expect(within(workspace).getByRole('button', { name: '工作流编排' })).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '发布历史' }));
  const historyDialog = screen.getByRole('dialog', { name: '发布历史' });
  expect(historyDialog).toBeInTheDocument();
  fireEvent.keyDown(historyDialog, { key: 'Escape' });
  expect(screen.queryByRole('dialog', { name: '发布历史' })).not.toBeInTheDocument();
});
