// @asset-migrated: teamclaw 自研资产
/**
 * TaskPanel —— 任务副屏主组件（H3）：Fetch 轮询 + 头部进度条 + 三 tab（默认任务进度）+ 折叠副屏按钮。
 * 节点下钻从左侧并列打开；主任务内容和原有任务 Tab 保持不变。
 */
import React, { useCallback, useState } from 'react';
import { GroupDrillDownPanel } from './GroupDrillDown';
import { PanelToggle } from './icons';
import { TaskArtifactsTab } from './TaskArtifactsTab';
import { TaskInfoTab } from './TaskInfoTab';
import { TaskPanelFetcher } from './TaskPanelFetcher';
import { TaskProgressTab } from './TaskProgressTab';
import { TaskSubTaskPanel } from './TaskSubTaskPanel';
import { GlobalKeyframes, Empty as StateEmpty } from './theme';
import { C } from './tokens';
import type { TaskNodeView, TaskView } from './types';
import { useResizableRail } from './useResizableRail';

const TABS = [
  { key: 'info', label: '任务信息' },
  { key: 'artifacts', label: '产物' },
  { key: 'progress', label: '任务进度' },
] as const;
type TabKey = (typeof TABS)[number]['key'];

const leftRailStyle: React.CSSProperties = {
  width: 360,
  minWidth: 300,
  maxWidth: '70%',
  flexShrink: 0,
  height: '100%',
  minHeight: 0,
  background: C.surface,
  display: 'flex',
  flexDirection: 'column',
  overflow: 'hidden',
  animation: 'task-panel-rail-in 220ms ease-out',
};

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
  initialTab?: TabKey;
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
  initialTab = 'progress',
  onOpenSubTask,
  onTogglePanel,
  className,
  style,
}) => {
  const [tab, setTab] = useState<TabKey>(initialTab);
  const [groupDrillNodes, setGroupDrillNodes] = useState<TaskNodeView[]>([]);
  const [activeGroupDrillId, setActiveGroupDrillId] = useState<string | null>(null);
  const [subTaskIds, setSubTaskIds] = useState<string[]>([]);
  const [activeSubTaskId, setActiveSubTaskId] = useState<string | null>(null);

  // 节点下钻左侧 rail 与主面板之间可拖拽调节宽度(nil 为关闭时);宽度持久化、键盘可达。
  const {
    railWidth,
    setRailWidth,
    containerRef,
    startResize,
    min: railMin,
    maxRatio: railMaxRatio,
    defaultWidth: railDefaultWidth,
  } = useResizableRail();

  const openSubTask = useCallback(
    (subTaskId: string) => {
      setSubTaskIds((current) => (current.includes(subTaskId) ? current : [...current, subTaskId]));
      setActiveSubTaskId(subTaskId);
      onOpenSubTask?.(subTaskId);
    },
    [onOpenSubTask],
  );

  const closeSubTask = useCallback((subTaskId: string) => {
    setSubTaskIds((current) => {
      const next = current.filter((id) => id !== subTaskId);
      setActiveSubTaskId((active) => (active === subTaskId ? next[next.length - 1] ?? null : active));
      return next;
    });
  }, []);

  const openGroupSession = useCallback((node: TaskNodeView) => {
    setGroupDrillNodes((current) => (current.some((item) => item.id === node.id) ? current : [...current, node]));
    setActiveGroupDrillId(node.id);
  }, []);

  const closeGroupSession = useCallback((nodeId: string) => {
    setGroupDrillNodes((current) => {
      const next = current.filter((node) => node.id !== nodeId);
      setActiveGroupDrillId((active) => (active === nodeId ? next[next.length - 1]?.id ?? null : active));
      return next;
    });
  }, []);

  return (
    <>
      <GlobalKeyframes />
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
          // 节点下钻左侧 rail：协作群下钻优先,其次子任务下钻;两者均无时不渲染 rail 与分隔条。
          const drillPanel =
            activeGroupDrillId && groupDrillNodes.length > 0 ? (
              <GroupDrillDownPanel
                nodes={groupDrillNodes}
                activeNodeId={activeGroupDrillId}
                bcsBaseUrl={bcsBaseUrl}
                apiBaseUrl={apiBaseUrl}
                userId={userId}
                onSelect={setActiveGroupDrillId}
                onClose={closeGroupSession}
              />
            ) : activeSubTaskId && subTaskIds.length > 0 ? (
              <TaskSubTaskPanel
                apiBaseUrl={apiBaseUrl}
                taskApiBase={taskApiBase}
                taskIds={subTaskIds}
                activeTaskId={activeSubTaskId}
                onSelect={setActiveSubTaskId}
                onClose={closeSubTask}
              />
            ) : null;
          // 当前下钻副屏选中的节点 id:协作群下钻即被选中节点 id,子任务下钻则取 subTaskId 命中的节点。
          // 用于主副屏节点视图高亮显示当前下钻的是哪个节点任务。
          const activeDrillNodeId =
            activeGroupDrillId ??
            (activeSubTaskId && resolvedTask
              ? resolvedTask.nodes.find((n) => n.subTaskId === activeSubTaskId)?.id ?? null
              : null);
          return (
            <div
              ref={containerRef}
              className={className}
              style={{
                display: 'flex',
                flexDirection: 'row',
                height: '100%',
                minHeight: 0,
                overflow: 'hidden',
                background: C.surface,
                ...style,
              }}
            >
              {drillPanel && (
                <>
                  <aside style={{ ...leftRailStyle, width: railWidth }}>{drillPanel}</aside>
                  <div
                    role="separator"
                    aria-orientation="vertical"
                    aria-label="拖动调节下钻面板宽度，双击恢复默认"
                    tabIndex={0}
                    title="拖动调节下钻面板宽度，双击恢复"
                    onPointerDown={startResize}
                    onDoubleClick={() => setRailWidth(railDefaultWidth)}
                    onKeyDown={(event) => {
                      if (event.key === 'ArrowLeft') {
                        event.preventDefault();
                        setRailWidth((w) => Math.max(railMin, w - 24));
                      } else if (event.key === 'ArrowRight') {
                        event.preventDefault();
                        const rect = containerRef.current?.getBoundingClientRect();
                        const max = rect ? Math.floor(rect.width * railMaxRatio) : railDefaultWidth;
                        setRailWidth((w) => Math.min(max, w + 24));
                      }
                    }}
                    style={{
                      width: 6,
                      flexShrink: 0,
                      alignSelf: 'stretch',
                      cursor: 'col-resize',
                      background: C.border,
                      transition: 'background 0.15s',
                    }}
                    onMouseEnter={(event) => (event.currentTarget.style.background = C.primary)}
                    onMouseLeave={(event) => (event.currentTarget.style.background = C.border)}
                  />
                </>
              )}

              <div
                style={{ flex: 1, minWidth: 0, height: '100%', minHeight: 0, display: 'flex', flexDirection: 'column' }}
              >
                <div
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    borderBottom: `1px solid ${C.border}`,
                    flexShrink: 0,
                  }}
                >
                  {TABS.map((t) => {
                    const active = tab === t.key;
                    return (
                      <button
                        key={t.key}
                        type="button"
                        onClick={() => setTab(t.key)}
                        style={{
                          padding: '12px 20px',
                          border: 'none',
                          background: 'transparent',
                          cursor: 'pointer',
                          fontSize: 14,
                          fontWeight: active ? 600 : 400,
                          color: active ? C.primary : C.textSecondary,
                          borderBottom: active ? `2px solid ${C.primary}` : '2px solid transparent',
                          transition: 'all 0.2s',
                          marginBottom: -1,
                        }}
                      >
                        {t.label}
                      </button>
                    );
                  })}
                  <div style={{ flex: 1 }} />
                  {onTogglePanel && (
                    <div
                      onClick={onTogglePanel}
                      title="折叠副屏"
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        width: 28,
                        height: 28,
                        marginRight: 8,
                        borderRadius: 6,
                        cursor: 'pointer',
                        transition: 'background 0.15s',
                      }}
                      onMouseEnter={(event) => (event.currentTarget.style.background = C.surfaceAlt)}
                      onMouseLeave={(event) => (event.currentTarget.style.background = 'transparent')}
                    >
                      <PanelToggle />
                    </div>
                  )}
                </div>
                <div style={{ flex: 1, minHeight: 0, overflow: 'auto' }}>
                  {loading && !task && <StateEmpty description="加载任务中…" />}
                  {error && !task && (
                    <div style={{ padding: 24, textAlign: 'center' }}>
                      <div style={{ color: C.danger, fontSize: 13, marginBottom: 12 }}>加载失败：{error}</div>
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
                    <>
                      {tab === 'info' && <TaskInfoTab task={resolvedTask} />}
                      {tab === 'artifacts' && <TaskArtifactsTab task={resolvedTask} />}
                      {tab === 'progress' && (
                        <TaskProgressTab
                          task={resolvedTask}
                          userId={userId}
                          activeDrillNodeId={activeDrillNodeId}
                          onOpenSubTask={openSubTask}
                          onOpenGroupSession={openGroupSession}
                        />
                      )}
                    </>
                  )}
                </div>
              </div>
            </div>
          );
        }}
      </TaskPanelFetcher>
    </>
  );
};
