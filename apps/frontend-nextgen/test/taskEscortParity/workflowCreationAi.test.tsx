/** @jest-environment jsdom */
import type { TaskEscortParityContext } from '@/domain/taskEscortParity/types';
import {
  WorkflowAiRecoveryError,
  type WorkflowAiDraftJob,
  type WorkflowAiGenerationJob,
} from '@/domain/taskEscortParity/workflowAuthoringTypes';
import { useWorkflowCreation } from '@/hooks/taskEscortParity/useWorkflowCreation';
import { WorkflowCreationDialog } from '@/pages/TaskEscort/parity/WorkflowCreationDialog';
import { workflowAuthoringService } from '@/services/taskEscortParity/workflowAuthoringService';
import '@testing-library/jest-dom';
import { act, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react';

jest.mock('@/services/taskEscortParity/workflowAuthoringService', () => ({
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
}));
const service = jest.mocked(workflowAuthoringService);
const context = (): TaskEscortParityContext => ({
  scopeKey: 'user:space',
  ready: false,
  creationReady: true,
  userId: 'user',
  workflowId: '',
  workflow: null,
  query: {},
  navigate: jest.fn(),
  refreshWorkflows: jest.fn(),
});
const spec = {
  id: 'new-flow',
  title: '新工作流',
  version: '1.0.0',
  nodes: [{ id: 'start', executor: { type: 'done' } }],
  vendor: { retained: true },
};
const pending: WorkflowAiGenerationJob = {
  kind: 'generation',
  generationId: 'job-1',
  userId: 'user',
  status: 'pending',
};
const completed: WorkflowAiGenerationJob = {
  ...pending,
  status: 'completed',
  result: { yaml: 'a yaml candidate', summary: '新增一个节点' },
};
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((yes) => {
    resolve = yes;
  });
  return { promise, resolve };
}
function fill(result: { current: ReturnType<typeof useWorkflowCreation> }) {
  act(() =>
    result.current.update({ source: 'ai', workflowId: 'new-flow', title: '新工作流', aiSourceWorkflowId: 'source' }),
  );
  act(() => result.current.ai.changePrompt('收到请求后生成报告，并发送告警'));
}
beforeEach(() => {
  jest.clearAllMocks();
  service.readBaseline.mockResolvedValue({
    workflowId: 'source',
    userId: 'user',
    packId: 'source',
    rawPackId: 'source',
    draftToken: 'draft-token',
    spec: { ...spec, id: 'source' },
    specJson: JSON.stringify({ ...spec, id: 'source' }),
    sourceVersion: 'draft',
    sourceCommit: 'a'.repeat(40),
    expectedSavedHead: 'a'.repeat(40),
  });
  service.listCloneCandidates.mockResolvedValue([
    { workflowId: 'source', title: '来源工作流', packId: 'source', updatedAt: 1 },
  ]);
  service.readClonePreview.mockResolvedValue({
    sourceWorkflowId: 'source',
    sourcePackId: 'source',
    sourceTitle: '来源工作流',
    baseCommit: 'a'.repeat(40),
    scriptCount: 0,
  });
  service.startGeneration.mockResolvedValue(pending);
  service.waitForGeneration.mockResolvedValue(completed);
  service.applyGeneration.mockResolvedValue(spec);
  service.createFromSpec.mockResolvedValue(spec);
  service.cancelGeneration.mockImplementation(async (job) => ({
    ...job,
    status: 'cancelled',
    cancellation: 'local-only',
  }));
});

