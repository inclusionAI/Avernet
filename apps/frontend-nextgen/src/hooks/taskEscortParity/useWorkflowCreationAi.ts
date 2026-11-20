import type { TaskEscortParityContext } from '@/domain/taskEscortParity/types';
import { authoringText, authoringWorkflowId, workflowAuthoringYaml } from '@/domain/taskEscortParity/workflowAuthoring';
import {
  WorkflowAiRecoveryError,
  type WorkflowAiDraftJob,
  type WorkflowAiGenerationJob,
  type WorkflowAuthoringSpec,
} from '@/domain/taskEscortParity/workflowAuthoringTypes';
import { workflowAuthoringService } from '@/services/taskEscortParity/workflowAuthoringService';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useWorkflowCreationAiContext } from './useWorkflowCreationAiContext';

interface AiState {
  token: object;
  phase: 'idle' | 'generating' | 'validating' | 'preview' | 'cancelled' | 'failed';
  job: WorkflowAiGenerationJob | null;
  candidate: WorkflowAuthoringSpec | null;
  appliedSpec: WorkflowAuthoringSpec | null;
  error: string;
  recoveryJob: WorkflowAiDraftJob | null;
}
const initial = (token: object): AiState => ({
  token,
  phase: 'idle',
  job: null,
  candidate: null,
  appliedSpec: null,
  error: '',
  recoveryJob: null,
});

