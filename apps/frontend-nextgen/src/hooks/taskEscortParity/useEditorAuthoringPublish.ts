import type { TaskEscortParityContext } from '@/domain/taskEscortParity/types';
import type { WorkflowPublishReview, WorkflowSaveResult } from '@/domain/taskEscortParity/workflowAuthoringTypes';
import { workflowAuthoringService } from '@/services/taskEscortParity/workflowAuthoringService';
import { useEffect, useRef, useState } from 'react';

interface Publication {
  receipt: WorkflowSaveResult;
  review: WorkflowPublishReview;
  yaml: string;
  note?: string;
}

/** Save → server file diff → explicit review → commit-bound publication. */
export function useEditorAuthoringPublish(
  context: TaskEscortParityContext,
  yaml: string,
  canEdit: boolean,
  save: (note?: string) => Promise<WorkflowSaveResult | null>,
) {
  const [candidate, setCandidate] = useState<Publication | null>(null);
  const [phase, setPhase] = useState<'prepare' | 'publish' | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const active = useRef<AbortController | null>(null);
  const live = useRef({ yaml, canEdit });
  live.current = { yaml, canEdit };
  useEffect(
    () => () => {
      active.current?.abort();
      active.current = null;
    },
    [],
  );
  useEffect(() => {
    if (!canEdit) {
      active.current?.abort();
      active.current = null;
      setCandidate(null);
      setPhase(null);
    }
  }, [canEdit]);
  function begin(next: 'prepare' | 'publish') {
    if (!canEdit || !context.userId || active.current) return null;
    const pending = new AbortController();
    active.current = pending;
    setPhase(next);
    setError(null);
    setNotice(null);
    return { pending, params: { user_id: context.userId }, snapshot: live.current.yaml };
  }
  const current = (pending: AbortController, snapshot: string) =>
    active.current === pending && !pending.signal.aborted && live.current.canEdit && live.current.yaml === snapshot;
  function finish(pending: AbortController) {
    if (active.current === pending) {
      active.current = null;
      setPhase(null);
    }
  }
  async function prepare(note?: string) {
    const request = begin('prepare');
    if (!request) return;
    const { pending, params, snapshot } = request;
    setCandidate(null);
    let saved = false;
    try {
      const receipt = await save(note);
      if (!receipt || !current(pending, snapshot)) return;
      saved = true;
      const review = await workflowAuthoringService.readPublishReview(receipt, params, pending.signal);
      if (!current(pending, snapshot)) return;
      setCandidate({ receipt, review, yaml: snapshot, note });
      setNotice('工作流已保存，请审阅完整文件差异后确认发布。');
    } catch (cause) {
      if (current(pending, snapshot))
        setError(
          `${saved ? '工作流已保存，读取发布差异失败：' : ''}${
            cause instanceof Error ? cause.message : '准备发布失败'
          }`,
        );
    } finally {
      finish(pending);
    }
  }
  async function publish(confirmed: boolean) {
    if (!confirmed || !candidate || candidate.yaml !== yaml) return;
    const request = begin('publish');
    if (!request) return;
    const { pending, params, snapshot } = request;
    try {
      await workflowAuthoringService.publish(
        {
          workflowId: context.workflowId,
          receipt: candidate.receipt,
          review: candidate.review,
          confirmedFileDiff: true,
          note: candidate.note,
        },
        params,
        pending.signal,
      );
      if (!current(pending, snapshot)) return;
      setCandidate(null);
      setNotice('工作流已保存并发布。');
      context.refreshWorkflows();
    } catch (cause) {
      if (current(pending, snapshot))
        setError(`工作流已保存，发布失败：${cause instanceof Error ? cause.message : '请核对发布状态'}`);
    } finally {
      finish(pending);
    }
  }
  function close() {
    if (!active.current) {
      setCandidate(null);
      setError(null);
    }
  }
  return { candidate, phase, error, notice, stale: !!candidate && candidate.yaml !== yaml, prepare, publish, close };
}
export type EditorAuthoringPublish = ReturnType<typeof useEditorAuthoringPublish>;