it('polls then validates and previews a candidate, and requires explicit apply before creating', async () => {
  const scope = context();
  const { result } = renderHook(() => useWorkflowCreation(scope));
  fill(result);
  await waitFor(() => expect(result.current.ai.source.data).not.toBeNull());
  await act(async () => result.current.ai.generate());
  expect(service.startGeneration).toHaveBeenCalledWith(
    expect.objectContaining({
      workflowId: 'source',
      currentYaml: expect.stringContaining('id: source'),
      prompt: expect.stringContaining('收到请求后生成报告'),
    }),
    { user_id: 'user' },
    expect.any(AbortSignal),
  );
  expect(service.waitForGeneration).toHaveBeenCalledWith(
    pending,
    { user_id: 'user' },
    expect.objectContaining({ signal: expect.any(AbortSignal), onUpdate: expect.any(Function) }),
  );
  expect(service.applyGeneration).toHaveBeenCalledWith(
    completed,
    { workflowId: 'new-flow', title: '新工作流' },
    { user_id: 'user' },
    expect.any(AbortSignal),
  );
  expect(result.current.ai.candidate).toEqual(spec);
  expect(result.current.canSubmit).toBe(false);
  await act(async () => result.current.submit());
  expect(service.createFromSpec).not.toHaveBeenCalled();
  expect(service.createBlank).not.toHaveBeenCalled();
  act(() => result.current.ai.apply());
  expect(result.current.canSubmit).toBe(true);
  expect(service.createFromSpec).not.toHaveBeenCalled();
  await act(async () => result.current.submit());
  expect(service.createFromSpec).toHaveBeenCalledWith(
    { workflowId: 'new-flow', title: '新工作流', spec },
    { user_id: 'user' },
    expect.any(AbortSignal),
  );
  expect(scope.navigate).toHaveBeenCalledWith('editor', { workflowId: 'new-flow' });
});

it('renders asynchronous generation progress, YAML preview and a separate application action', async () => {
  const wait = deferred<WorkflowAiGenerationJob>();
  service.waitForGeneration.mockReturnValue(wait.promise);
  render(<WorkflowCreationDialog context={context()} onClose={jest.fn()} />);
  fireEvent.click(screen.getByRole('button', { name: 'AI 生成' }));
  fireEvent.click(await screen.findByRole('button', { name: /来源工作流/ }));
  await waitFor(() => expect(service.readBaseline).toHaveBeenCalled());
  await act(async () => undefined);
  fireEvent.change(screen.getByRole('textbox', { name: '工作流 ID' }), { target: { value: 'new-flow' } });
  fireEvent.change(screen.getByRole('textbox', { name: '工作流名称' }), { target: { value: '新工作流' } });
  fireEvent.change(screen.getByLabelText('描述你的工作流需求'), { target: { value: '生成报告' } });
  fireEvent.click(screen.getByRole('button', { name: '生成 YAML 草稿' }));
  expect(screen.getByRole('status')).toHaveTextContent('AI 正在生成工作流');
  await waitFor(() => expect(service.waitForGeneration).toHaveBeenCalled());
  await act(async () => {
    wait.resolve(completed);
  });
  expect(screen.getByRole('region', { name: 'AI 草稿预览' })).toBeInTheDocument();
  expect(screen.getByLabelText('生成的 YAML')).toHaveTextContent('retained: true');
  expect(screen.getByRole('button', { name: '创建' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: '应用草稿到创建表单' }));
  expect(screen.getByRole('button', { name: '创建' })).toBeEnabled();
  expect(service.createFromSpec).not.toHaveBeenCalled();
});

it('keeps prompt/target inputs on validation failure and never creates an invalid candidate', async () => {
  service.applyGeneration.mockRejectedValueOnce(new Error('草稿校验失败：nodes 不是有效 DAG'));
  const { result } = renderHook(() => useWorkflowCreation(context()));
  fill(result);
  await waitFor(() => expect(result.current.ai.source.data).not.toBeNull());
  await act(async () => result.current.ai.generate());
  expect(result.current.ai.error).toMatch(/草稿校验失败/);
  expect(result.current.ai.prompt).toBe('收到请求后生成报告，并发送告警');
  expect(result.current.form.workflowId).toBe('new-flow');
  act(() => result.current.ai.apply());
  await act(async () => result.current.submit());
  expect(service.createFromSpec).not.toHaveBeenCalled();
  expect(result.current.canSubmit).toBe(false);
  await waitFor(() => expect(result.current.ai.source.data).not.toBeNull());
  await act(async () => result.current.ai.generate());
  expect(result.current.ai.candidate).toEqual(spec);
});

