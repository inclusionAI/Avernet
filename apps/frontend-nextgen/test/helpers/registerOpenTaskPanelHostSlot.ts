// 显式注册真实 Open SDK 生命周期适配；必须晚于测试文件的 jest.mock 提升。
import { TaskPanelAdapter } from '@/components/Workspace/TaskPanelAdapter';
import { getExtensionSlots, registerExtensionSlots } from '@/extensions/slots';
import { taskNavigationAwareHandle, onTaskPanelTabClosed } from '@/services/tasks/panel/taskPanelHandleAdapter';
import { peekTaskPanelController } from '@/services/tasks/panel/taskPanelRuntime';

registerExtensionSlots({
  ...getExtensionSlots(),
  taskPanelHost: {
    Adapter: TaskPanelAdapter,
    wrapHandle: taskNavigationAwareHandle,
    onTabClosed: onTaskPanelTabClosed,
    leaveConversation: () => peekTaskPanelController()?.leaveConversation(),
  },
});
