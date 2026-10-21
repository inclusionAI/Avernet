import { GroupSessionView } from '@/assets/TaskPanel/GroupDrillDown';
import type { TaskNodeView } from '@/assets/TaskPanel/types';
import type { TaskLinkedSessionTarget } from '@/stores/taskSidePanelStore';

function isGroupSession(target: TaskLinkedSessionTarget): boolean {
  return target.kind === 'group' || target.sessionId.startsWith('bcs_grp_');
}

/**
 * TaskPanel 节点对应的左侧联动会话。
 *
 * 本组件作为 ChatLayout 的普通 child 渲染在 ChatArea 内；ChatLayoutPanelSlot 仍是
 * ChatLayout 的直接 Panel sibling，因此切节点只替换主会话内容，不卸载或清空任务副屏。
 * GroupSessionView 已统一适配协作群和 Bot 单聊消息端点，这里只负责把跳转目标映射为
 * 只读的 TaskNodeView 取数形状。
 */
export function TaskLinkedSession({
  target,
  userId,
  onClose,
}: {
  target: TaskLinkedSessionTarget;
  userId?: string;
  onClose: () => void;
}) {
  const group = isGroupSession(target);
  const executor = group ? null : target.botId ?? null;
  const node: TaskNodeView = {
    id: `linked-${target.sessionId}`,
    name: group ? '任务联动 · 群会话' : '任务联动 · Bot 会话',
    sequence: 0,
    status: 'running',
    runMode: group ? 'coop_group' : 'single_bot',
    groupId: group ? target.groupId ?? null : null,
    sessionId: target.sessionId,
    assignee: executor,
    executor,
    artifacts: [],
    stepTraces: [],
    hasSubTask: false,
  };

  return (
    <section aria-label="任务联动会话" className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden bg-background">
      <GroupSessionView
        node={node}
        bcsBaseUrl=""
        apiBaseUrl=""
        userId={userId}
        onBack={onClose}
        backLabel="返回当前会话"
      />
    </section>
  );
}

export default TaskLinkedSession;