it('invalidates applied candidates when target, prompt or mode changes', async () => {
  const { result } = renderHook(() => useWorkflowCreation(context()));
  fill(result);
  await waitFor(() => expect(result.current.ai.source.data).not.toBeNull());
  await act(async () => result.current.ai.generate());
  act(() => result.current.ai.apply());
  expect(result.current.canSubmit).toBe(true);
  act(() => result.current.update({ title: '另一个名称' }));
  expect(result.current.ai.appliedSpec).toBeNull();
  expect(result.current.ai.prompt).toBe('收到请求后生成报告，并发送告警');
  expect(result.current.canSubmit).toBe(false);
  act(() => result.current.update({ title: '新工作流' }));
  await waitFor(() => expect(result.current.ai.source.data).not.toBeNull());
  await act(async () => result.current.ai.generate());
  act(() => result.current.ai.apply());
  act(() => result.current.ai.changePrompt('换个需求'));
  expect(result.current.ai.appliedSpec).toBeNull();
  expect(result.current.canSubmit).toBe(false);
  act(() => result.current.update({ source: 'blank' }));
  act(() => result.current.update({ source: 'ai' }));
  expect(result.current.ai.candidate).toBeNull();
});

it('cancels waiting, ignores late completion and accurately discloses local-only cancellation', async () => {
  const wait = deferred<WorkflowAiGenerationJob>();
  service.waitForGeneration.mockReturnValue(wait.promise);
  const { result } = renderHook(() => useWorkflowCreation(context()));
  fill(result);
  await waitFor(() => expect(result.current.ai.source.data).not.toBeNull());
  let request!: Promise<void>;
  act(() => {
    request = result.current.ai.generate();
  });
  await waitFor(() => expect(service.waitForGeneration).toHaveBeenCalledTimes(1));
  const signal = service.waitForGeneration.mock.calls[0][2]!.signal!;
  act(() => result.current.ai.cancel());
  expect(signal.aborted).toBe(true);
  await waitFor(() => expect(service.cancelGeneration).toHaveBeenCalledWith(pending, { user_id: 'user' }));
  await act(async () => {
    wait.resolve(completed);
    await request;
  });
  expect(result.current.ai.phase).toBe('cancelled');
  expect(result.current.ai.candidate).toBeNull();
  expect(service.applyGeneration).not.toHaveBeenCalled();
});

it.each(['space', 'identity'])(
  'discards old generation responses on %s change even before a generation ID arrives',
  async (change) => {
    const start = deferred<WorkflowAiGenerationJob>();
    service.startGeneration.mockReturnValue(start.promise);
    const first = context();
    const { result, rerender } = renderHook(({ scope }) => useWorkflowCreation(scope), {
      initialProps: { scope: first },
    });
    fill(result);
    await waitFor(() => expect(result.current.ai.source.data).not.toBeNull());
    let request!: Promise<void>;
    act(() => {
      request = result.current.ai.generate();
    });
    const signal = service.startGeneration.mock.calls[0][2]!;
    rerender({
      scope: {
        ...first,
        scopeKey: change === 'space' ? 'user:other-space' : first.scopeKey,
        userId: change === 'identity' ? 'other-user' : first.userId,
      },
    });
    expect(signal.aborted).toBe(true);
    await act(async () => {
      start.resolve(pending);
      await request;
    });
    await waitFor(() => expect(service.cancelGeneration).toHaveBeenCalledWith(pending, { user_id: 'user' }));
    expect(service.waitForGeneration).not.toHaveBeenCalled();
    expect(result.current.ai.candidate).toBeNull();
    expect(result.current.ai.prompt).toBe('');
  },
);

