/** @jest-environment jsdom */
import { editorYaml } from '@/domain/taskEscortParity/editor';
import type { TaskEscortParityContext } from '@/domain/taskEscortParity/types';
import {
  WorkflowAiRecoveryError,
  type WorkflowAiDraftJob,
  type WorkflowAuthoringBaseline,
  type WorkflowPublishResult,
  type WorkflowPublishReview,
  type WorkflowSaveResult,
} from '@/domain/taskEscortParity/workflowAuthoringTypes';
import { useEditorAiDraft } from '@/hooks/taskEscortParity/useEditorAiDraft';
import { useEditorAuthoring } from '@/hooks/taskEscortParity/useEditorAuthoring';
import { useEditorAuthoringPublish } from '@/hooks/taskEscortParity/useEditorAuthoringPublish';
import { workflowAuthoringService } from '@/services/taskEscortParity/workflowAuthoringService';
import '@testing-library/jest-dom';
import { act, render, renderHook, screen, waitFor } from '@testing-library/react';
import { load } from 'js-yaml';
import { webcrypto } from 'node:crypto';
import { TextEncoder } from 'node:util';
import { useState } from 'react';

jest.mock('@/services/taskEscortParity/workflowAuthoringService', () => ({
  workflowAuthoringService: {
    readBaseline: jest.fn(),
    readPublishReview: jest.fn(),
    validateYaml: jest.fn(),
    save: jest.fn(),
    publish: jest.fn(),
    capabilities: jest.fn(),
    startModification: jest.fn(),
    waitForModification: jest.fn(),
    cancelModification: jest.fn(),
    applyModification: jest.fn(),
  },
}));
const api = jest.mocked(workflowAuthoringService);
const spec = {
  id: 'wf',
  title: '真实工作流',
  version: 1,
  nodes: [{ id: 'a', executor: { type: 'approval' } }],
  extension: { keep: true },
};
const changed = { ...spec, title: 'AI 候选标题' };
const yaml = editorYaml(spec);
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
  draftToken: 'baseline',
  spec,
  specJson: JSON.stringify(spec),
  sourceVersion: 'current',
  sourceCommit: 'a'.repeat(40),
  expectedSavedHead: 'a'.repeat(40),
};
const saved: WorkflowSaveResult = {
  ok: true,
  saved: true,
  dbSaved: true,
  gitSaved: true,
  workflowId: 'wf',
  packId: 'pack',
  branch: 'saved/wf',
  commit: 'b'.repeat(40),
  spec,
  draftToken: 'saved-draft',
  requestId: 'save-request',
  baseline: {
    ...baseline,
    draftToken: 'saved-draft',
    sourceVersion: 'draft',
    sourceCommit: 'b'.repeat(40),
    expectedSavedHead: 'b'.repeat(40),
  },
};
const review: WorkflowPublishReview = {
  workflowId: 'wf',
  packId: 'pack',
  candidateCommit: saved.commit,
  draftToken: saved.draftToken,
  expectedActiveCommit: baseline.sourceCommit,
  changes: [{ path: 'workflow.yaml', operation: 'replace', before: yaml, after: yaml }],
};
const published: WorkflowPublishResult = {
  ok: true,
  deployed: true,
  workflowId: 'wf',
  packId: 'pack',
  deployNumber: 2,
  version: 1,
  tagName: 'deploy/wf/#2',
};
function job(status: WorkflowAiDraftJob['status']): WorkflowAiDraftJob {
  return {
    kind: 'modification',
    draftId: 'signed-draft',
    receipt: 'signed-recovery-receipt',
    workflowId: 'wf',
    userId: 'alice',
    requestId: '10000000-0000-4000-8000-000000000001',
    currentYaml: yaml,
    inputSpecDigest: 'd'.repeat(64),
    status,
    ...(status === 'completed' ? { result: { yaml: editorYaml(changed), summary: '待审阅修改' } } : {}),
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
  Object.defineProperty(globalThis, 'crypto', { value: webcrypto, configurable: true });
  Object.defineProperty(globalThis, 'TextEncoder', { value: TextEncoder, configurable: true });
  api.readBaseline.mockResolvedValue(baseline);
  api.readPublishReview.mockResolvedValue(review);
  api.save.mockResolvedValue(saved);
  api.publish.mockResolvedValue(published);
  api.capabilities.mockResolvedValue({ available: true });
  api.validateYaml.mockImplementation(async (text) => {
    const value = load(text) as typeof spec;
    return { valid: true, issues: [], errors: [], warnings: [], spec: value, normalizedSpec: value };
  });
  api.cancelModification.mockResolvedValue(job('cancelled'));
});

