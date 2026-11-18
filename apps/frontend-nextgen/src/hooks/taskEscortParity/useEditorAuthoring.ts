import { editorYaml } from '@/domain/taskEscortParity/editor';
import type { EditorSpec } from '@/domain/taskEscortParity/editorTypes';
import type { TaskEscortParityContext } from '@/domain/taskEscortParity/types';
import type { WorkflowAuthoringIssue, WorkflowSaveResult } from '@/domain/taskEscortParity/workflowAuthoringTypes';
import { workflowAuthoringService } from '@/services/taskEscortParity/workflowAuthoringService';
import { useEffect, useRef, useState } from 'react';
import { useEditorAuthoringBaseline } from './useEditorAuthoringBaseline';

export function assertEditorDraft(spec: EditorSpec | null | undefined, original: EditorSpec): EditorSpec {
  if (!spec) throw new Error('校验未返回可应用的工作流规格');
  if (spec.id !== original.id || spec.version !== original.version) throw new Error('草稿不得修改当前工作流标识或版本');
  return spec;
}
export const editorIssueText = (issues: WorkflowAuthoringIssue[]) =>
  issues.map((issue) => `${issue.path ? `${issue.path}: ` : ''}${issue.message}`);
const message = (error: unknown) => (error instanceof Error ? error.message : '操作失败，请重试');

/** The submitted snapshot is the only snapshot a successful save may mark clean. */
export function useEditorAuthoring(context: TaskEscortParityContext, original: EditorSpec, canEdit: boolean) {
  const persistence = useEditorAuthoringBaseline(context, original, canEdit);
  const initialYaml = editorYaml(original);
  const [spec, setSpec] = useState(original);
  const [yaml, setYaml] = useState(initialYaml);
  const [savedYaml, setSavedYaml] = useState(initialYaml);
  const [validation, setValidation] = useState<{ yaml: string; spec: EditorSpec; warnings: string[] } | null>(null);
  const [phase, setPhase] = useState<'validate' | 'save' | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const active = useRef<AbortController | null>(null);
  const current = useRef({ yaml, canEdit });
  current.current = { yaml, canEdit };
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
      setPhase(null);
      setValidation(null);
    }
  }, [canEdit]);

  function begin(nextPhase: NonNullable<typeof phase>) {
    if (active.current) return null;
    if (!current.current.canEdit || !context.userId) {
      setError('需要当前身份的工作流编辑权限才能修改、保存或发布');
      return null;
    }
    const controller = new AbortController();
    active.current = controller;
    setPhase(nextPhase);
    setError(null);
    setNotice(null);
    return { controller, params: { user_id: context.userId }, snapshot: current.current.yaml };
  }
  function isCurrent(controller: AbortController, snapshot: string) {
    return (
      !controller.signal.aborted &&
      active.current === controller &&
      current.current.canEdit &&
      current.current.yaml === snapshot
    );
  }
  function finish(controller: AbortController) {
    if (active.current !== controller) return;
    active.current = null;
    setPhase(null);
  }
  async function validate() {
    const request = begin('validate');
    if (!request) return;
    const { controller, params, snapshot } = request;
    try {
      const result = await workflowAuthoringService.validateYaml(snapshot, params, controller.signal);
      if (!isCurrent(controller, snapshot)) return;
      if (!result.valid) throw new Error(editorIssueText(result.errors).join('；') || 'YAML 校验未通过');
      const next = assertEditorDraft(result.spec, original);
      setValidation({ yaml: snapshot, spec: next, warnings: editorIssueText(result.warnings) });
      setNotice('YAML 校验通过，可应用到画布；尚未保存。');
    } catch (cause) {
      if (isCurrent(controller, snapshot)) {
        setValidation(null);
        setError(message(cause));
      }
    } finally {
      finish(controller);
    }
  }
  function replaceDraft(next: EditorSpec) {
    if (!current.current.canEdit || active.current) throw new Error('当前不能应用草稿，请等待保存完成并检查编辑权限');
    const checked = assertEditorDraft(next, original);
    const text = editorYaml(checked);
    current.current.yaml = text;
    setSpec(checked);
    setYaml(text);
    setValidation(null);
    setError(null);
    setNotice('草稿已应用到画布与 YAML，尚未保存或发布。');
  }
  function applyYaml() {
    if (!validation || validation.yaml !== current.current.yaml) return;
    replaceDraft(validation.spec);
  }
  function editYaml(text: string) {
    if (!current.current.canEdit || active.current) return;
    current.current.yaml = text;
    setYaml(text);
    setValidation(null);
    setError(null);
    setNotice(null);
  }
  async function save(note?: string): Promise<WorkflowSaveResult | null> {
    if (!persistence.baseline) {
      setError('缺少已读取的工作流保存基线，请刷新基线后重试');
      return null;
    }
    const request = begin('validate');
    if (!request) return null;
    const { controller, params, snapshot } = request;
    try {
      const validation = await workflowAuthoringService.validateYaml(snapshot, params, controller.signal);
      if (!isCurrent(controller, snapshot)) return null;
      if (!validation.valid) throw new Error(editorIssueText(validation.errors).join('；') || 'YAML 校验未通过');
      const next = assertEditorDraft(validation.spec, original);
      setPhase('save');
      const receipt = await workflowAuthoringService.save(
        { workflowId: original.id, spec: next, note, baseline: persistence.baseline },
        params,
        controller.signal,
      );
      if (!isCurrent(controller, snapshot)) return null;
      const persisted = assertEditorDraft(receipt.spec, original);
      persistence.setBaseline(receipt.baseline);
      setSavedYaml(snapshot);
      setSpec(persisted);
      setValidation(null);
      setNotice('工作流已保存，尚未发布。');
      // Keep this session mounted until the caller has reviewed/published the saved receipt.
      return receipt;
    } catch (cause) {
      if (isCurrent(controller, snapshot)) setError(message(cause));
      return null;
    } finally {
      finish(controller);
    }
  }
  return {
    spec,
    yaml,
    persistence,
    saveReady: !!persistence.baseline && !persistence.loading,
    dirty: yaml !== savedYaml,
    pendingYaml: yaml !== editorYaml(spec),
    phase,
    error,
    notice,
    validated: validation?.yaml === yaml,
    warnings: validation?.yaml === yaml ? validation.warnings : [],
    editYaml,
    validate,
    applyYaml,
    replaceDraft,
    save,
  };
}
export type EditorAuthoring = ReturnType<typeof useEditorAuthoring>;
