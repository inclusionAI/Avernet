// @asset-migrated: teamclaw 自研资产
/**
 * TaskPanelV2 —— 副屏壳层(spec §3.2):同会话任务切换条(可选)
 *  + 面包屑即标题行(两级栈导航) + 收起按钮 + L1/L2 互斥渲染。
 * 下钻封顶 2 层:dagStack 顶即当前节点;节点在下一次轮询后消失时 L2 出空态,不卡栈。
 */
import React from 'react';

import './TaskPanelV2.css';

import { ChevronRight, PanelToggle } from '../icons';
import { truncateText } from '../text';
import { C, TASK_STATUS_TONES } from '../tokens';
import type { TaskView } from '../types';
import { TaskNodePanelV2 } from './TaskNodePanelV2';
import { TaskOverviewV2 } from './TaskOverviewV2';
import type { TaskPanelNav } from './taskPanelNav';

export interface TaskPanelV2Props {
  task: TaskView;
  nav: TaskPanelNav;
  apiBaseUrl: string;
  bcsBaseUrl?: string;
  userId?: string;
  onOpenSubTask?: (subTaskId: string) => void;
  /** 宿主注入的收起回调(SDK 面板级 handle 不暴露给 content,故由宿主决定行为) */
  onTogglePanel?: () => void;
  /** 查询到任务记录时，由宿主注入任务切换开关；空/失败可不注入。 */
  switcher?: React.ReactNode;
  className?: string;
  style?: React.CSSProperties;
}

export const TaskPanelV2: React.FC<TaskPanelV2Props> = ({
  task,
  nav,
  apiBaseUrl,
  bcsBaseUrl,
  userId,
  onOpenSubTask,
  onTogglePanel,
  switcher,
  className,
  style,
}) => {
  const currentNodeId = nav.drillStack.length > 0 ? nav.drillStack[nav.drillStack.length - 1] : null;
  const node = currentNodeId ? task.nodes.find((n) => n.id === currentNodeId) ?? null : null;
  const nodeShort =
    (currentNodeId && task.dagNodes.find((dn) => dn.id === currentNodeId)?.short) ||
    (node ? truncateText(node.name, 8) : null);
  const focusStatusLabel = TASK_STATUS_TONES[task.status]?.label ?? task.status;

  return (
    <section
      data-task-panel-root="true"
      className={className}
      style={{
        position: 'relative',
        display: 'flex',
        flexDirection: 'column',
        height: '100%',
        minHeight: 0,
        background: C.surface,
        ...style,
      }}
    >
      {switcher}
      <header
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 4,
          padding: '0 12px',
          flexShrink: 0,
          borderBottom: `1px solid ${C.border}`,
          fontSize: 14,
          color: C.textPrimary,
        }}
      >
        {currentNodeId === null ? (
          <>
            <span
              title={`任务总览：${task.name}`}
              style={{
                minWidth: 0,
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
                fontWeight: 600,
              }}
            >
              {task.name}
            </span>
            <span style={{ color: C.textSecondary }}>·</span>
            <span style={{ color: C.textSecondary, whiteSpace: 'nowrap' }}>{focusStatusLabel}</span>
          </>
        ) : (
          <>
            <button
              type="button"
              onClick={nav.backToOverview}
              title="返回任务总览"
              style={{
                border: 'none',
                background: 'transparent',
                color: C.textSecondary,
                fontSize: 14,
                cursor: 'pointer',
                padding: '4px 6px',
                borderRadius: 6,
              }}
            >
              任务总览
            </button>
            <ChevronRight />
            <span style={{ fontWeight: 600 }}>{nodeShort ?? currentNodeId}</span>
          </>
        )}
        <div style={{ flex: 1 }} />
        {onTogglePanel && (
          <div
            onClick={onTogglePanel}
            title="收起任务副屏"
            role="button"
            aria-label="收起任务副屏"
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              width: 28,
              height: 28,
              borderRadius: 6,
              cursor: 'pointer',
              color: C.textSecondary,
              transition: 'background 0.15s',
            }}
            onMouseEnter={(e) => (e.currentTarget.style.background = C.surfaceAlt)}
            onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
          >
            <PanelToggle />
          </div>
        )}
      </header>
      <div style={{ flex: 1, minHeight: 0, overflow: 'auto' }}>
        {currentNodeId === null ? (
          <TaskOverviewV2
            task={task}
            nav={nav}
            apiBaseUrl={apiBaseUrl}
            bcsBaseUrl={bcsBaseUrl}
            userId={userId}
            onOpenSubTask={onOpenSubTask}
          />
        ) : (
          <TaskNodePanelV2
            task={task}
            node={node}
            nav={nav}
            apiBaseUrl={apiBaseUrl}
            bcsBaseUrl={bcsBaseUrl}
            userId={userId}
            onOpenSubTask={onOpenSubTask}
          />
        )}
      </div>
    </section>
  );
};

export default TaskPanelV2;
