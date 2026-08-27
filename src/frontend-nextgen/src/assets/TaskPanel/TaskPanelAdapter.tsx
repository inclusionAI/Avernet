// @asset-migrated: teamclaw 自研适配层（路 A 本地注册胶水，非副屏 SDK 源码）
/**
 * TaskPanelAdapter —— 引擎 PanelContentProps ↔ TaskPanel 业务 props 适配。
 * 设计与 BcsWorkflowPanelAdapter 对齐。
 *
 * 数据流向：
 * - 入：PanelContentProps.params（openPanelTab({params:{apiBaseUrl, taskId, initialTab}}) 透传）
 * - 出：TaskPanel onInteraction 上报（节点交互/打开子任务），经 onInteraction 转 InteractionRecord。
 */
import type { PanelContentProps } from '@tc-chat/ui/es/SidePanelContent';
import type { CSSProperties } from 'react';
import { TaskPanel } from './TaskPanel';

type TaskPanelParams = {
  apiBaseUrl?: string;
  bcsBaseUrl?: string;
  userId?: string;
  taskId?: string;
  initialTab?: 'info' | 'artifacts' | 'progress';
  onTogglePanelName?: string;
};

/** 兼容已落库的旧 AixUI 消息，避免历史 params 继续请求已删除的本地测试代理。 */
function normalizePanelBaseUrl(value?: string): string {
  return value === '/__test_local__' || value === '/__test_bcs__' ? '' : value ?? '';
}

export function TaskPanelAdapter({
  params,
  onInteraction,
  className,
  style,
}: PanelContentProps & { className?: string; style?: CSSProperties }) {
  const p = (params ?? {}) as TaskPanelParams;
  const apiBaseUrl = normalizePanelBaseUrl(p.apiBaseUrl);
  const bcsBaseUrl = normalizePanelBaseUrl(p.bcsBaseUrl);
  if (!p.taskId) {
    return (
      <div
        style={{ padding: 24, color: '#86909C', fontSize: 13, textAlign: 'center', ...(style as object) }}
        className={className}
      >
        任务副屏缺少 taskId 参数（apiBaseUrl 为空串表示相对路径，mock/proxy 接管）。
      </div>
    );
  }

  return (
    <TaskPanel
      apiBaseUrl={apiBaseUrl}
      bcsBaseUrl={bcsBaseUrl}
      userId={p.userId}
      taskId={p.taskId}
      initialTab={p.initialTab}
      onOpenSubTask={(subTaskId) => {
        onInteraction({
          source: { type: 'panel', target: 'taskPanel.TaskLoopView' },
          description: `打开子任务 ${subTaskId}`,
          action: { verb: 'open', subject: 'subtask', params: { subTaskId } },
          snapshot: { parentTaskId: p.taskId },
        });
      }}
      style={style}
      className={className}
    />
  );
}

export default TaskPanelAdapter;
