// @asset-migrated: teamclaw 自研资产
/**
 * TaskNodePanelV2 —— Level2 节点副屏(节点信息/执行流程/节点产物)。
 * 本文件为 Task 5 装配占位,实体实现由后续任务(P3)按 plan.md Task 7 TDD 落地;
 * 节点消失(图变更)时的空态契约在此定死:任务轮询后节点不存在 → 「节点数据缺失」。
 */
import React from 'react';

import { Empty as StateEmpty } from '../theme';
import { C } from '../tokens';
import type { TaskNodeView, TaskView } from '../types';
import type { TaskPanelNav } from './taskPanelNav';

export interface TaskNodePanelV2Props {
  task: TaskView;
  /** null = drillStack 顶节点已不存在(数据变更),渲染空态并允许返回 */
  node: TaskNodeView | null;
  nav: TaskPanelNav;
  apiBaseUrl: string;
  bcsBaseUrl?: string;
  userId?: string;
  onOpenSubTask?: (subTaskId: string) => void;
}

export const TaskNodePanelV2: React.FC<TaskNodePanelV2Props> = ({ node, nav }) =>
  node ? (
    <div data-testid="task-node-panel-v2" style={{ padding: 16, color: C.textPrimary, fontSize: 13 }}>
      节点副屏占位(P3 实施):{node.name}
    </div>
  ) : (
    <div style={{ padding: 24 }} data-testid="task-node-panel-v2-missing">
      <StateEmpty description="节点数据缺失(任务图已变更)" />
      <button
        type="button"
        onClick={nav.backToOverview}
        style={{
          display: 'block',
          margin: '12px auto 0',
          padding: '6px 16px',
          border: `1px solid ${C.primary}`,
          borderRadius: 6,
          background: 'transparent',
          color: C.primary,
          cursor: 'pointer',
        }}
      >
        返回任务总览
      </button>
    </div>
  );

export default TaskNodePanelV2;