/** @jest-environment jsdom */
import { editorYaml } from '@/domain/taskEscortParity/editor';
import type { TaskEscortParityContext } from '@/domain/taskEscortParity/types';
import type { WorkflowAuthoringBaseline, WorkflowSaveResult } from '@/domain/taskEscortParity/workflowAuthoringTypes';
import { useEditorAuthoring } from '@/hooks/taskEscortParity/useEditorAuthoring';
import { useEditorAuthoringNavigation } from '@/hooks/taskEscortParity/useEditorAuthoringNavigation';
import { workflowAuthoringService } from '@/services/taskEscortParity/workflowAuthoringService';
import { act, renderHook, waitFor } from '@testing-library/react';
import { history } from '@umijs/max';
import { load } from 'js-yaml';

jest.mock('@umijs/max', () => ({ history: jest.requireActual('history').createMemoryHistory() }));
jest.mock('@/services/taskEscortParity/workflowAuthoringService', () => ({
  workflowAuthoringService: {
    readBaseline: jest.fn(),
    validateYaml: jest.fn(),
    save: jest.fn(),
    publish: jest.fn(),
  },
}));
const api = jest.mocked(workflowAuthoringService);
const spec = {
  id: 'wf',
  title: '工作流',
  version: 2,
  nodes: [{ id: 'a', executor: { type: 'approval' } }],
  extension: { keep: true },
};
const next = { ...spec, title: '未保存工作流' };
const context: TaskEscortParityContext = {
  scopeKey: 'alice:wf',
  userId: 'alice',
  workflowId: 'wf',
  ready: true,
  workflow: null,
  query: {},
  navigate: jest.fn(),
  refreshWorkflows: jest.fn(),
};
const baseline: WorkflowAuthoringBaseline = {
  workflowId: 'wf',
  userId: 'alice',
  packId: 'pack',
  rawPackId: 'pack',
  draftToken: 'base-token',
  spec,
  specJson: JSON.stringify(spec),
  sourceVersion: 'current',
  sourceCommit: 'a'.repeat(40),
  expectedSavedHead: 'a'.repeat(40),
};
function receipt(value = spec): WorkflowSaveResult {
  return {
    ok: true,
    saved: true,
    dbSaved: true,
    gitSaved: true,
    workflowId: 'wf',
    packId: 'pack',
    branch: 'saved/wf',
    commit: 'b'.repeat(40),
    spec: value,
    draftToken: 'next-token',
    requestId: 'save-request',
    baseline: {
      ...baseline,
      spec: value,
      specJson: JSON.stringify(value),
      draftToken: 'next-token',
      sourceCommit: 'b'.repeat(40),
      sourceVersion: 'draft',
      expectedSavedHead: 'b'.repeat(40),
    },
  };
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}
beforeEach(() => {
  jest.clearAllMocks();
  history.replace('/evolution-factory/task-escort?view=editor&workflowId=wf');
  api.readBaseline.mockResolvedValue(baseline);
  api.validateYaml.mockImplementation(async (yaml) => {
    const value = load(yaml) as typeof spec;
    return { valid: true, spec: value, normalizedSpec: value, issues: [], errors: [], warnings: [] };
  });
  api.save.mockImplementation(async (input) => receipt(input.spec as typeof spec));
});

it('requires the real read baseline, never fabricates CAS values from a fresh save-time fetch', async () => {
  const pending = deferred<WorkflowAuthoringBaseline>();
  api.readBaseline.mockReturnValue(pending.promise);
  const { result } = renderHook(() => useEditorAuthoring(context, spec, true));
  act(() => result.current.editYaml(editorYaml(next)));
  await act(async () => {
    await result.current.save();
  });
  expect(result.current.error).toContain('保存基线');
  expect(result.current.dirty).toBe(true);
  expect(api.save).not.toHaveBeenCalled();
  await act(async () => pending.resolve(baseline));
  expect(result.current.saveReady).toBe(true);
  await act(async () => {
    await result.current.save();
  });
  expect(api.save).toHaveBeenCalledWith(
    expect.objectContaining({ spec: next, baseline }),
    { user_id: 'alice' },
    expect.any(AbortSignal),
  );
  expect(result.current.spec.title).toBe('未保存工作流');
  expect(result.current.dirty).toBe(false);
  expect(result.current.persistence.baseline?.draftToken).toBe('next-token');
});

it('detects a baseline/read mismatch without discarding the YAML draft or allowing save', async () => {
  api.readBaseline.mockResolvedValue({ ...baseline, spec: { ...spec, title: '服务端新标题' } });
  const { result } = renderHook(() => useEditorAuthoring(context, spec, true));
  act(() => result.current.editYaml(editorYaml(next)));
  await waitFor(() => expect(result.current.persistence.error).toContain('基线已变化'));
  expect(result.current.saveReady).toBe(false);
  expect(result.current.yaml).toBe(editorYaml(next));
  expect(result.current.dirty).toBe(true);
});

