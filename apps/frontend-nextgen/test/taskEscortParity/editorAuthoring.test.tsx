/** @jest-environment jsdom */
import { editorYaml } from '@/domain/taskEscortParity/editor';
import type { TaskEscortParityContext } from '@/domain/taskEscortParity/types';
import type { WorkflowAiDraftJob } from '@/domain/taskEscortParity/workflowAuthoringTypes';
import { WorkflowEditorPage } from '@/pages/TaskEscort/parity/editor/WorkflowEditorPage';
import { backendRequest } from '@/services/backendApi/httpClient';
import { workflowAuthoringService } from '@/services/taskEscortParity/workflowAuthoringService';
import '@testing-library/jest-dom';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { history } from '@umijs/max';
import { load } from 'js-yaml';
import { webcrypto } from 'node:crypto';
import { TextEncoder } from 'node:util';

jest.mock('@umijs/max', () => ({ history: jest.requireActual('history').createMemoryHistory() }));
jest.mock('@/services/backendApi/httpClient', () => ({ backendRequest: jest.fn() }));
jest.mock('@/services/taskEscortParity/workflowAuthoringService', () => ({
  workflowAuthoringService: {
    readBaseline: jest.fn(),
    readPublishReview: jest.fn(),
    capabilities: jest.fn(),
    validateYaml: jest.fn(),
    startModification: jest.fn(),
    waitForModification: jest.fn(),
    cancelModification: jest.fn(),
    applyModification: jest.fn(),
    save: jest.fn(),
    publish: jest.fn(),
  },
}));
const api = jest.mocked(workflowAuthoringService);
const request = jest.mocked(backendRequest);
const spec = {
  id: 'wf',
  title: '真实护航',
  version: 6,
  extension: { keep: true },
  nodes: [{ id: 'a', title: '原节点', executor: { type: 'approval' } }],
};
const nextSpec = { ...spec, nodes: [{ ...spec.nodes[0], title: 'AI 调整节点', extension: { keep: true } }] };
const context: TaskEscortParityContext = {
  scopeKey: 'alice:wf',
  ready: true,
  userId: 'alice',
  workflowId: 'wf',
  workflow: null,
  query: {},
  navigate: jest.fn(),
  refreshWorkflows: jest.fn(),
};
function job(overrides: Partial<WorkflowAiDraftJob> = {}): WorkflowAiDraftJob {
  return {
    kind: 'modification',
    draftId: 'draft-one',
    receipt: 'signed-receipt',
    workflowId: 'wf',
    userId: 'alice',
    requestId: 'request-one',
    currentYaml: editorYaml(spec),
    inputSpecDigest: 'a'.repeat(64),
    status: 'completed',
    result: { yaml: editorYaml(nextSpec), summary: '已调整原节点，保留其他配置' },
    ...overrides,
  };
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (cause: Error) => void;
  const promise = new Promise<T>((done, fail) => {
    resolve = done;
    reject = fail;
  });
  return { promise, resolve, reject };
}
async function ready() {
  await screen.findByRole('button', { name: /原节点.*approval/ });
}
async function yamlView() {
  fireEvent.click(screen.getByRole('button', { name: 'YAML' }));
  return screen.getByRole('textbox', { name: '工作流 YAML' });
}
async function generate() {
  fireEvent.click(screen.getByRole('button', { name: 'AI 修改' }));
  const prompt = await screen.findByRole('textbox', { name: '描述对现有流程的修改' });
  await waitFor(() => expect(screen.getByRole('button', { name: '生成 YAML 草稿' })).toBeDisabled());
  fireEvent.change(prompt, { target: { value: '调整原节点配置' } });
  await waitFor(() => expect(screen.getByRole('button', { name: '生成 YAML 草稿' })).toBeEnabled());
  fireEvent.click(screen.getByRole('button', { name: '生成 YAML 草稿' }));
}
beforeEach(() => {
  Object.defineProperty(globalThis, 'crypto', { value: webcrypto, configurable: true });
  Object.defineProperty(globalThis, 'TextEncoder', { value: TextEncoder, configurable: true });
  jest.clearAllMocks();
  history.replace('/evolution-factory/task-escort?view=editor&workflowId=wf');
  request.mockImplementation((url) =>
    Promise.resolve(
      url.endsWith('/access')
        ? { workflowId: url.includes('/other/') ? 'other' : 'wf', canView: true, canEdit: true }
        : url.includes('/other')
        ? { ...spec, id: 'other', title: '另一个工作流' }
        : spec,
    ),
  );
  api.readBaseline.mockResolvedValue({
    workflowId: 'wf',
    userId: 'alice',
    spec,
    packId: 'pack',
    rawPackId: 'pack',
    draftToken: 'baseline',
    specJson: JSON.stringify(spec),
    sourceVersion: 'current',
    sourceCommit: 'a'.repeat(40),
    expectedSavedHead: 'a'.repeat(40),
  });
  api.capabilities.mockResolvedValue({ available: true });
  api.validateYaml.mockImplementation(async (yaml) => {
    const parsed = load(yaml) as typeof spec;
    return { valid: true, issues: [], errors: [], warnings: [], spec: parsed, normalizedSpec: parsed };
  });
  api.startModification.mockImplementation(async (_id, input) =>
    job({ currentYaml: input.currentYaml, status: 'running', result: undefined }),
  );
  api.waitForModification.mockResolvedValue(job());
  api.cancelModification.mockResolvedValue(job({ status: 'cancelled', result: undefined }));
  api.applyModification.mockResolvedValue(nextSpec);
  api.save.mockResolvedValue({
    ok: true,
    saved: true,
    dbSaved: true,
    gitSaved: true,
    workflowId: 'wf',
    spec,
    packId: 'pack',
    branch: 'saved/wf',
    commit: 'a'.repeat(40),
    draftToken: 'next-baseline',
    requestId: 'save-one',
    baseline: {
      workflowId: 'wf',
      userId: 'alice',
      spec,
      packId: 'pack',
      rawPackId: 'pack',
      draftToken: 'next-baseline',
      specJson: JSON.stringify(spec),
      sourceVersion: 'draft',
      sourceCommit: 'a'.repeat(40),
      expectedSavedHead: 'a'.repeat(40),
    },
  });
  api.readPublishReview.mockResolvedValue({
    workflowId: 'wf',
    packId: 'pack',
    candidateCommit: 'a'.repeat(40),
    draftToken: 'next-baseline',
    expectedActiveCommit: 'c'.repeat(40),
    changes: [{ path: 'workflow.yaml', operation: 'replace', before: editorYaml(spec), after: editorYaml(nextSpec) }],
  });
  api.publish.mockResolvedValue({
    ok: true,
    deployed: true,
    workflowId: 'wf',
    packId: 'pack',
    deployNumber: 7,
    version: 6,
    tagName: 'wf-v6',
  });
});