/** Generation and validation never persist; only an explicit application unlocks creation. */
export function useWorkflowCreationAi(
  context: TaskEscortParityContext,
  enabled: boolean,
  workflowId: string,
  title: string,
  sourceId: string,
) {
  const allowed = enabled && (context.creationReady ?? context.ready) && !!context.userId?.trim();
  const identityToken = useMemo(() => ({}), [context.scopeKey, context.userId, allowed]);
  const source = useWorkflowCreationAiContext(context, enabled, sourceId);
  const token = useMemo(() => ({}), [identityToken, workflowId, title, sourceId, source.data]);
  const [promptState, setPromptState] = useState({ token: identityToken, value: '' });
  const prompt = promptState.token === identityToken ? promptState.value : '';
  const latest = useRef(token);
  latest.current = token;
  const [state, setState] = useState<AiState>(() => initial(token));
  const current = state.token === token ? state : initial(token);
  const request = useRef<{ token: object; controller: AbortController; job: WorkflowAiGenerationJob | null } | null>(
    null,
  );
  const candidateRef = useRef<{ token: object; spec: WorkflowAuthoringSpec } | null>(null);
  const recoveryRef = useRef<{ token: object; job: WorkflowAiDraftJob } | null>(null);
  const cancelJob = (job: WorkflowAiGenerationJob | WorkflowAiDraftJob) => {
    // Stop local acceptance immediately; remote cancellation is confirmed only by the service.
    void (async () => {
      try {
        if (job.kind === 'generation') await workflowAuthoringService.cancelGeneration(job, { user_id: job.userId });
        else await workflowAuthoringService.cancelModification(job, { user_id: job.userId });
      } catch {
        if (latest.current === token)
          setState((previous) =>
            previous.token === token && previous.phase === 'cancelled'
              ? { ...previous, error: '已停止接纳结果，但服务端未确认取消；上游任务可能仍在运行。' }
              : previous,
          );
      }
    })();
  };
  useEffect(
    () => () => {
      if (request.current?.token === token) {
        request.current.controller.abort();
        if (request.current.job) cancelJob(request.current.job);
        request.current = null;
      }
      if (recoveryRef.current?.token === token) {
        cancelJob(recoveryRef.current.job);
        recoveryRef.current = null;
      }
    },
    [token],
  );
  const cancel = () => {
    candidateRef.current = null;
    const active = request.current;
    if (active?.token === token) {
      active.controller.abort();
      if (active.job) cancelJob(active.job);
      request.current = null;
    }
    if (recoveryRef.current?.token === token) {
      cancelJob(recoveryRef.current.job);
      recoveryRef.current = null;
    }
    setState({ ...current, phase: 'cancelled', candidate: null, appliedSpec: null, recoveryJob: null, error: '' });
  };
  const changePrompt = (prompt: string) => {
    cancel();
    setPromptState({ token: identityToken, value: prompt });
    setState(initial(token));
  };
  const busy = current.phase === 'generating' || current.phase === 'validating';
  const validationRetry = current.phase === 'failed' && current.job?.status === 'completed';
  const generate = async () => {
    if (
      !allowed ||
      !context.userId ||
      !workflowId ||
      !title ||
      !sourceId ||
      !source.data ||
      source.loading ||
      source.error ||
      !prompt.trim() ||
      prompt.length > 4000 ||
      request.current?.token === token ||
      latest.current !== token
    )
      return;
    const operation = { token, controller: new AbortController(), job: null as WorkflowAiGenerationJob | null };
    request.current = operation;
    candidateRef.current = null;
    const { signal } = operation.controller;
    const active = () => latest.current === token && request.current === operation && !signal.aborted;
    const identity = { user_id: context.userId };
    setState({
      ...initial(token),
      phase: validationRetry ? 'validating' : 'generating',
      job: validationRetry ? current.job : null,
    });
    try {
      authoringWorkflowId(workflowId);
      authoringText(title, '工作流名称');
      // A completed generation is still the same receipt when validation is unavailable.
      const submitted =
        validationRetry && current.job
          ? current.job
          : current.recoveryJob
          ? await workflowAuthoringService.resumeGeneration(current.recoveryJob, identity, signal)
          : await workflowAuthoringService.startGeneration(
              {
                workflowId: sourceId,
                currentYaml: workflowAuthoringYaml(source.data),
                prompt: prompt.trim(),
              },
              identity,
              signal,
            );
      if (!active()) {
        cancelJob(submitted);
        return;
      }
      recoveryRef.current = null;
      operation.job = submitted;
      setState((previous) => ({ ...previous, job: submitted }));
      const job =
        submitted.status === 'completed'
          ? submitted
          : await workflowAuthoringService.waitForGeneration(submitted, identity, {
              signal,
              onUpdate: (progress) => {
                if (active()) setState((previous) => ({ ...previous, job: progress }));
              },
            });
      if (!active()) return;
      operation.job = job;
      if (job.status !== 'completed') throw new Error(job.error || 'AI 生成未完成，请重试');
      setState((previous) => ({ ...previous, phase: 'validating', job }));
      const candidate = await workflowAuthoringService.applyGeneration(job, { workflowId, title }, identity, signal);
      if (!active()) return;
      if (candidate.id !== workflowId || candidate.title !== title) throw new Error('AI 草稿不属于目标工作流');
      candidateRef.current = { token, spec: candidate };
      setState((previous) => ({ ...previous, phase: 'preview', candidate, appliedSpec: null }));
    } catch (error) {
      const recovery = error instanceof WorkflowAiRecoveryError ? error.job : null;
      if (active()) {
        recoveryRef.current = recovery ? { token, job: recovery } : null;
        setState((previous) => ({
          ...previous,
          phase: 'failed',
          recoveryJob: recovery,
          error: error instanceof Error ? error.message : '生成草稿失败，请重试',
        }));
      } else if (recovery) cancelJob(recovery);
    } finally {
      if (request.current === operation) request.current = null;
    }
  };
  const apply = () => {
    if (
      allowed &&
      latest.current === token &&
      !busy &&
      current.phase === 'preview' &&
      current.candidate &&
      candidateRef.current?.token === token &&
      candidateRef.current.spec === current.candidate
    )
      setState({ ...current, appliedSpec: current.candidate });
  };
  return { ...current, prompt, source, busy, validationRetry, generate, cancel, changePrompt, apply };
}
