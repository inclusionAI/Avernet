import type { EditorSpec } from '@/domain/taskEscortParity/editorTypes';
import type { TaskEscortParityContext } from '@/domain/taskEscortParity/types';
import { workflowAuthoringSignature } from '@/domain/taskEscortParity/workflowAuthoring';
import { workflowAuthoringService } from '@/services/taskEscortParity/workflowAuthoringService';
import { useEffect, useRef, useState } from 'react';

type Baseline = Awaited<ReturnType<typeof workflowAuthoringService.readBaseline>>;

/** The CAS baseline must be the one actually read before the user starts saving. */
export function useEditorAuthoringBaseline(context: TaskEscortParityContext, original: EditorSpec, canEdit: boolean) {
  const [baseline, setBaseline] = useState<Baseline | null>(null);
  const [loading, setLoading] = useState(canEdit);
  const [error, setError] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);
  const latest = useRef(original);
  latest.current = original;
  useEffect(() => {
    const pending = new AbortController();
    setBaseline(null);
    setError(null);
    setLoading(canEdit);
    if (canEdit && context.userId)
      void workflowAuthoringService
        .readBaseline(original.id, { user_id: context.userId }, pending.signal)
        .then((value) => {
          if (pending.signal.aborted) return;
          if (workflowAuthoringSignature(value.spec) !== workflowAuthoringSignature(latest.current))
            throw new Error('工作流保存基线已变化，请先刷新规格再保存；当前草稿未丢弃。');
          setBaseline(value);
        })
        .catch((cause: unknown) => {
          if (!pending.signal.aborted) setError(cause instanceof Error ? cause.message : '无法读取工作流保存基线');
        })
        .finally(() => {
          if (!pending.signal.aborted) setLoading(false);
        });
    return () => pending.abort();
  }, [context.userId, original.id, canEdit, revision]);
  return { baseline, setBaseline, loading, error, retry: () => setRevision((value) => value + 1) };
}