it('edits, validates and explicitly applies YAML before updating the graph; extensions survive', async () => {
  render(<WorkflowEditorPage context={context} />);
  await ready();
  const yaml = await yamlView();
  fireEvent.change(yaml, { target: { value: editorYaml(nextSpec) } });
  expect(screen.getAllByText('未保存').length).toBeGreaterThan(0);
  expect(screen.getByRole('button', { name: '应用 YAML 到画布' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: '校验 YAML' }));
  await waitFor(() => expect(screen.getByRole('button', { name: '应用 YAML 到画布' })).toBeEnabled());
  fireEvent.click(screen.getByRole('button', { name: '应用 YAML 到画布' }));
  fireEvent.click(screen.getByRole('button', { name: '画布' }));
  expect(await screen.findByRole('button', { name: /AI 调整节点.*approval/ })).toBeInTheDocument();
  expect(api.save).not.toHaveBeenCalled();
  expect(api.publish).not.toHaveBeenCalled();
  expect(api.validateYaml).toHaveBeenCalledWith(editorYaml(nextSpec), { user_id: 'alice' }, expect.any(AbortSignal));
});

it('preserves invalid or failed-save drafts and never publishes after a failed save', async () => {
  render(<WorkflowEditorPage context={context} />);
  await ready();
  const yaml = await yamlView();
  const text = editorYaml({ ...spec, title: '未保存编辑' });
  fireEvent.change(yaml, { target: { value: text } });
  api.validateYaml.mockResolvedValueOnce({
    valid: false,
    issues: [],
    errors: [{ path: 'nodes', message: '依赖不存在', severity: 'error' }],
    warnings: [],
    spec: null,
    normalizedSpec: null,
  });
  fireEvent.click(screen.getByRole('button', { name: '保存工作流' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('依赖不存在');
  expect(yaml).toHaveValue(text);
  expect(api.save).not.toHaveBeenCalled();
  api.save.mockRejectedValueOnce(new Error('Git 保存失败'));
  fireEvent.click(screen.getByRole('button', { name: '发布工作流' }));
  fireEvent.click(
    within(screen.getByRole('dialog', { name: '发布工作流' })).getByRole('button', { name: '保存并预览发布差异' }),
  );
  expect(await screen.findByRole('alert')).toHaveTextContent('Git 保存失败');
  expect(yaml).toHaveValue(text);
  expect(api.publish).not.toHaveBeenCalled();
  expect(screen.getAllByText('未保存').length).toBeGreaterThan(0);
});

it('saves before publishing, reports publish failure honestly and retains saved YAML', async () => {
  render(<WorkflowEditorPage context={context} />);
  await ready();
  const yaml = await yamlView();
  fireEvent.change(yaml, { target: { value: editorYaml(nextSpec) } });
  api.publish.mockRejectedValueOnce(new Error('发布服务不可用'));
  fireEvent.click(screen.getByRole('button', { name: '发布工作流' }));
  fireEvent.click(screen.getByRole('button', { name: '保存并预览发布差异' }));
  const review = await screen.findByRole('dialog', { name: '审阅发布文件差异' });
  expect(api.publish).not.toHaveBeenCalled();
  expect(within(review).getByLabelText('workflow.yaml 发布后')).toHaveTextContent('AI 调整节点');
  expect(screen.getByRole('button', { name: '确认发布候选版本' })).toBeDisabled();
  fireEvent.click(within(review).getByRole('checkbox'));
  fireEvent.click(screen.getByRole('button', { name: '确认发布候选版本' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('工作流已保存，发布失败：发布服务不可用');
  expect(api.publish).toHaveBeenCalledWith(
    expect.objectContaining({
      receipt: expect.objectContaining({ commit: 'a'.repeat(40), draftToken: 'next-baseline' }),
      review: expect.objectContaining({ candidateCommit: 'a'.repeat(40) }),
      confirmedFileDiff: true,
    }),
    { user_id: 'alice' },
    expect.any(AbortSignal),
  );
  expect(api.save.mock.invocationCallOrder[0]).toBeLessThan(api.publish.mock.invocationCallOrder[0]);
  expect(api.save).toHaveBeenCalledWith(
    expect.objectContaining({ workflowId: 'wf', spec: nextSpec }),
    { user_id: 'alice' },
    expect.any(AbortSignal),
  );
  expect(yaml).toHaveValue(editorYaml(nextSpec));
  expect(screen.queryByText('工作流已保存并发布。')).not.toBeInTheDocument();
});

it('sends the full unsaved YAML to AI and requires preview, validation and reviewed application', async () => {
  render(<WorkflowEditorPage context={context} />);
  await ready();
  const yaml = await yamlView(),
    unsaved = editorYaml({ ...spec, extension: { keep: true, unsaved: '必须传给 AI' } });
  fireEvent.change(yaml, { target: { value: unsaved } });
  api.waitForModification.mockResolvedValue(job({ currentYaml: unsaved }));
  await generate();
  const preview = await screen.findByRole('region', { name: 'AI 草稿预览' });
  expect(api.startModification).toHaveBeenCalledWith(
    'wf',
    expect.objectContaining({ currentYaml: unsaved, prompt: '调整原节点配置' }),
    { user_id: 'alice' },
    expect.any(AbortSignal),
  );
  expect(yaml).toHaveValue(unsaved);
  expect(api.applyModification).not.toHaveBeenCalled();
  expect(api.save).not.toHaveBeenCalled();
  const apply = within(preview).getByRole('button', { name: '应用草稿到当前 Editor' });
  expect(apply).toBeDisabled();
  fireEvent.click(within(preview).getByRole('button', { name: '校验 AI 草稿' }));
  const checkbox = await screen.findByRole('checkbox');
  const validatedApply = screen.getByRole('button', { name: '应用草稿到当前 Editor' });
  expect(validatedApply).toBeDisabled();
  fireEvent.click(checkbox);
  fireEvent.click(validatedApply);
  await waitFor(() => expect(yaml).toHaveValue(editorYaml(nextSpec)));
  fireEvent.click(screen.getByRole('button', { name: '画布' }));
  expect(await screen.findByRole('button', { name: /AI 调整节点.*approval/ })).toBeInTheDocument();
  expect(api.save).not.toHaveBeenCalled();
  expect(api.publish).not.toHaveBeenCalled();
});

it('keeps the candidate stale after local edits and cannot apply it over a newer draft', async () => {
  render(<WorkflowEditorPage context={context} />);
  await ready();
  const yaml = await yamlView();
  await generate();
  await screen.findByRole('region', { name: 'AI 草稿预览' });
  fireEvent.click(screen.getByRole('button', { name: '校验 AI 草稿' }));
  fireEvent.click(await screen.findByRole('checkbox'));
  fireEvent.change(yaml, { target: { value: editorYaml({ ...spec, title: '生成后又编辑' }) } });
  expect(screen.getByText(/草稿已过期/)).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '应用草稿到当前 Editor' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: '应用草稿到当前 Editor' }));
  expect(api.applyModification).not.toHaveBeenCalled();
});

it('exposes async progress and cancel, ignores non-cooperative late completions', async () => {
  const waiting = deferred<WorkflowAiDraftJob>();
  api.waitForModification.mockReturnValue(waiting.promise);
  render(<WorkflowEditorPage context={context} />);
  await ready();
  await generate();
  await waitFor(() => expect(api.waitForModification).toHaveBeenCalled());
  expect(screen.getByText(/任务状态：running/)).toBeInTheDocument();
  const signal = api.waitForModification.mock.calls[0][2]?.signal;
  fireEvent.click(screen.getByRole('button', { name: '取消生成' }));
  expect(await screen.findByText('AI 任务已取消，未更改工作流。')).toBeInTheDocument();
  expect(signal?.aborted).toBe(true);
  expect(api.cancelModification).toHaveBeenCalled();
  await act(async () => waiting.resolve(job()));
  expect(screen.queryByRole('region', { name: 'AI 草稿预览' })).not.toBeInTheDocument();
});

it.each(['identity', 'workflow'] as const)(
  'aborts old AI requests and hides old results on %s switches',
  async (change) => {
    const waiting = deferred<WorkflowAiDraftJob>();
    api.waitForModification.mockReturnValue(waiting.promise);
    const { rerender } = render(<WorkflowEditorPage context={context} />);
    await ready();
    await generate();
    await waitFor(() => expect(api.waitForModification).toHaveBeenCalled());
    const signal = api.waitForModification.mock.calls[0][2]?.signal;
    rerender(
      <WorkflowEditorPage
        context={{
          ...context,
          scopeKey: 'new-scope',
          userId: change === 'identity' ? 'bob' : 'alice',
          workflowId: change === 'workflow' ? 'other' : 'wf',
        }}
      />,
    );
    expect(signal?.aborted).toBe(true);
    await act(async () => waiting.resolve(job()));
    await waitFor(() => expect(api.cancelModification).toHaveBeenCalled());
    expect(screen.queryByRole('region', { name: 'AI 草稿预览' })).not.toBeInTheDocument();
    expect(api.applyModification).not.toHaveBeenCalled();
  },
);

it('supports failed generation retry without touching saved content', async () => {
  api.waitForModification.mockRejectedValueOnce(new Error('任务轮询失败'));
  render(<WorkflowEditorPage context={context} />);
  await ready();
  await generate();
  expect(await screen.findByRole('alert')).toHaveTextContent('任务轮询失败');
  fireEvent.click(screen.getByRole('button', { name: '重试生成' }));
  expect(await screen.findByRole('region', { name: 'AI 草稿预览' })).toBeInTheDocument();
  expect(api.save).not.toHaveBeenCalled();
  expect(api.applyModification).not.toHaveBeenCalled();
});

it('guards dirty exits, keeps drafts on stay and releases history only on explicit discard', async () => {
  const { unmount } = render(<WorkflowEditorPage context={context} />);
  await ready();
  const yaml = await yamlView();
  fireEvent.change(yaml, { target: { value: editorYaml(nextSpec) } });
  const unload = new Event('beforeunload', { cancelable: true });
  window.dispatchEvent(unload);
  expect(unload.defaultPrevented).toBe(true);
  act(() => history.push('/outside'));
  expect(history.location.pathname).not.toBe('/outside');
  fireEvent.click(screen.getByRole('button', { name: '继续编辑' }));
  expect(yaml).toHaveValue(editorYaml(nextSpec));
  act(() => history.push('/outside'));
  fireEvent.click(screen.getByRole('button', { name: '放弃草稿并继续' }));
  expect(history.location.pathname).toBe('/outside');
  unmount();
  const clean = new Event('beforeunload', { cancelable: true });
  window.dispatchEvent(clean);
  expect(clean.defaultPrevented).toBe(false);
});