it('prevents duplicate generation requests and blocks identity-less or oversized input before IO', async () => {
  const start = deferred<WorkflowAiGenerationJob>();
  service.startGeneration.mockReturnValue(start.promise);
  const { result } = renderHook(() => useWorkflowCreation(context()));
  fill(result);
  await waitFor(() => expect(result.current.ai.source.data).not.toBeNull());
  let request!: Promise<void>;
  act(() => {
    request = result.current.ai.generate();
    void result.current.ai.generate();
  });
  expect(service.startGeneration).toHaveBeenCalledTimes(1);
  await act(async () => {
    start.resolve(pending);
    await request;
  });
  act(() => result.current.ai.changePrompt('x'.repeat(4001)));
  await waitFor(() => expect(result.current.ai.source.data).not.toBeNull());
  await act(async () => result.current.ai.generate());
  expect(service.startGeneration).toHaveBeenCalledTimes(1);
});

it('does not apply a cancelled preview through an old callback and does not create when generation fails', async () => {
  const { result } = renderHook(() => useWorkflowCreation(context()));
  fill(result);
  service.waitForGeneration.mockResolvedValueOnce({ ...pending, status: 'failed', error: '生成服务不可用' });
  await waitFor(() => expect(result.current.ai.source.data).not.toBeNull());
  await act(async () => result.current.ai.generate());
  expect(result.current.ai.error).toBe('生成服务不可用');
  expect(service.applyGeneration).not.toHaveBeenCalled();
  await waitFor(() => expect(result.current.ai.source.data).not.toBeNull());
  await act(async () => result.current.ai.generate());
  const oldApply = result.current.ai.apply;
  act(() => {
    result.current.ai.cancel();
    oldApply();
  });
  expect(result.current.ai.appliedSpec).toBeNull();
  expect(result.current.canSubmit).toBe(false);
  expect(service.createFromSpec).not.toHaveBeenCalled();
});

it('cancels generation on dialog close without accepting a later validated candidate', async () => {
  const validation = deferred<typeof spec>();
  service.applyGeneration.mockReturnValue(validation.promise);
  const close = jest.fn();
  const scope = context();
  const { unmount } = render(<WorkflowCreationDialog context={scope} onClose={close} />);
  fireEvent.click(screen.getByRole('button', { name: 'AI 生成' }));
  fireEvent.click(await screen.findByRole('button', { name: /来源工作流/ }));
  await waitFor(() => expect(service.readBaseline).toHaveBeenCalled());
  await act(async () => undefined);
  fireEvent.change(screen.getByRole('textbox', { name: '工作流 ID' }), { target: { value: 'new-flow' } });
  fireEvent.change(screen.getByRole('textbox', { name: '工作流名称' }), { target: { value: '新工作流' } });
  fireEvent.change(screen.getByLabelText('描述你的工作流需求'), { target: { value: '生成报告' } });
  fireEvent.click(screen.getByRole('button', { name: '生成 YAML 草稿' }));
  await waitFor(() => expect(service.applyGeneration).toHaveBeenCalled());
  const signal = service.applyGeneration.mock.calls[0][3]!;
  unmount();
  expect(signal.aborted).toBe(true);
  await act(async () => validation.resolve(spec));
  expect(service.createFromSpec).not.toHaveBeenCalled();
  expect(scope.navigate).not.toHaveBeenCalled();
});

it('requires an editable source context and never dispatches after a 403 source read', async () => {
  const { result } = renderHook(() => useWorkflowCreation(context()));
  act(() => result.current.update({ source: 'ai', workflowId: 'new-flow', title: '新工作流' }));
  act(() => result.current.ai.changePrompt('生成报告'));
  await act(async () => result.current.ai.generate());
  expect(service.readBaseline).not.toHaveBeenCalled();
  expect(service.startGeneration).not.toHaveBeenCalled();
  service.readBaseline.mockRejectedValueOnce(Object.assign(new Error('403：没有来源编辑权限'), { status: 403 }));
  act(() => result.current.update({ aiSourceWorkflowId: 'denied-source' }));
  await waitFor(() => expect(result.current.ai.source.error).toMatch(/403/));
  await act(async () => result.current.ai.generate());
  expect(service.readBaseline).toHaveBeenCalledWith('denied-source', { user_id: 'user' }, expect.any(AbortSignal));
  expect(service.startGeneration).not.toHaveBeenCalled();
  expect(result.current.ai.prompt).toBe('生成报告');
  expect(result.current.canSubmit).toBe(false);
});

