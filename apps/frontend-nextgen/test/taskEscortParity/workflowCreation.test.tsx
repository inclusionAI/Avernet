/** @jest-environment jsdom */
import type { TaskEscortParityContext } from '@/domain/taskEscortParity/types';
import { useWorkflowCreation } from '@/hooks/taskEscortParity/useWorkflowCreation';
import { WorkspaceHeader } from '@/pages/TaskEscort/WorkspaceHeader';
import { workflowAuthoringService } from '@/services/taskEscortParity/workflowAuthoringService';
import '@testing-library/jest-dom';
import { act, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react';

jest.mock(
  '@/services/taskEscortParity/workflowAuthoringService',
  () => ({
    workflowAuthoringService: {
      createBlank: jest.fn(),
      createFromExisting: jest.fn(),
      createFromSpec: jest.fn(),
      startGeneration: jest.fn(),
      waitForGeneration: jest.fn(),
      cancelGeneration: jest.fn(),
      applyGeneration: jest.fn(),
      listCloneCandidates: jest.fn(),
      readClonePreview: jest.fn(),
      readBaseline: jest.fn(),
      resumeGeneration: jest.fn(),
      cancelModification: jest.fn(),
    },
  }),
  { virtual: true },
);
const service = jest.mocked(workflowAuthoringService);
const spec = {
  id: 'new-flow',
  title: '新工作流',
  version: '1.0.0',
  nodes: [{ id: 'start', executor: { type: 'done' } }],
};
const rows = [{ id: 'source', title: '原工作流', runCount: null, lastStatus: null, ownerId: 'owner' }];
const makeContext = (overrides: Partial<TaskEscortParityContext> = {}): TaskEscortParityContext => ({
  scopeKey: 'user-a:space-a',
  ready: false,
  creationReady: true,
  userId: 'user-a',
  workflowId: '',
  workflow: null,
  query: {},
  navigate: jest.fn(),
  refreshWorkflows: jest.fn(),
  ...overrides,
});
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}
function fill(result: { current: ReturnType<typeof useWorkflowCreation> }) {
  act(() => result.current.update({ workflowId: ' new-flow ', title: ' 新工作流 ' }));
}
beforeEach(() => {
  jest.clearAllMocks();
  service.createBlank.mockResolvedValue(spec);
  service.createFromSpec.mockResolvedValue(spec);
  service.createFromExisting.mockResolvedValue({
    targetWorkflowId: spec.id,
    targetPackId: spec.id,
    sourceWorkflowId: 'source',
    sourcePackId: 'source',
    sourceTitle: '原工作流',
    baseCommit: 'a'.repeat(40),
    scriptCount: 2,
    branch: 'saved/new-flow',
    commit: 'b'.repeat(40),
  });
  service.cancelGeneration.mockImplementation(async (job) => ({ ...job, status: 'cancelled', cancellation: 'local-only' }));
});

it('creates from an empty list using explicit identity, refreshes and opens the new editor', async () => {
  const context = makeContext();
  const onCreated = jest.fn();
  const { result } = renderHook(() => useWorkflowCreation(context, onCreated));
  fill(result);
  expect(result.current.canSubmit).toBe(true);
  await act(async () => result.current.submit());
  expect(service.createBlank).toHaveBeenCalledWith(
    { workflowId: 'new-flow', title: '新工作流' },
    { user_id: 'user-a' },
    expect.any(AbortSignal),
  );
  expect(context.refreshWorkflows).toHaveBeenCalledTimes(1);
  expect(context.navigate).toHaveBeenCalledWith('editor', { workflowId: 'new-flow' });
  expect(onCreated).toHaveBeenCalledTimes(1);
});

it('prevents duplicate creates synchronously and retains fields on 409/403 for retry', async () => {
  const pending = deferred<typeof spec>();
  service.createBlank.mockReturnValueOnce(pending.promise);
  const context = makeContext();
  const { result } = renderHook(() => useWorkflowCreation(context));
  fill(result);
  let first!: Promise<void>;
  act(() => {
    first = result.current.submit();
    void result.current.submit();
  });
  expect(service.createBlank).toHaveBeenCalledTimes(1);
  await act(async () => {
    pending.reject(Object.assign(new Error('conflict'), { status: 409 }));
    await first;
  });
  expect(result.current.error).toMatch(/409|已存在|冲突/);
  expect(result.current.form).toMatchObject({ workflowId: ' new-flow ', title: ' 新工作流 ' });
  expect(context.navigate).not.toHaveBeenCalled();
  service.createBlank.mockRejectedValueOnce(Object.assign(new Error('forbidden'), { status: 403 }));
  await act(async () => result.current.submit());
  expect(result.current.error).toMatch(/403|权限/);
  expect(result.current.form.title).toBe(' 新工作流 ');
  await act(async () => result.current.submit());
  expect(context.navigate).toHaveBeenCalledTimes(1);
});

