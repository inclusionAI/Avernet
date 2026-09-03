// @asset-migrated: teamclaw 自研资产
/** 任务进度 Tab：节点/DAG 视图切换、节点详情和子任务下钻。 */
import React, { useEffect, useMemo, useState } from 'react';
import { DagView } from './DagView';
import { NodeDetailDrawer } from './NodeDetailDrawer';
import { NodeListView } from './NodeListView';
import { Segmented } from './theme';
import { C } from './tokens';
import type { DagNodeView, TaskNodeView, TaskView } from './types';

export const TaskProgressTab: React.FC<{
  task: TaskView;
  userId?: string;
  onOpenSubTask?: (subTaskId: string) => void;
  /** 打开群会话视图（左侧并列下钻面板） */
  onOpenGroupSession?: (node: TaskNodeView) => void;
}> = ({ task, userId, onOpenSubTask, onOpenGroupSession }) => {
  const [view, setView] = useState<'node' | 'dag'>('node');
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);

  const selected = useMemo(
    () => task.nodes.find((node) => node.id === selectedNodeId) ?? null,
    [selectedNodeId, task.nodes],
  );

  useEffect(() => {
    if (selectedNodeId && !selected) {
      setSelectedNodeId(null);
      setDrawerOpen(false);
    }
  }, [selected, selectedNodeId]);

  const openDetail = (node: TaskNodeView) => {
    setSelectedNodeId(node.id);
    setDrawerOpen(false);
    requestAnimationFrame(() => requestAnimationFrame(() => setDrawerOpen(true)));
  };

  const openDagNodeDetail = (dagNode: DagNodeView) => {
    const node = task.nodes.find((item) => item.id === dagNode.id);
    if (!node) return;
    if (node.hasSubTask && node.subTaskId && onOpenSubTask) {
      onOpenSubTask(node.subTaskId);
      return;
    }
    openDetail(node);
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0, background: C.page }}>
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          gap: 12,
          padding: '10px 14px',
          borderBottom: `1px solid ${C.border}`,
          background: C.surface,
          flexShrink: 0,
        }}
      >
        <Segmented
          value={view}
          onChange={(value) => setView(value as 'node' | 'dag')}
          options={[
            { label: '节点视图', value: 'node' },
            { label: 'DAG 视图', value: 'dag' },
          ]}
        />
      </div>

      <div style={{ flex: 1, minHeight: 0, overflow: 'auto' }}>
        {view === 'node' ? (
          <NodeListView
            nodes={task.nodes}
            ownerBotId={task.ownerBotId}
            userId={userId}
            onViewNodeDetail={openDetail}
            onOpenSubTask={onOpenSubTask}
            onOpenGroupSession={onOpenGroupSession}
          />
        ) : (
          <DagView
            dagNodes={task.dagNodes}
            dagEdges={task.dagEdges}
            selectedNodeId={selectedNodeId}
            onViewNodeDetail={openDagNodeDetail}
          />
        )}
      </div>

      {selected && (
        <NodeDetailDrawer
          node={selected}
          open={drawerOpen}
          onClose={() => {
            setDrawerOpen(false);
            window.setTimeout(() => setSelectedNodeId(null), 300);
          }}
        />
      )}
    </div>
  );
};
