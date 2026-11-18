import type { TaskEscortWorkspaceMode } from '@/domain/taskEscortWorkspace';
import type { EditorNode, EditorNodePatch, EditorSnapshot } from './editorModel';
import { copyEditorSnapshot, initialEditorSnapshot, validateEditorNodes } from './editorModel';

/** A session belongs to one mounted workspace. No persistence, network or module-global draft cache. */
export function createEditorSession(mode: TaskEscortWorkspaceMode) {
  const saved = new Map<string, EditorSnapshot>();
  const drafts = new Map<string, EditorSnapshot>();
  const listeners = new Set<() => void>();
  const emit = () => listeners.forEach((listener) => listener());
  const getSnapshot = (workflowId: string) => {
    if (!drafts.has(workflowId)) drafts.set(workflowId, initialEditorSnapshot(mode));
    return drafts.get(workflowId)!;
  };
  const update = (workflowId: string, snapshot: EditorSnapshot) => {
    drafts.set(workflowId, snapshot);
    emit();
  };
  const changed = (workflowId: string, nodes: EditorNode[]) =>
    JSON.stringify(nodes) !== JSON.stringify((saved.get(workflowId) ?? initialEditorSnapshot(mode)).nodes);
  const commit = (workflowId: string, publish: boolean) => {
    if (mode !== 'demo') return { ok: false, message: '真实模式尚未接入编辑与发布，未向服务器提交任何变更' };
    const snapshot = getSnapshot(workflowId);
    const validation = validateEditorNodes(snapshot.nodes);
    if (!validation.valid) return { ok: false, message: `校验未通过：${validation.errors.join('；')}` };
    const next = copyEditorSnapshot({ ...snapshot, version: publish ? 'v2.5' : snapshot.version, dirty: false });
    saved.set(workflowId, copyEditorSnapshot(next));
    update(workflowId, next);
    return {
      ok: true,
      message: publish
        ? '模拟发布完成 · 版本 v2.5，仅在当前演示会话生效'
        : '草稿已保存 · 仅保存在本次演示会话中，未提交到服务器',
    };
  };
  return {
    getSnapshot,
    subscribe(listener: () => void) {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    select(workflowId: string, id: string) {
      const snapshot = getSnapshot(workflowId);
      if (snapshot.selected === id || !snapshot.nodes.some((node) => node.id === id)) return;
      update(workflowId, { ...snapshot, selected: id });
    },
    patch(workflowId: string, patch: EditorNodePatch) {
      if (mode !== 'demo') return;
      const snapshot = getSnapshot(workflowId);
      const selected = snapshot.nodes.find(({ id }) => id === snapshot.selected);
      if (!selected || Object.entries(patch).every(([key, value]) => selected[key as keyof EditorNodePatch] === value))
        return;
      const nodes = snapshot.nodes.map((node) => (node.id === selected.id ? { ...node, ...patch } : node));
      update(workflowId, {
        ...snapshot,
        nodes,
        past: [...snapshot.past, snapshot.nodes].slice(-100),
        future: [],
        dirty: changed(workflowId, nodes),
      });
    },
    undo(workflowId: string) {
      if (mode !== 'demo') return;
      const snapshot = getSnapshot(workflowId),
        nodes = snapshot.past.at(-1);
      if (nodes)
        update(workflowId, {
          ...snapshot,
          nodes,
          past: snapshot.past.slice(0, -1),
          future: [snapshot.nodes, ...snapshot.future],
          dirty: changed(workflowId, nodes),
        });
    },
    redo(workflowId: string) {
      if (mode !== 'demo') return;
      const snapshot = getSnapshot(workflowId),
        nodes = snapshot.future[0];
      if (nodes)
        update(workflowId, {
          ...snapshot,
          nodes,
          past: [...snapshot.past, snapshot.nodes],
          future: snapshot.future.slice(1),
          dirty: changed(workflowId, nodes),
        });
    },
    save(workflowId: string) {
      return commit(workflowId, false);
    },
    publish(workflowId: string) {
      return commit(workflowId, true);
    },
    restore(workflowId: string) {
      update(workflowId, copyEditorSnapshot(saved.get(workflowId) ?? initialEditorSnapshot(mode)));
    },
    reset() {
      saved.clear();
      drafts.clear();
      emit();
    },
  };
}
export type TaskEscortEditorSession = ReturnType<typeof createEditorSession>;