it.each(['', 'bad id', '../path', 'bad/name', 'bad\\name', '__reserved__', 'bad.lock', 'bad.'])(
  'rejects invalid workflow ID %j before IO',
  async (workflowId) => {
    const { result } = renderHook(() => useWorkflowCreation(makeContext()));
    act(() => result.current.update({ workflowId, title: 'Title' }));
    expect(result.current.canSubmit).toBe(false);
    await act(async () => result.current.submit());
    expect(service.createBlank).not.toHaveBeenCalled();
  },
);

it('requires a title and resolved explicit identity even if leaf reads are ready', async () => {
  const { result, rerender } = renderHook(({ context }) => useWorkflowCreation(context), {
    initialProps: { context: makeContext({ ready: true, userId: undefined }) },
  });
  fill(result);
  expect(result.current.canSubmit).toBe(false);
  await act(async () => result.current.submit());
  expect(service.createBlank).not.toHaveBeenCalled();
  rerender({ context: makeContext({ creationReady: false }) });
  fill(result);
  expect(result.current.canSubmit).toBe(false);
  rerender({ context: makeContext() });
  act(() => result.current.update({ workflowId: 'new-flow', title: '   ' }));
  expect(result.current.canSubmit).toBe(false);
});

it('aborts in-flight creates and ignores replies after close or identity/space switch', async () => {
  const pending = deferred<typeof spec>();
  service.createBlank.mockReturnValue(pending.promise);
  const first = makeContext();
  const onCreated = jest.fn();
  const { result, rerender, unmount } = renderHook(({ context }) => useWorkflowCreation(context, onCreated), {
    initialProps: { context: first },
  });
  fill(result);
  let request!: Promise<void>;
  act(() => {
    request = result.current.submit();
  });
  const signal = service.createBlank.mock.calls[0][2]!;
  const second = makeContext({ scopeKey: 'user-b:space-b', userId: 'user-b' });
  rerender({ context: second });
  expect(signal.aborted).toBe(true);
  expect(result.current.form.workflowId).toBe('');
  await act(async () => {
    pending.resolve(spec);
    await request;
  });
  expect(first.refreshWorkflows).not.toHaveBeenCalled();
  expect(second.navigate).not.toHaveBeenCalled();
  expect(onCreated).not.toHaveBeenCalled();
  fill(result);
  service.createBlank.mockReturnValue(new Promise(() => {}));
  act(() => {
    void result.current.submit();
  });
  const closeSignal = service.createBlank.mock.calls.at(-1)![2]!;
  unmount();
  expect(closeSignal.aborted).toBe(true);
});

it('enables the real header entry for empty lists and closes on scope changes', async () => {
  const context = makeContext();
  const { rerender } = render(
    <WorkspaceHeader page="runs" context={context} workflows={[]} selectWorkflow={jest.fn()} />,
  );
  expect(screen.getByRole('button', { name: '新建工作流' })).toBeEnabled();
  fireEvent.click(screen.getByRole('button', { name: '新建工作流' }));
  expect(screen.getByRole('dialog', { name: '新建工作流' })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '空白工作流' })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '基于已有工作流' })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'AI 生成' })).toBeInTheDocument();
  rerender(
    <WorkspaceHeader
      page="runs"
      context={makeContext({ scopeKey: 'other-space' })}
      workflows={rows}
      selectWorkflow={jest.fn()}
    />,
  );
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  rerender(
    <WorkspaceHeader
      page="runs"
      context={makeContext({ creationReady: false, userId: undefined })}
      workflows={[]}
      selectWorkflow={jest.fn()}
    />,
  );
  expect(screen.getByRole('button', { name: '新建工作流' })).toBeDisabled();
});

it('submits a new ID/title and the selected source ID through the clone service', async () => {
  const context = makeContext();
  const { result } = renderHook(() => useWorkflowCreation(context));
  fill(result);
  act(() => result.current.update({ source: 'existing', sourceWorkflowId: 'source' }));
  await act(async () => result.current.submit());
  expect(service.createFromExisting).toHaveBeenCalledWith(
    { workflowId: 'new-flow', title: '新工作流', sourceWorkflowId: 'source' },
    { user_id: 'user-a' },
    expect.any(AbortSignal),
  );
  expect(service.createBlank).not.toHaveBeenCalled();
  expect(context.navigate).toHaveBeenCalledWith('editor', { workflowId: 'new-flow' });
});

it('requires a source distinct from the target and preserves selected source after a denied clone', async () => {
  const { result } = renderHook(() => useWorkflowCreation(makeContext()));
  fill(result);
  act(() => result.current.update({ source: 'existing' }));
  expect(result.current.canSubmit).toBe(false);
  act(() => result.current.update({ sourceWorkflowId: 'new-flow' }));
  expect(result.current.canSubmit).toBe(false);
  act(() => result.current.update({ sourceWorkflowId: 'source' }));
  service.createFromExisting.mockRejectedValueOnce(Object.assign(new Error('forbidden'), { status: 403 }));
  await act(async () => result.current.submit());
  expect(result.current.error).toMatch(/权限/);
  expect(result.current.form.sourceWorkflowId).toBe('source');
});

