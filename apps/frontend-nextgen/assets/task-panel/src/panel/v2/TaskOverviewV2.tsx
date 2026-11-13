// @asset-migrated: teamclaw 自研资产
/**
 * TaskOverviewV2 —— Level1 任务总览,PRD 两区结构:步骤区仅展示节点视图 + 产物区。
 * 交互语义(PRD 方案A):点根节点恢复宿主会话，点其他节点联动会话；
 * 节点视图当前隐藏下钻入口，暂不提供节点副屏下钻展示。
 */
import React from 'react';

import { C } from '../tokens';
import type { TaskView } from '../types';
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

export const TaskOverviewV2: React.FC<TaskOverviewV2Props> = ({ task, nav, apiBaseUrl, onOpenSubTask }) => (
  <div data-testid="task-overview-v2" style={{ minHeight: '100%' }}>
    <section>
      <div style={{ display: 'flex', alignItems: 'center', padding: '12px 16px 0' }}>
        <div style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
          <span style={{ fontSize: 14, fontWeight: 600, color: C.textPrimary }}>步骤</span>
          <span style={{ fontSize: 11, color: C.textSecondary }}>点击查看会话</span>
        </div>
      </div>
      <NodeListViewV2 task={task} nav={nav} onOpenSubTask={onOpenSubTask} />
    </section>
    <TaskArtifactsList task={task} apiBaseUrl={apiBaseUrl} enrichGroupDisplay={false} />
  </div>
);

export default TaskOverviewV2;
