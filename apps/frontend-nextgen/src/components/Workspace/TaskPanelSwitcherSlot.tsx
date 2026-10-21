import { TaskSwitcherBar } from '@/assets/TaskPanel/v2/TaskSwitcherBar';
import { getCapabilities } from '@/capabilities';
import { useTaskSwitcherTasks } from '@/hooks/useTaskSwitcherTasks';
import { chatBridge } from '@/services/workspace/chatBridge';
import { resolveTaskPanelDefaultWidth } from '@/services/workspace/panelWidthMemory';
import { useTaskSidePanelStore } from '@/stores/taskSidePanelStore';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import React from 'react';

/**
 * chat 宿主的多任务切换条挂件(业务层装配,经 TaskPanelAdapter.switcher 注入副屏):
 * - 数据:listTasks 经 useTaskSwitcherTasks(登录人工号);
 * - 活跃:taskSidePanelStore.activeTaskId(发起任务自动切换写入),未设置时以当前 tab 任务为准;
 * - 切换:chatBridge.openPanelTab 幂等 upsert——已存在 `task-${id}` tab 则更新数据并聚焦,
 *   不存在则按当前面板 params 形状新建同组件 tab(openTab 的既有语义,见 taskPanelMessage.ts 注释)。
 */
export const TaskPanelSwitcherSlot: React.FC<{
  currentTaskId: string;
  params: Record<string, unknown>;
}> = ({ currentTaskId, params }) => {
  // 订阅 identities:登录态变化时重渲染(getHumanIdentity 同步读 store)。
  useWorkspaceStore((s) => s.identities);
  const human = getCapabilities().getHumanIdentity();
  const userId = human.status === 'available' ? human.value?.userId?.trim() || undefined : undefined;
  const { items } = useTaskSwitcherTasks(userId);
  const activeTaskId = useTaskSidePanelStore((s) => s.activeTaskId);
  // I4:横条「当前」以本面板实例的任务为准——全局 activeTaskId 只服务发起任务后的 tab
  // 自动聚焦语义,不得让非活跃任务面板在自己的横条里显示别的任务为"当前"。
  const effectiveActive = currentTaskId || activeTaskId || items[0]?.taskId || '';
  if (!items.length) return null;

  return (
    <TaskSwitcherBar
      items={items}
      activeTaskId={effectiveActive}
      onSelectTask={(taskId) => {
        if (taskId === effectiveActive) return;
        useTaskSidePanelStore.getState().setActiveTask(taskId);
        const target = items.find((i) => i.taskId === taskId);
        chatBridge.openPanelTab({
          id: `task-${taskId}`,
          title: target?.title ?? '任务',
          type: 'taskPanel.TaskLoopView',
          closable: true,
          // 新开任务 tab 的初始宽:用户拖宽过则以记忆宽恢复,否则 PRD 初始 420(见 panelWidthMemory)。
          defaultWidth: resolveTaskPanelDefaultWidth(),
          params: { ...params, taskId },
        });
      }}
    />
  );
};

export default TaskPanelSwitcherSlot;