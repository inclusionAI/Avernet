import { SetStateAction, useState } from 'react';
import { DemoNode, DemoWorkflow, EditorView, NodeId } from './model';
import { DEMO_WORKFLOWS, getAccessibleDemoWorkflows } from './workflowCatalog';
import { parseWorkflow, serializeWorkflow } from './yaml';

interface WorkflowDraft {
  nodes: DemoNode[];
  selectedId: NodeId;
  view: EditorView;
  yaml: string;
  yamlError: string;
  savedYaml: string;
  notice: string;
}
const EMPTY_DRAFT: WorkflowDraft = {
  nodes: [],
  selectedId: 'strategy',
  view: 'canvas',
  yaml: '',
  yamlError: '',
  savedYaml: '',
  notice: '',
};
function createDraft(workflow: DemoWorkflow): WorkflowDraft {
  const nodes = workflow.nodes.map((node) => ({ ...node }));
  const yaml = serializeWorkflow(nodes, workflow);
  return { ...EMPTY_DRAFT, nodes, yaml, savedYaml: yaml };
}
function draftError(draft: WorkflowDraft): string {
  return draft.yamlError || (draft.nodes.some((node) => !node.name.trim()) ? '节点名称不能为空。' : '');
}

/** Drafts belong to this mounted hook, not to the drawer's visible content or a global store. */
export function useTaskEscortDemo(catalog: readonly DemoWorkflow[] = DEMO_WORKFLOWS) {
  const workflows = getAccessibleDemoWorkflows(catalog);
  const [tab, setTab] = useState<'overview' | 'editor'>('overview');
  const [workflowId, setWorkflowId] = useState<string | null>(() => workflows[0]?.id ?? null);
  const [drafts, setDrafts] = useState(() => new Map<string, WorkflowDraft>());
  // Resolve access before reading ANY draft: revoked data must never appear in an intermediate render.
  const workflow = workflows.find((item) => item.id === workflowId) ?? (workflows.length ? workflows[0] : null);
  const resolvedId = workflow?.id ?? null;
  if (workflowId !== resolvedId) setWorkflowId(resolvedId);
  const draft = workflow ? drafts.get(workflow.id) ?? createDraft(workflow) : EMPTY_DRAFT;
  const error = draftError(draft);

  const updateDraft = (update: (current: WorkflowDraft) => WorkflowDraft) => {
    if (!workflow) return;
    setDrafts((current) =>
      new Map(current).set(workflow.id, update(current.get(workflow.id) ?? createDraft(workflow))),
    );
  };
  const selectWorkflow = (id: string): boolean => {
    if (!workflows.some((item) => item.id === id)) return false;
    setWorkflowId(id);
    return true;
  };
  const setView = (value: SetStateAction<EditorView>) => {
    updateDraft((current) => ({
      ...current,
      view: typeof value === 'function' ? value(current.view) : value,
    }));
  };
  const setSelectedId = (value: SetStateAction<NodeId>) => {
    updateDraft((current) => ({
      ...current,
      selectedId: typeof value === 'function' ? value(current.selectedId) : value,
    }));
  };
  const updateNode = (id: NodeId, patch: Partial<Pick<DemoNode, 'name' | 'description' | 'timeoutSeconds'>>) => {
    if (!workflow) return;
    updateDraft((current) => {
      const nodes = current.nodes.map((node) => (node.id === id ? { ...node, ...patch } : node));
      return { ...current, nodes, yaml: serializeWorkflow(nodes, workflow), yamlError: '', notice: '' };
    });
  };
  const updateYaml = (text: string) => {
    if (!workflow) return;
    updateDraft((current) => {
      try {
        return { ...current, yaml: text, nodes: parseWorkflow(text, workflow), yamlError: '', notice: '' };
      } catch (cause) {
        return {
          ...current,
          yaml: text,
          notice: '',
          yamlError: cause instanceof Error ? cause.message : 'YAML 无效。',
        };
      }
    });
  };
  const locateNode = (id: NodeId) => {
    if (!workflow) return;
    updateDraft((current) => ({ ...current, selectedId: id, view: 'canvas' }));
    setTab('editor');
  };
  const save = () => {
    updateDraft((current) =>
      draftError(current)
        ? current
        : {
            ...current,
            savedYaml: current.yaml,
            notice: '本地草稿已保存 · 仅当前页面内存，刷新或卸载组件后清空；未运行、未部署。',
          },
    );
  };
  const reset = () => {
    if (!workflow) return;
    updateDraft((current) => ({
      ...createDraft(workflow),
      selectedId: current.selectedId,
      view: current.view,
      notice: '已重置为初始演示数据，未影响任何真实工作流。',
    }));
  };
  return {
    workflows,
    workflow,
    selectWorkflow,
    tab,
    setTab,
    view: draft.view,
    setView,
    nodes: draft.nodes,
    selectedId: draft.selectedId,
    setSelectedId,
    yaml: draft.yaml,
    savedYaml: draft.savedYaml,
    yamlError: draft.yamlError,
    updateYaml,
    error,
    dirty: draft.yaml !== draft.savedYaml,
    notice: draft.notice,
    updateNode,
    locateNode,
    save,
    reset,
  };
}
export type TaskEscortDemoState = ReturnType<typeof useTaskEscortDemo>;
