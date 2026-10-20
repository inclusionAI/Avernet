// @asset-migrated: teamclaw 自研资产
/**
 * TaskPanel —— 任务副屏主组件:H3 轮询内核 + V2 面包屑两级栈壳(TaskPanelV2)。
 * 三 Tab 平铺与左侧下钻 rail 已由两级栈演进替代(spec §3.2):L1 任务总览、L2 节点副屏;
 * 导航态经 nav prop 注入(宿主缝合 taskSidePanelStore;未注入时本地降级)。
 */
import React from 'react';

import { TaskPanelFetcher } from './TaskPanelFetcher';
import { Empty as StateEmpty } from './theme';
import { C } from './tokens';
import type { TaskView } from './types';
import { TaskPanelV2 } from './v2/TaskPanelV2';
import { useLocalTaskPanelNav, type TaskPanelNav } from './v2/taskPanelNav';

export interface TaskPanelProps {
  apiBaseUrl: string;
  // task API 路径前缀：Open Core /openapi、内部 /api（不含 host），由上层经 capability 透传。
  taskApiBase?: string;
  bcsBaseUrl?: string;
  userId?: string;
  taskId: string;
  taskInfoFallback?: Partial<
    Pick<TaskView, 'taskTypeLabel' | 'sourceLabel' | 'ownerBotName' | 'createdAt' | 'finishedAt'>
  >;
  /** 宿主注入导航(业务层 useTaskPanelNavBinding);缺省时组件内本地降级,副屏仍可用但不联动会话 */
  nav?: TaskPanelNav;
  /** 多任务切换条槽位(宿主注入 TaskSwitcherBar) */
  switcher?: React.ReactNode;
  onOpenSubTask?: (subTaskId: string) => void;
  onTogglePanel?: () => void;
  className?: string;
  style?: React.CSSProperties;
}

export const TaskPanel: React.FC<TaskPanelProps> = ({
  apiBaseUrl,
  taskApiBase,
  bcsBaseUrl = '',
  userId,
  taskId,
  taskInfoFallback,
  nav: navProp,
  switcher,
  onOpenSubTask,
  onTogglePanel,
  className,
  style,
}) => {
  // nav 未注入时的本地降级(hook 必须无条件调用,再按 prop 有无择一)
  const localNav = useLocalTaskPanelNav();
  const nav = navProp ?? localNav;

  return (
    <>
      <TaskPanelFetcher apiBaseUrl={apiBaseUrl} taskApiBase={taskApiBase} taskId={taskId} userId={userId}>
        {({ task, loading, error, retry }) => {
          const resolvedTask = task
            ? {
                ...task,
                taskTypeLabel: task.taskTypeLabel || taskInfoFallback?.taskTypeLabel || '—',
                sourceLabel: task.sourceLabel || taskInfoFallback?.sourceLabel || '—',
                ownerBotName: task.ownerBotName || taskInfoFallback?.ownerBotName || '—',
                createdAt: task.createdAt || taskInfoFallback?.createdAt || '',
                finishedAt: task.finishedAt || taskInfoFallback?.finishedAt || null,
              }
            : null;

          return (
            <div
              className={className}
              style={{
                display: 'flex',
                flexDirection: 'column',
                height: '100%',
                minHeight: 0,
                overflow: 'hidden',
                background: C.surface,
                ...style,
              }}
            >
              {loading && !task && <StateEmpty description="加载任务中…" />}
              {error && !task && (
                <div style={{ padding: 24, textAlign: 'center' }}>
                  <div style={{ color: C.danger, fontSize: 13, marginBottom: 12 }}>加载失败:{error}</div>
                  <button
                    type="button"
                    onClick={retry}
                    style={{
                      padding: '6px 16px',
                      border: `1px solid ${C.primary}`,
                      borderRadius: 4,
                      background: C.surface,
                      color: C.primary,
                      cursor: 'pointer',
                    }}
                  >
                    重试
                  </button>
                </div>
              )}
              {resolvedTask && (
                <TaskPanelV2
                  task={resolvedTask}
                  nav={nav}
                  apiBaseUrl={apiBaseUrl}
                  bcsBaseUrl={bcsBaseUrl}
                  userId={userId}
                  onOpenSubTask={onOpenSubTask}
                  onTogglePanel={onTogglePanel}
                  switcher={switcher}
                  style={{ flex: 1, minHeight: 0 }}
                />
              )}
            </div>
          );
        }}
      </TaskPanelFetcher>
    </>
  );
};