it('keeps the actual authoring session mounted through save → delayed file review; only successful publication refreshes the workspace', async () => {
  const pendingReview = deferred<WorkflowPublishReview>(),
    pendingPublish = deferred<WorkflowPublishResult>();
  api.readPublishReview.mockReturnValue(pendingReview.promise);
  api.publish.mockReturnValue(pendingPublish.promise);
  let latest!: {
    editor: ReturnType<typeof useEditorAuthoring>;
    publication: ReturnType<typeof useEditorAuthoringPublish>;
  };
  const refresh = jest.fn();
  function Editor({ boundContext }: { boundContext: TaskEscortParityContext }) {
    const editor = useEditorAuthoring(boundContext, spec, true);
    const publication = useEditorAuthoringPublish(boundContext, editor.yaml, true, editor.save);
    latest = { editor, publication };
    return <div data-testid="mounted-authoring-session">{publication.candidate ? '文件差异已就绪' : '等待审阅'}</div>;
  }
  function Workspace() {
    const [loading, setLoading] = useState(false);
    return loading ? (
      <div>工作流列表刷新中</div>
    ) : (
      <Editor
        boundContext={{
          ...context,
          refreshWorkflows: () => {
            refresh();
            setLoading(true);
          },
        }}
      />
    );
  }
  render(<Workspace />);
  await waitFor(() => expect(latest.editor.saveReady).toBe(true));
  let preparing!: Promise<void>;
  act(() => {
    preparing = latest.publication.prepare('验证发布链路');
  });
  await waitFor(() => expect(api.readPublishReview).toHaveBeenCalledTimes(1));
  const reviewSignal = api.readPublishReview.mock.calls[0][2];
  expect(refresh).not.toHaveBeenCalled();
  expect(reviewSignal?.aborted).toBe(false);
  expect(screen.getByTestId('mounted-authoring-session')).toBeInTheDocument();
  await act(async () => {
    pendingReview.resolve(review);
    await preparing;
  });
  expect(latest.publication.candidate?.receipt).toBe(saved);
  expect(latest.publication.candidate?.review).toBe(review);
  expect(api.publish).not.toHaveBeenCalled();
  expect(refresh).not.toHaveBeenCalled();
  let publishing!: Promise<void>;
  act(() => {
    publishing = latest.publication.publish(true);
  });
  await waitFor(() => expect(api.publish).toHaveBeenCalledTimes(1));
  expect(refresh).not.toHaveBeenCalled();
  expect(screen.getByTestId('mounted-authoring-session')).toBeInTheDocument();
  await act(async () => {
    pendingPublish.resolve(published);
    await publishing;
  });
  expect(refresh).toHaveBeenCalledTimes(1);
  expect(screen.getByText('工作流列表刷新中')).toBeInTheDocument();
});

it('does not refresh or destroy the saved draft when file-review loading fails', async () => {
  api.readPublishReview.mockRejectedValueOnce(new Error('读取差异失败'));
  const { result } = renderHook(() => {
    const editor = useEditorAuthoring(context, spec, true);
    const publication = useEditorAuthoringPublish(context, editor.yaml, true, editor.save);
    return { editor, publication };
  });
  await waitFor(() => expect(result.current.editor.saveReady).toBe(true));
  await act(async () => {
    await result.current.publication.prepare();
  });
  expect(result.current.publication.error).toContain('工作流已保存，读取发布差异失败');
  expect(result.current.editor.dirty).toBe(false);
  expect(result.current.editor.yaml).toBe(yaml);
  expect(context.refreshWorkflows).not.toHaveBeenCalled();
  expect(api.publish).not.toHaveBeenCalled();
});

