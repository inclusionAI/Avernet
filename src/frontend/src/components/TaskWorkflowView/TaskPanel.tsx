/**
 * TaskPanel — 副屏 panel 包装 (FR-OBS-11, plan §1.4b)。
 *
 * 注册 type = "taskPanel.TaskWorkflowView"。Backend create_spec 发 <AixUI panel>
 * 消息(component=taskPanel.TaskWorkflowView, params={task_id}) → 前端
 * hasAixPanelContent 命中 → chatBridge.openPanelTab({type, payload:{task_id}})
 * → 渲染本 panel → 内嵌 TaskWorkflowView 画布弹出任务整体执行流程。
 *
 * 注:backend EventBus TaskPanelEvent → 前端 <AixUI panel> chat 消息 的 carrier
 * transport 是 transport-bridge 层工作(TODO);本 panel 是接收端,task_id 从
 * payload 读取(payload.task_id 或 payload.data.task_id)。
 */
import type { PanelContentProps } from '@aix-chat/ui';
import React from 'react';

import { registerPanel } from '@/components/Panels/registry';
import TaskWorkflowView from './index';

const TaskPanelContent: React.FC<PanelContentProps> = ({ payload }) => {
  const taskId =
    (payload as any)?.task_id ??
    (payload as any)?.data?.task_id ??
    (payload as any)?._componentKey; // 兜底
  if (!taskId || typeof taskId !== 'string' || !taskId.startsWith('task-')) {
    return (
      <div style={{ padding: 16, color: '#6b7280' }}>
        未提供有效 task_id,无法加载任务执行流程画布。
      </div>
    );
  }
  return <TaskWorkflowView taskId={taskId} poll />;
};

/** 副屏 panel 注册(模块加载即注册,由 GroupChat/app 引入本模块触发) */
export function registerTaskPanel(): void {
  registerPanel({
    type: 'taskPanel.TaskWorkflowView',
    name: '任务执行流程',
    component: TaskPanelContent,
  });
}

/**
 * openTaskPanel — 命令式弹出任务执行流程副屏 (FR-OBS-11, plan §4.5.3)。
 *
 * Backend 的 carrier transport 在 community profile 是 Noop(开源无 chat 推送
 * 总线),因此由前端 create-flow 在创建成功后直接调用本 helper 命中
 * chatBridge.openPanelTab 弹出副屏;corp/transport-bridge 接通真实 chat-WS
 * <AixUI panel> 推送后,群聊侧 hasAixPanelContent 命中也会走到同一 panel,
 * 两条路径收敛到同一渲染器。
 *
 * 防御性:chatBridge 未挂载或副屏未装载时静默降级(不阻塞 create 主流程)。
 */
export function openTaskPanel(taskId: string, title?: string): void {
  if (typeof window === 'undefined') return;
  const bridge = (window as any).aixBridge;
  if (!bridge || typeof bridge.openPanelTab !== 'function') return;
  const tabTitle = title ? `任务执行 - ${title}` : '任务执行流程';
  try {
    bridge.openPanelTab({
      id: `task-run-${taskId}`,
      title: tabTitle,
      type: 'taskPanel.TaskWorkflowView',
      params: { task_id: taskId },
      closable: true,
    });
  } catch (e) {
    // 副屏未装载或 panel 未注册 — 不阻塞任务创建主流程
    // eslint-disable-next-line no-console
    console.warn('[openTaskPanel] openPanelTab failed (non-fatal):', e);
  }
}

// 模块加载即自注册(对齐 GroupChatPage 内 umd panel 的 top-level 注册模式)
registerTaskPanel();

export default TaskPanelContent;