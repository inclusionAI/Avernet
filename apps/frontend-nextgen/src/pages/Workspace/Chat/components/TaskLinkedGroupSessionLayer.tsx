import { useHumanIdentity } from '@/hooks/useHumanIdentity';
import { useTaskSidePanelStore } from '@/stores/taskSidePanelStore';
import { TaskLinkedGroupSession } from './TaskLinkedGroupSession';

/**
 * 任务联动层(自闭合):订阅 taskSidePanelStore.linkedGroupSession,在对话页主区
 * 原位渲染跨域群会话覆盖层。页面只需挂载本组件一行;关闭即回到当前单聊。
 */
export function TaskLinkedGroupSessionLayer() {
  const target = useTaskSidePanelStore((s) => s.linkedGroupSession);
  const closeLinkedGroupSession = useTaskSidePanelStore((s) => s.closeLinkedGroupSession);
  const { identity: human } = useHumanIdentity();
  if (!target) return null;
  return (
    <TaskLinkedGroupSession
      target={target}
      userId={human?.userId || undefined}
      onClose={closeLinkedGroupSession}
    />
  );
}

export default TaskLinkedGroupSessionLayer;