it('fences a completed-but-unvalidated candidate when the AI source changes', async () => {
  const validation = deferred<typeof spec>();
  service.applyGeneration.mockReturnValueOnce(validation.promise);
  const { result } = renderHook(() => useWorkflowCreation(context()));
  fill(result);
  await waitFor(() => expect(result.current.ai.source.data).not.toBeNull());
  let request!: Promise<void>;
  act(() => {
    request = result.current.ai.generate();
  });
  await waitFor(() => expect(service.applyGeneration).toHaveBeenCalled());
  const signal = service.applyGeneration.mock.calls[0][3]!;
  act(() => result.current.update({ aiSourceWorkflowId: 'other-source' }));
  expect(signal.aborted).toBe(true);
  await act(async () => {
    validation.resolve(spec);
    await request;
  });
  expect(result.current.ai.candidate).toBeNull();
  expect(result.current.canSubmit).toBe(false);
  expect(service.createFromSpec).not.toHaveBeenCalled();
});

it('recovers the same AI receipt after lost acknowledgement instead of starting another generation', async () => {
  const draft: WorkflowAiDraftJob = {
    kind: 'modification',
    draftId: 'draft-1',
    receipt: 'receipt-1',
    workflowId: 'source',
    userId: 'user',
    requestId: '11111111-1111-4111-8111-111111111111',
    currentYaml: 'baseline',
    inputSpecDigest: 'a'.repeat(64),
    status: 'dispatch_uncertain',
  };
  service.startGeneration.mockRejectedValueOnce(new WorkflowAiRecoveryError(draft, new Error('lost response')));
  service.resumeGeneration.mockResolvedValueOnce(pending);
  const { result } = renderHook(() => useWorkflowCreation(context()));
  fill(result);
  await waitFor(() => expect(result.current.ai.source.data).not.toBeNull());
  await act(async () => result.current.ai.generate());
  expect(result.current.ai.recoveryJob).toBe(draft);
  expect(result.current.ai.candidate).toBeNull();
  await act(async () => result.current.ai.generate());
  expect(service.startGeneration).toHaveBeenCalledTimes(1);
  expect(service.resumeGeneration).toHaveBeenCalledWith(draft, { user_id: 'user' }, expect.any(AbortSignal));
  expect(result.current.ai.candidate).toEqual(spec);
  expect(service.createFromSpec).not.toHaveBeenCalled();
});

it('retries validation of a completed generation after 503 without dispatching or polling a new job', async () => {
  service.applyGeneration.mockRejectedValueOnce(Object.assign(new Error('validation unavailable'), { status: 503 }));
  const { result } = renderHook(() => useWorkflowCreation(context()));
  fill(result);
  await waitFor(() => expect(result.current.ai.source.data).not.toBeNull());
  await act(async () => result.current.ai.generate());
  expect(result.current.ai.job).toBe(completed);
  expect(result.current.ai.candidate).toBeNull();
  expect(result.current.canSubmit).toBe(false);
  await act(async () => result.current.ai.generate());
  expect(service.startGeneration).toHaveBeenCalledTimes(1);
  expect(service.waitForGeneration).toHaveBeenCalledTimes(1);
  expect(service.applyGeneration).toHaveBeenCalledTimes(2);
  expect(service.applyGeneration.mock.calls[1][0]).toBe(completed);
  expect(result.current.ai.candidate).toEqual(spec);
  expect(service.createFromSpec).not.toHaveBeenCalled();
});
