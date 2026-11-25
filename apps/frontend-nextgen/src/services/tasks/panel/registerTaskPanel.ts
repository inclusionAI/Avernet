import { registerPanelContent } from '@tc-chat/ui/es/SidePanelContent';
import { TaskPanelAdapter } from '@/components/Workspace/TaskPanelAdapter';
/** SDK-only registration; both declaration keys mount the same trusted UMD host. */
export function registerTaskPanel(): void {
  registerPanelContent('taskPanel.TaskLoopView', TaskPanelAdapter);
  registerPanelContent('task-loop', TaskPanelAdapter);
}