it('reads real clone candidates independently of the empty workspace list, searches and previews the selection', async () => {
  service.listCloneCandidates.mockResolvedValue([
    { workflowId: 'source', title: '原工作流', packId: 'source-pack', updatedAt: 1 },
    { workflowId: 'another', title: '其他来源', packId: null, updatedAt: 2 },
  ]);
  service.readClonePreview.mockResolvedValue({
    sourceWorkflowId: 'source',
    sourcePackId: 'source-pack',
    sourceTitle: '原工作流',
    baseCommit: 'a'.repeat(40),
    scriptCount: 2,
  });
  const context = makeContext();
  render(<WorkspaceHeader page="runs" context={context} workflows={[]} selectWorkflow={jest.fn()} />);
  fireEvent.click(screen.getByRole('button', { name: '新建工作流' }));
  fireEvent.click(screen.getByRole('button', { name: '基于已有工作流' }));
  expect(await screen.findByRole('button', { name: /原工作流/ })).toBeInTheDocument();
  expect(service.listCloneCandidates).toHaveBeenCalledWith({ user_id: 'user-a' }, expect.any(AbortSignal));
  fireEvent.change(screen.getByRole('searchbox', { name: '搜索源工作流' }), { target: { value: 'SOURCE' } });
  expect(screen.queryByRole('button', { name: /其他来源/ })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: /原工作流/ }));
  expect(await screen.findByText('a'.repeat(40))).toBeInTheDocument();
  expect(service.readClonePreview).toHaveBeenCalledWith('source', { user_id: 'user-a' }, expect.any(AbortSignal));
  fireEvent.change(screen.getByRole('textbox', { name: '工作流 ID' }), { target: { value: 'new-flow' } });
  expect(screen.getByRole('textbox', { name: '工作流名称' })).toHaveValue('原工作流');
  fireEvent.click(screen.getByRole('button', { name: '创建' }));
  await waitFor(() => expect(service.createFromExisting).toHaveBeenCalledTimes(1));
  expect(service.createFromExisting).toHaveBeenCalledWith(
    { workflowId: 'new-flow', title: '原工作流', sourceWorkflowId: 'source' },
    { user_id: 'user-a' },
    expect.any(AbortSignal),
  );
});

it('closes a pending create via the header and ignores the late response', async () => {
  const pending = deferred<typeof spec>();
  service.createBlank.mockReturnValueOnce(pending.promise);
  const context = makeContext();
  render(<WorkspaceHeader page="runs" context={context} workflows={[]} selectWorkflow={jest.fn()} />);
  fireEvent.click(screen.getByRole('button', { name: '新建工作流' }));
  fireEvent.change(screen.getByRole('textbox', { name: '工作流 ID' }), { target: { value: 'new-flow' } });
  fireEvent.change(screen.getByRole('textbox', { name: '工作流名称' }), { target: { value: '新工作流' } });
  fireEvent.click(screen.getByRole('button', { name: '创建' }));
  const signal = service.createBlank.mock.calls[0][2]!;
  fireEvent.click(screen.getByRole('button', { name: '取消' }));
  expect(signal.aborted).toBe(true);
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  await act(async () => pending.resolve(spec));
  expect(context.navigate).not.toHaveBeenCalled();
});

it('does not refresh or navigate on a response for a different workflow', async () => {
  service.createBlank.mockResolvedValueOnce({ ...spec, id: 'different' });
  const context = makeContext();
  const { result } = renderHook(() => useWorkflowCreation(context));
  fill(result);
  await act(async () => result.current.submit());
  expect(result.current.error).toMatch(/不属于目标工作流/);
  expect(context.refreshWorkflows).not.toHaveBeenCalled();
  expect(context.navigate).not.toHaveBeenCalled();
});

it.each([
  Object.assign(new Error('network lost'), { creationUncertain: true }),
  Object.assign(new Error('timeout'), { status: 504 }),
  new Error('invalid response after submission'),
])('locks an uncertain creation outcome without clearing inputs or blindly retrying: %s', async (error) => {
  service.createBlank.mockRejectedValueOnce(error);
  const context = makeContext();
  const { result } = renderHook(() => useWorkflowCreation(context));
  fill(result);
  await act(async () => result.current.submit());
  expect(result.current.uncertain).toBe(true);
  expect(result.current.form.workflowId).toBe(' new-flow ');
  expect(result.current.canSubmit).toBe(false);
  act(() => result.current.update({ workflowId: 'different-new-flow' }));
  expect(result.current.form.workflowId).toBe(' new-flow ');
  await act(async () => result.current.submit());
  expect(service.createBlank).toHaveBeenCalledTimes(1);
  expect(context.navigate).not.toHaveBeenCalled();
});

it('uses the workspace post-create navigation port instead of refreshing a dirty editor immediately', async () => {
  const context = makeContext({ openCreatedWorkflow: jest.fn() });
  const { result } = renderHook(() => useWorkflowCreation(context));
  fill(result);
  await act(async () => result.current.submit());
  expect(context.openCreatedWorkflow).toHaveBeenCalledWith('new-flow');
  expect(context.refreshWorkflows).not.toHaveBeenCalled();
  expect(context.navigate).not.toHaveBeenCalled();
});
