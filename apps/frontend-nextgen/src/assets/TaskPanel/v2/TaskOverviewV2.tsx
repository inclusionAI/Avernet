// @asset-migrated: teamclaw 自研资产
/**
 * TaskOverviewV2 —— Level1 任务总览,PRD 两区结构:步骤区(节点视图/DAG 视图双切换)+ 产物区。
 * 交互语义(PRD 方案A):点节点 = 跳转选中(不进第二层);下钻按钮 = 选中 + 进栈。
 */
import React, { useState } from 'react';

import { resolveNodeSessionTarget } from '../sessionResolution';
import { C } from '../tokens';
import type { TaskView } from '../types';
import { DagViewV2 } from './DagViewV2';
import { NodeListViewV2 } from './NodeListViewV2';
import { TaskArtifactsList } from './TaskArtifactsList';
import type { TaskPanelNav } from './taskPanelNav';

export interface TaskOverviewV2Props {
  task: TaskView;
  nav: TaskPanelNav;
  apiBaseUrl: string;
  bcsBaseUrl?: string;
  userId?: string;
  onOpenSubTask?: (subTaskId: string) => void;
}

type StepView = 'node' | 'dag';

const ViewSwitchButton: React.FC<{
  active: boolean;
  title: string;
  label: string;
  onClick: () => void;
}> = ({ active, title, label, onClick }) => (
  <button
    type="button"
    title={title}
    onClick={onClick}
    style={{
      border: 'none',
      background: active ? C.surface : 'transparent',
      color: active ? C.primary : C.textSecondary,
      fontSize: 12,
      padding: '4px 10px',
      borderRadius: 6,
      cursor: 'pointer',
      boxShadow: active ? '0 1px 2px rgba(0,0,0,0.06)' : 'none',
      fontWeight: active ? 600 : 400,
    }}
  >
    {label}
  </button>
);

export const TaskOverviewV2: React.FC<TaskOverviewV2Props> = ({ task, nav, apiBaseUrl, onOpenSubTask }) => {
  const [view, setView] = useState<StepView>('node');

  return (
    <div data-testid="task-overview-v2" style={{ minHeight: '100%' }}>
      <section>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '12px 16px 0' }}>
          <span style={{ fontSize: 14, fontWeight: 600, color: C.textPrimary }}>步骤</span>
          <div style={{ display: 'inline-flex', gap: 2, background: C.surfaceAlt, borderRadius: 6, padding: 2 }}>
            <ViewSwitchButton
              active={view === 'node'}
              title={view === 'node' ? '当前为节点视图' : '切换到节点视图'}
              label="节点视图"
              onClick={() => setView('node')}
            />
            <ViewSwitchButton
              active={view === 'dag'}
              title={view === 'dag' ? '当前为 DAG 视图' : '切换到 DAG 视图'}
              label="DAG视图"
              onClick={() => setView('dag')}
            />
          </div>
        </div>
        {view === 'node' ? (
          <NodeListViewV2 task={task} nav={nav} onOpenSubTask={onOpenSubTask} />
        ) : (
          <div style={{ height: 420, minHeight: 0 }}>
            <DagViewV2
              view="interactive"
              nodes={task.dagNodes}
              edges={task.dagEdges}
              selectedId={nav.selectedNodeId}
              onNodeClick={(id) => {
                const node = task.nodes.find((n) => n.id === id);
                if (node) nav.selectNode(id, resolveNodeSessionTarget(node, task));
              }}
              onNodeDrill={(id) => {
                const node = task.nodes.find((n) => n.id === id);
                if (!node) return;
                const target = resolveNodeSessionTarget(node, task);
                nav.selectNode(id, target);
                nav.drillInto(id, target);
              }}
            />
          </div>
        )}
      </section>
      <TaskArtifactsList task={task} apiBaseUrl={apiBaseUrl} />
    </div>
  );
};

export default TaskOverviewV2;
