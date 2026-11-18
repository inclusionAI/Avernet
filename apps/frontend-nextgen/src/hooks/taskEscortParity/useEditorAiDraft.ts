import type { EditorSpec } from '@/domain/taskEscortParity/editorTypes';
import type { TaskEscortParityContext } from '@/domain/taskEscortParity/types';
import {
  parseWorkflowAuthoringYaml,
  restoreWorkflowAuthoringLayout,
  workflowAuthoringSignature,
} from '@/domain/taskEscortParity/workflowAuthoring';
import { WorkflowAiRecoveryError } from '@/domain/taskEscortParity/workflowAuthoringTypes';
import { workflowAuthoringService } from '@/services/taskEscortParity/workflowAuthoringService';
import { useEffect, useRef, useState } from 'react';
import { useEditorAiCapability } from './useEditorAiCapability';
import { assertEditorDraft, editorIssueText } from './useEditorAuthoring';
type Job = Awaited<ReturnType<typeof workflowAuthoringService.startModification>>;
interface Candidate {
  job: Job;
  yaml: string;
  base: EditorSpec;
  spec?: EditorSpec;
  warnings: string[];
}
const errorText = (cause: unknown) => (cause instanceof Error ? cause.message : 'AI 草稿操作失败，请重试');
const running = (job: Job) => ['pending', 'running', 'dispatch_uncertain'].includes(job.status);
export function useEditorAiDraft(
  context: TaskEscortParityContext,
  yaml: string,
  original: EditorSpec,
  blocked: boolean,
  onApply: (spec: EditorSpec) => void,
) {
  const [prompt, setPrompt] = useState('');
  const [job, setJob] = useState<Job | null>(null);
  const [candidate, setCandidate] = useState<Candidate | null>(null);
  const [phase, setPhase] = useState<'generate' | 'validate' | 'apply' | 'cancel' | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const capability = useEditorAiCapability(original.id, context.userId);
  const sequence = useRef(0);
  const controller = useRef<AbortController | null>(null);
  const remote = useRef<Job | null>(null);
  const live = useRef({ yaml, blocked });
  live.current = { yaml, blocked };
  const userId = context.userId;
  function stop() {
    sequence.current += 1;
    controller.current?.abort();
    controller.current = null;
    const previous = remote.current;
    remote.current = null;
    return previous;
  }
  async function cancelRemote(previous: Job | null) {
    if (previous && running(previous) && userId)
      await workflowAuthoringService.cancelModification(previous, { user_id: userId });
  }
  useEffect(
    () => () => {
      void cancelRemote(stop()).catch(() => undefined);
    },
    [userId, original.id],
  );
  const stale = !!candidate && candidate.yaml !== yaml;
  function changePrompt(value: string) {
    if (value === prompt) return;
    void cancelRemote(stop()).catch(() => undefined);
    setPrompt(value);
    setCandidate(null);
    setJob(null);
    setPhase(null);
    setError(null);
    setNotice(null);
  }
  async function generate() {
    if (controller.current || blocked || !userId || !capability?.available || !prompt.trim()) return;
    const recovery = remote.current && running(remote.current) ? remote.current : null;
    stop();
    remote.current = recovery;
    setCandidate(null);
    setError(null);
    setNotice(null);
    setPhase('generate');
    const id = sequence.current,
      pending = new AbortController(),
      snapshot = recovery?.currentYaml ?? yaml;
    controller.current = pending;
    const current = () => id === sequence.current && !pending.signal.aborted;
    try {
      const validation = await workflowAuthoringService.validateYaml(snapshot, { user_id: userId }, pending.signal);
      if (!current()) return;
      if (!validation.valid)
        throw new Error(editorIssueText(validation.errors).join('；') || '当前 YAML 无效，请先修复草稿');
      assertEditorDraft(validation.spec, original);
      const base = assertEditorDraft(parseWorkflowAuthoringYaml(snapshot), original);
      const started =
        recovery ??
        (await workflowAuthoringService.startModification(
          original.id,
          { requestId: crypto.randomUUID(), prompt: prompt.trim(), currentYaml: snapshot },
          { user_id: userId },
          pending.signal,
        ));
      if (!current()) {
        await cancelRemote(started);
        return;
      }
      remote.current = started;
      setJob(started);
      const done = await workflowAuthoringService.waitForModification(
        started,
        { user_id: userId },
        {
          signal: pending.signal,
          onUpdate: (update) => {
            if (current()) {
              remote.current = update;
              setJob(update);
            }
          },
        },
      );
      if (!current()) return;
      remote.current = done;
      setJob(done);
      if (done.status === 'failed') throw new Error(done.error || 'AI 草稿生成失败');
      if (done.status === 'cancelled') {
        setNotice('AI 任务已取消，未更改工作流。');
        return;
      }
      if (done.status !== 'completed' || !done.result) throw new Error('AI 任务未返回可预览的候选结果');
      setCandidate({ job: done, yaml: snapshot, base, warnings: [] });
      setNotice('草稿已生成，请先预览并校验，再明确应用。');
    } catch (cause) {
      if (current()) {
        if (cause instanceof WorkflowAiRecoveryError) {
          remote.current = cause.job;
          setJob(cause.job);
        }
        setError(errorText(cause));
      }
    } finally {
      if (current()) {
        controller.current = null;
        setPhase(null);
      }
    }
  }
  async function validate() {
    if (!candidate || !candidate.job.result || blocked || stale || controller.current || !userId) return;
    const pending = new AbortController(),
      id = sequence.current;
    controller.current = pending;
    setPhase('validate');
    setCandidate({ ...candidate, spec: undefined });
    setError(null);
    try {
      const result = await workflowAuthoringService.validateYaml(
        candidate.job.result.yaml,
        { user_id: userId },
        pending.signal,
      );
      if (
        id !== sequence.current ||
        pending.signal.aborted ||
        live.current.yaml !== candidate.yaml ||
        live.current.blocked
      )
        return;
      if (!result.valid) throw new Error(editorIssueText(result.errors).join('；') || 'AI 候选 YAML 校验未通过');
      const spec = restoreWorkflowAuthoringLayout(assertEditorDraft(result.spec, original), candidate.base);
      setCandidate({ ...candidate, spec, warnings: editorIssueText(result.warnings) });
      setNotice('候选校验通过。请审阅变更后应用，应用不会保存或发布。');
    } catch (cause) {
      if (id === sequence.current && !pending.signal.aborted) setError(errorText(cause));
    } finally {
      if (id === sequence.current) {
        controller.current = null;
        setPhase(null);
      }
    }
  }
  async function apply() {
    if (!candidate?.spec || blocked || stale || controller.current || !userId) return;
    const pending = new AbortController(),
      id = sequence.current;
    controller.current = pending;
    setPhase('apply');
    setError(null);
    try {
      const spec = await workflowAuthoringService.applyModification(
        candidate.job,
        candidate.base,
        { user_id: userId },
        pending.signal,
      );
      if (
        id !== sequence.current ||
        pending.signal.aborted ||
        live.current.blocked ||
        live.current.yaml !== candidate.yaml
      )
        return;
      if (workflowAuthoringSignature(spec) !== workflowAuthoringSignature(candidate.spec))
        throw new Error('服务端校验结果已变化，请重新校验并审阅候选');
      onApply(assertEditorDraft(spec, original));
      setCandidate(null);
      setNotice('草稿已应用到画布与 YAML，尚未保存或发布。');
    } catch (cause) {
      if (id === sequence.current && !pending.signal.aborted) setError(errorText(cause));
    } finally {
      if (id === sequence.current) {
        controller.current = null;
        setPhase(null);
      }
    }
  }
  async function cancel() {
    const previous = stop(),
      id = sequence.current;
    setPhase('cancel');
    setCandidate(null);
    setError(null);
    setNotice(null);
    try {
      await cancelRemote(previous);
      if (id === sequence.current) {
        setJob(null);
        setNotice(previous ? 'AI 任务已取消，未更改工作流。' : '已停止当前请求，未更改工作流；尚未确认远端任务取消。');
      }
    } catch (cause) {
      if (id === sequence.current) setError(`已停止等待；远端取消失败：${errorText(cause)}`);
    } finally {
      if (id === sequence.current) setPhase(null);
    }
  }
  return {
    prompt,
    job,
    candidate,
    phase,
    error,
    notice,
    capability,
    stale,
    changePrompt,
    generate,
    validate,
    apply,
    cancel,
  };
}
export type EditorAiDraft = ReturnType<typeof useEditorAiDraft>;