it('keeps drafts dirty on save failure and prevents a write or AI apply from racing an active save', async () => {
  const pending = deferred<WorkflowSaveResult>();
  api.save.mockReturnValue(pending.promise);
  const { result } = renderHook(() => useEditorAuthoring(context, spec, true));
  await waitFor(() => expect(result.current.saveReady).toBe(true));
  act(() => result.current.editYaml(editorYaml(next)));
  let saving!: Promise<unknown>;
  act(() => {
    saving = result.current.save();
  });
  await waitFor(() => expect(api.save).toHaveBeenCalled());
  act(() => result.current.editYaml('id: unsafe'));
  expect(result.current.yaml).toBe(editorYaml(next));
  expect(() => result.current.replaceDraft(spec)).toThrow('当前不能应用草稿');
  await act(async () => {
    pending.resolve(receipt(next));
    await saving;
  });
  expect(result.current.dirty).toBe(false);
  expect(result.current.spec.title).toBe(next.title);
});

it('aborts saves on permission revocation and ignores a transport that still resolves', async () => {
  const pending = deferred<WorkflowSaveResult>();
  api.save.mockReturnValue(pending.promise);
  const { result, rerender } = renderHook(({ editable }) => useEditorAuthoring(context, spec, editable), {
    initialProps: { editable: true },
  });
  await waitFor(() => expect(result.current.saveReady).toBe(true));
  act(() => result.current.editYaml(editorYaml(next)));
  let saving!: Promise<unknown>;
  act(() => {
    saving = result.current.save();
  });
  await waitFor(() => expect(api.save).toHaveBeenCalled());
  const signal = api.save.mock.calls[0][2];
  rerender({ editable: false });
  expect(signal?.aborted).toBe(true);
  await act(async () => {
    pending.resolve(receipt(next));
    await saving;
  });
  expect(result.current.dirty).toBe(true);
  expect(result.current.notice).toBeNull();
  expect(api.publish).not.toHaveBeenCalled();
});

it('rejects identity/version edits and invalidates an earlier validation after text changes', async () => {
  const { result } = renderHook(() => useEditorAuthoring(context, spec, true));
  act(() => result.current.editYaml(editorYaml(next)));
  await act(async () => {
    await result.current.validate();
  });
  expect(result.current.validated).toBe(true);
  act(() => result.current.editYaml(editorYaml({ ...next, id: 'other' })));
  expect(result.current.validated).toBe(false);
  await act(async () => {
    await result.current.validate();
  });
  expect(result.current.error).toContain('标识或版本');
  expect(result.current.spec.id).toBe('wf');
  expect(api.save).not.toHaveBeenCalled();
});

it('removes the beforeunload blocker after the submitted YAML is actually saved', async () => {
  const { result } = renderHook(() => {
    const editor = useEditorAuthoring(context, spec, true);
    const navigation = useEditorAuthoringNavigation(editor.dirty || !!editor.phase);
    return { editor, navigation };
  });
  await waitFor(() => expect(result.current.editor.saveReady).toBe(true));
  act(() => result.current.editor.editYaml(editorYaml(next)));
  const dirty = new Event('beforeunload', { cancelable: true });
  window.dispatchEvent(dirty);
  expect(dirty.defaultPrevented).toBe(true);
  await act(async () => {
    await result.current.editor.save();
  });
  const clean = new Event('beforeunload', { cancelable: true });
  window.dispatchEvent(clean);
  expect(clean.defaultPrevented).toBe(false);
});

it.each(['push', 'replace'] as const)(
  'guards dirty %s exits but allows node/period transitions and restores after cancellation',
  (method) => {
    history.push('/evolution-factory/task-escort?view=editor&workflowId=wf&nodeId=a');
    const { result, unmount } = renderHook(() => useEditorAuthoringNavigation(true));
    act(() => history.push('/evolution-factory/task-escort?view=editor&workflowId=wf&nodeId=b&period=today'));
    expect(result.current.leave).toBeNull();
    const before = history.location;
    act(() => {
      history[method]('/outside');
    });
    expect(history.location).toBe(before);
    expect(result.current.leave).not.toBeNull();
    act(() => result.current.cancelLeave());
    expect(result.current.leave).toBeNull();
    act(() => history.push('/outside'));
    act(() => result.current.discardAndLeave());
    expect(history.location.pathname).toBe('/outside');
    unmount();
  },
);