it.each(['submit', 'poll'] as const)(
  'retains the signed %s recovery job and retries by polling the same receipt, never dispatching a new UUID',
  async (stage) => {
    const uncertain = job('dispatch_uncertain'),
      running = job('running'),
      completed = job('completed');
    if (stage === 'submit')
      api.startModification.mockRejectedValueOnce(new WorkflowAiRecoveryError(uncertain, new Error('submit ack lost')));
    else {
      api.startModification.mockResolvedValueOnce(running);
      api.waitForModification.mockRejectedValueOnce(
        new WorkflowAiRecoveryError(uncertain, new Error('poll disconnected')),
      );
    }
    api.waitForModification.mockResolvedValueOnce(completed);
    const onApply = jest.fn();
    const { result } = renderHook(() => useEditorAiDraft(context, yaml, spec, false, onApply));
    await waitFor(() => expect(result.current.capability?.available).toBe(true));
    act(() => result.current.changePrompt('修改审核流程'));
    await act(async () => {
      await result.current.generate();
    });
    expect(result.current.job).toBe(uncertain);
    expect(result.current.error).toContain('同一草稿恢复轮询');
    expect(result.current.candidate).toBeNull();
    expect(api.startModification).toHaveBeenCalledTimes(1);
    expect(api.cancelModification).not.toHaveBeenCalled();
    await act(async () => {
      await result.current.generate();
    });
    expect(api.startModification).toHaveBeenCalledTimes(1);
    expect(api.waitForModification).toHaveBeenLastCalledWith(
      uncertain,
      { user_id: 'alice' },
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
    expect(api.waitForModification.mock.calls.at(-1)?.[0].receipt).toBe(uncertain.receipt);
    expect(api.cancelModification).not.toHaveBeenCalled();
    expect(result.current.candidate?.job).toBe(completed);
    expect(onApply).not.toHaveBeenCalled();
    expect(api.save).not.toHaveBeenCalled();
    expect(api.publish).not.toHaveBeenCalled();
  },
);

it('keeps recovery available across repeated polling failures and cancels the same receipt on unmount', async () => {
  const uncertain = job('dispatch_uncertain');
  api.startModification.mockRejectedValueOnce(new WorkflowAiRecoveryError(uncertain, new Error('ack lost')));
  api.waitForModification.mockRejectedValue(new WorkflowAiRecoveryError(uncertain, new Error('still disconnected')));
  const { result, unmount } = renderHook(() => useEditorAiDraft(context, yaml, spec, false, jest.fn()));
  await waitFor(() => expect(result.current.capability?.available).toBe(true));
  act(() => result.current.changePrompt('修改审核流程'));
  await act(async () => {
    await result.current.generate();
    await result.current.generate();
    await result.current.generate();
  });
  expect(api.startModification).toHaveBeenCalledTimes(1);
  expect(result.current.job).toBe(uncertain);
  unmount();
  await waitFor(() => expect(api.cancelModification).toHaveBeenCalledWith(uncertain, { user_id: 'alice' }));
});

it('recovers against the original YAML but makes the candidate stale if the user edited while polling was disconnected', async () => {
  const uncertain = job('dispatch_uncertain');
  api.startModification.mockRejectedValueOnce(new WorkflowAiRecoveryError(uncertain, new Error('ack lost')));
  api.waitForModification.mockResolvedValueOnce(job('completed'));
  const { result, rerender } = renderHook(({ text }) => useEditorAiDraft(context, text, spec, false, jest.fn()), {
    initialProps: { text: yaml },
  });
  await waitFor(() => expect(result.current.capability?.available).toBe(true));
  act(() => result.current.changePrompt('修改审核流程'));
  await act(async () => {
    await result.current.generate();
  });
  rerender({ text: editorYaml({ ...spec, title: '后续手工编辑' }) });
  await act(async () => {
    await result.current.generate();
  });
  expect(api.startModification).toHaveBeenCalledTimes(1);
  expect(result.current.candidate?.yaml).toBe(yaml);
  expect(result.current.stale).toBe(true);
  await act(async () => {
    await result.current.validate();
    await result.current.apply();
  });
  expect(api.applyModification).not.toHaveBeenCalled();
});
