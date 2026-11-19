import type { TaskEscortParityContext } from '@/domain/taskEscortParity/types';
import type { WorkflowAuthoringSpec } from '@/domain/taskEscortParity/workflowAuthoringTypes';
import { workflowAuthoringService } from '@/services/taskEscortParity/workflowAuthoringService';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useWorkflowCreationAi } from './useWorkflowCreationAi';

export type WorkflowCreationSource = 'blank' | 'existing' | 'ai';
interface CreationForm {
  source: WorkflowCreationSource;
  workflowId: string;
  title: string;
  sourceWorkflowId: string;
  aiSourceWorkflowId: string;
}
const emptyForm: CreationForm = {
  source: 'blank',
  workflowId: '',
  title: '',
  sourceWorkflowId: '',
  aiSourceWorkflowId: '',
};

export function workflowCreationIdError(value: string): string | null {
  const id = value.trim();
  if (!id) return '请填写工作流 ID';
  if (/\s/.test(id)) return '工作流 ID 不能包含空格';
  if (
    !/^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$/.test(id) ||
    id.includes('..') ||
    id.endsWith('.') ||
    id.endsWith('.lock') ||
    /^__.+__$/.test(id)
  )
    return '工作流 ID 需以字母、数字或下划线开头，只能包含字母、数字、下划线、点、连字符，不能使用保留格式或以 . / .lock 结尾';
  return null;
}
export function workflowCreationError(error: unknown): string {
  const status = error && typeof error === 'object' && 'status' in error ? error.status : undefined;
  if (status === 409) return '工作流 ID 已存在或源快照发生冲突（409），请检查 ID 或刷新来源后重试。';
  if (status === 403) return '当前身份没有创建或访问来源的权限（403），请检查权限后重试。';
  return error instanceof Error ? error.message : '创建工作流失败，请重试';
}

/** Creation has its own identity gate; an empty workflow list is a valid starting point. */
export function useWorkflowCreation(context: TaskEscortParityContext, onCreated?: () => void, initialSource: WorkflowCreationSource = 'blank') {
  const allowed = (context.creationReady ?? context.ready) && !!context.userId?.trim();
  const token = useMemo(() => ({}), [context.scopeKey, context.userId, allowed]);
  const latest = useRef(token);
  latest.current = token;
  const initialForm = { ...emptyForm, source: initialSource };
  const [state, setState] = useState({ token, form: initialForm, error: '', busy: false, uncertain: false });
  const current = state.token === token ? state : { token, form: initialForm, error: '', busy: false, uncertain: false };
  const { form } = current;
  const request = useRef<{ token: object; controller: AbortController } | null>(null);
  useEffect(
    () => () => {
      if (request.current?.token === token) {
        request.current.controller.abort();
        request.current = null;
      }
    },
    [token],
  );
  const ai = useWorkflowCreationAi(
    context,
    form.source === 'ai',
    form.workflowId.trim(),
    form.title.trim(),
    form.aiSourceWorkflowId,
  );
  const update = (patch: Partial<CreationForm>) => {
    if (latest.current !== token || request.current?.token === token || current.uncertain) return;
    setState({ token, form: { ...form, ...patch }, busy: false, error: '', uncertain: false });
  };
  const idError = workflowCreationIdError(form.workflowId);
  const validFields = !idError && !!form.title.trim() && !/[\u0000-\u001f\u007f]/.test(form.title);
  const canSubmit =
    allowed &&
    !current.uncertain &&
    validFields &&
    !current.busy &&
    !ai.busy &&
    (form.source === 'blank' ||
      (form.source === 'existing' && !!form.sourceWorkflowId && form.sourceWorkflowId !== form.workflowId.trim()) ||
      (form.source === 'ai' && !!ai.appliedSpec));
  const submit = async (): Promise<void> => {
    if (!canSubmit || request.current?.token === token || latest.current !== token || !context.userId) return;
    const controller = new AbortController();
    const operation = { token, controller };
    request.current = operation;
    setState({ token, form, busy: true, error: '', uncertain: false });
    const active = () => latest.current === token && request.current === operation && !controller.signal.aborted;
    const input = { workflowId: form.workflowId.trim(), title: form.title.trim() };
    const identity = { user_id: context.userId };
    try {
      let createdId: string;
      if (form.source === 'existing') {
        const result = await workflowAuthoringService.createFromExisting(
          { ...input, sourceWorkflowId: form.sourceWorkflowId },
          identity,
          controller.signal,
        );
        createdId = result.targetWorkflowId;
      } else {
        const spec: WorkflowAuthoringSpec =
          form.source === 'ai' && ai.appliedSpec
            ? await workflowAuthoringService.createFromSpec(
                { ...input, spec: ai.appliedSpec },
                identity,
                controller.signal,
              )
            : await workflowAuthoringService.createBlank(input, identity, controller.signal);
        createdId = spec.id;
      }
      if (!active()) return;
      if (createdId !== input.workflowId) throw new Error('创建响应不属于目标工作流，请刷新后确认创建结果。');
      if (context.openCreatedWorkflow) context.openCreatedWorkflow(createdId);
      else {
        context.refreshWorkflows();
        context.navigate('editor', { workflowId: createdId });
      }
      onCreated?.();
    } catch (error) {
      if (active()) {
        const status = error && typeof error === 'object' && 'status' in error ? error.status : undefined;
        const uncertain =
          !!(error && typeof error === 'object' && 'creationUncertain' in error && error.creationUncertain) ||
          typeof status !== 'number' ||
          status >= 500 ||
          status === 408;
        setState({ token, form, busy: false, uncertain, error: workflowCreationError(error) });
      }
    } finally {
      if (request.current === operation) {
        request.current = null;
        if (latest.current === token && !controller.signal.aborted)
          setState((previous) => (previous.token === token ? { ...previous, busy: false } : previous));
      }
    }
  };
  return {
    form,
    update,
    submit,
    canSubmit,
    allowed,
    busy: current.busy,
    uncertain: current.uncertain,
    error: current.error,
    idError,
    ai,
  };
}
