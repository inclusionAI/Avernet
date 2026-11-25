import type { TaskEscortWorkspaceController } from '@/domain/taskEscortWorkspace';
import type { EditorMode, EditorNodePatch } from '@/services/taskEscortWorkspace/editorModel';
import {
  editorConnections,
  editorModes,
  editorPalette,
  serializeEditorWorkflow,
  validateEditorNodes,
} from '@/services/taskEscortWorkspace/editorModel';
import { useCallback, useEffect, useState, useSyncExternalStore } from 'react';
import { useTaskEscortWorkspaceEditorSession } from './useTaskEscortWorkspaceEditorContext';
import { useTaskEscortWorkspaceEditorNavigation } from './useTaskEscortWorkspaceEditorNavigation';

export type { EditorMode, EditorNode, EditorNodeKind } from '@/services/taskEscortWorkspace/editorModel';
export function useTaskEscortWorkspaceEditor(demo: TaskEscortWorkspaceController) {
  const session = useTaskEscortWorkspaceEditorSession();
  const workflow = demo.workflows.find(({ id }) => id === demo.workflowId) ?? demo.workflows[0];
  const workflowId = workflow?.id ?? 'demo-workflow';
  const getSnapshot = useCallback(() => session.getSnapshot(workflowId), [session, workflowId]);
  const snapshot = useSyncExternalStore(session.subscribe, getSnapshot, getSnapshot);
  const [mode, setMode] = useState<EditorMode>('visual');
  const [publish, setPublish] = useState(false);
  const [zoom, setZoom] = useState(100);
  const restore = useCallback(() => session.restore(workflowId), [session, workflowId]);
  const navigation = useTaskEscortWorkspaceEditorNavigation(snapshot.dirty, restore, workflowId);
  const node = snapshot.nodes.find(({ id }) => id === snapshot.selected) ?? snapshot.nodes[0];
  const validation = validateEditorNodes(snapshot.nodes);
  const yaml = serializeEditorWorkflow(snapshot.nodes, workflow, snapshot.version);
  useEffect(() => {
    setMode('visual');
    setPublish(false);
    setZoom(100);
  }, [session, workflowId]);
  const save = () => {
    demo.notice(session.save(workflowId).message);
  };
  const publishDraft = () => {
    const result = session.publish(workflowId);
    demo.notice(result.message);
    if (result.ok) setPublish(false);
  };
  const changeWorkflow = (id: string) => {
    if (id === workflowId || !demo.workflows.some((item) => item.id === id)) return;
    navigation.requestLeave(() => demo.setWorkflowId(id));
  };
  const setSelected = (id: string) => session.select(workflowId, id);
  const selectPalette = (id: string) => {
    const item = editorPalette.find((entry) => entry.nodeId === id);
    if (!item) return;
    setSelected(id);
    demo.notice(`已选中${item.name}示例节点，可在右侧编辑属性`);
  };
  return {
    ...snapshot,
    ...navigation,
    workflow,
    node,
    validation,
    yaml,
    mode,
    setMode,
    publish,
    setPublish,
    zoom,
    setZoom,
    connections: editorConnections,
    palette: editorPalette,
    modes: editorModes,
    save,
    publishDraft,
    changeWorkflow,
    setSelected,
    selectPalette,
    patch: (patch: EditorNodePatch) => session.patch(workflowId, patch),
    undo: () => session.undo(workflowId),
    redo: () => session.redo(workflowId),
    openScript: () => {
      setMode('yaml');
      demo.notice('已打开 YAML / 脚本配置只读预览');
    },
  };
}
export type EditorState = ReturnType<typeof useTaskEscortWorkspaceEditor>;
