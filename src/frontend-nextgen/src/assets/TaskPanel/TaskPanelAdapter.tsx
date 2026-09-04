// @asset-migrated: teamclaw 自研适配层（路 A 本地注册胶水，非副屏 SDK 源码）
/**
 * TaskPanelAdapter —— 引擎 PanelContentProps ↔ TaskPanel 业务 props 适配。
 * 适配引擎 PanelContentProps 与 TaskPanel 业务 props。
 *
 * 数据流向：
 * - 入：PanelContentProps.params（openPanelTab({params:{apiBaseUrl, taskId, initialTab}}) 透传）
 * - 出：TaskPanel onInteraction 上报（节点交互/打开子任务），经 onInteraction 转 InteractionRecord。
 */
import type { PanelContentProps } from '@tc-chat/ui/es/SidePanelContent';
import type { CSSProperties } from 'react';
import { TaskPanel, type TaskPanelProps } from './TaskPanel';

type TaskPanelParams = {
  apiBaseUrl?: string;
  // task API 路径前缀（不含 host）：由 empty.ts 副屏 wrapper 经 capability 解析注入；缺省走内面路径。
  taskApiBase?: string;
  bcsBaseUrl?: string;
  userId?: string;
  taskId?: string;
  initialTab?: 'info' | 'artifacts' | 'progress';
  onTogglePanelName?: string;
  taskInfoFallback?: TaskPanelProps['taskInfoFallback'];
};

/**
 * 归一化副屏 apiBaseUrl:只保留可经前端网关/dev 代理同源转发的相对路径。
 * - 空 / 已删除的本地测试代理(/__test_local__、/__test_bcs__) → '' (相对,走代理)。
 * - 后端写死的绝对回调地址 → '' :浏览器直连会跨源 CORS,
 *   统一改相对路径,由网关/代理把 /api 与 /openapi 内部 API 转发到对应后端。
 */
function normalizePanelBaseUrl(value?: string): string {
  if (!value) return '';
  if (value === '/__test_local__' || value === '/__test_bcs__') return '';
  if (/^https?:\/\//i.test(value)) return '';
  return value;
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
      taskApiBase={p.taskApiBase}
      bcsBaseUrl={bcsBaseUrl}
      userId={p.userId}
      taskId={p.taskId}
      taskInfoFallback={p.taskInfoFallback}
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
