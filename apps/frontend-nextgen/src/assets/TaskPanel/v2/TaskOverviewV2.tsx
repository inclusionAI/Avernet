// @asset-migrated: teamclaw 自研资产
/**
 * TaskOverviewV2 —— Level1 任务总览(步骤区双视图 + 产物区 + 折叠任务信息)。
 * 本文件为 Task 5 装配占位,实体实现由后续任务(P2)按 plan.md Task 6 TDD 落地。
 */
import React from 'react';

import { C } from '../tokens';
import type { TaskView } from '../types';
import type { TaskPanelNav } from './taskPanelNav';

export interface TaskOverviewV2Props {
  task: TaskView;
  nav: TaskPanelNav;
  apiBaseUrl: string;
  bcsBaseUrl?: string;
  userId?: string;
  onOpenSubTask?: (subTaskId: string) => void;
}

export const TaskOverviewV2: React.FC<TaskOverviewV2Props> = ({ task }) => (
  <div data-testid="task-overview-v2" style={{ padding: 16, color: C.textSecondary, fontSize: 13 }}>
    任务总览占位(P2 实施):{task.name}
  </div>
);

export default TaskOverviewV2;