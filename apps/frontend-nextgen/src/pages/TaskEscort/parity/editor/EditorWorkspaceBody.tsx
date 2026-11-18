import type { EditorNode } from '@/domain/taskEscortParity/editorTypes';
import { useEditorVisualHistory } from '@/hooks/taskEscortParity/useEditorVisualHistory';
import type { EditorAuthoring } from '@/hooks/taskEscortParity/useEditorAuthoring';
import { EditorAuthoringYaml } from './EditorAuthoringYaml';
import { EditorGraph } from './EditorGraph';
import { useState } from 'react';
import { deleteNode, duplicateNode } from './canvas/model';
import { EditorInspector } from './EditorInspector';

/** Visual edits join the same unsaved spec/YAML draft and validated save boundary. */
export function EditorWorkspaceBody({ authoring, canEdit, busy, mode, node, focusId, select, nodePending, onPendingChange }: {
  authoring: EditorAuthoring;
  canEdit: boolean;
  busy: boolean;
  mode: 'visual' | 'split' | 'yaml';
  node: EditorNode | undefined;
  focusId: string | null;
  select: (id: string | null) => void;
  nodePending: boolean;
  onPendingChange: (pending: boolean) => void;
}) {
  const blocked = !canEdit || busy || authoring.pendingYaml;
  const [actionError, setActionError] = useState<string | null>(null);
  const history = useEditorVisualHistory(authoring.spec, authoring.replaceDraft, blocked);
  const mutate = (action: () => typeof authoring.spec) => {
    if (blocked || nodePending) return;
    try { history.change(action()); setActionError(null); }
    catch (error) { setActionError(error instanceof Error ? error.message : String(error)); }
  };
  return (
    <div className="editor-visual-workspace min-w-0">
      {actionError && <p role="alert" className="p-3 text-sm text-destructive">{actionError}</p>}
      {authoring.pendingYaml && (
        <p role="status" className="m-0 border-b border-border bg-muted px-4 py-3 text-sm text-muted-foreground">
          YAML 有未应用内容，画布编辑暂时锁定；请先校验并应用 YAML，避免覆盖草稿。
        </p>
      )}
      {focusId && !node && (
        <p role="status" className="m-0 border-b border-border px-4 py-3 text-sm text-muted-foreground">
          所选节点 {focusId} 不在当前规格中，请重新选择。
        </p>
      )}
      <div className={`editor-workspace-panes ${mode === 'yaml' ? 'editor-workspace-yaml-only' : ''}`}>
        <div className={`editor-workspace-center ${mode === 'split' ? 'editor-workspace-split' : ''}`}>
          {mode !== 'yaml' && <EditorGraph spec={authoring.spec} selectedId={node?.id ?? null}
            select={select} disabled={blocked || nodePending} onChange={history.change} onUndo={history.undo} onRedo={history.redo}
            canUndo={!nodePending && history.canUndo} canRedo={!nodePending && history.canRedo} />}
          {mode !== 'visual' && <EditorAuthoringYaml authoring={authoring} canEdit={canEdit} blocked={nodePending} />}
        </div>
        {mode !== 'yaml' && <EditorInspector node={node} nodes={authoring.spec.nodes}
          close={() => select(null)} disabled={blocked} onPendingChange={onPendingChange}
          onDuplicate={() => { if (node) mutate(() => duplicateNode(authoring.spec, node.id)); }}
          onDelete={() => { if (node) mutate(() => deleteNode(authoring.spec, node.id)); }}
          onChange={(next) => history.change({ ...authoring.spec,
            nodes: authoring.spec.nodes.map((item) => item.id === next.id ? next : item) })} />}
      </div>
    </div>
  );
}
