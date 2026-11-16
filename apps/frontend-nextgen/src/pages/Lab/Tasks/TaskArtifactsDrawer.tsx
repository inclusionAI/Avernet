import { TaskPanelAssetBoundary } from '@/components/Workspace/TaskPanelAssetBoundary';
import { Drawer, DrawerContent, DrawerDescription, DrawerTitle } from '@/components/ui/Drawer';
import { TASK_API_BASE, TASK_READ_API_BASE } from '@/services/tasks/taskConfig';
import { useState } from 'react';

export interface TaskArtifactsDrawerProps {
  taskId: string | null;
  ownerUserId: string;
  onClose: () => void;
}

/** 实验室任务产物抽屉：复用 TaskPanel 的 dashboard 映射、产物聚合、预览与下载能力。 */
export function TaskArtifactsDrawer({ taskId, ownerUserId, onClose }: TaskArtifactsDrawerProps) {
  const [drawerElement, setDrawerElement] = useState<HTMLDivElement | null>(null);

  return (
    <Drawer open={Boolean(taskId)} onOpenChange={(open) => !open && onClose()}>
      <DrawerContent
        ref={setDrawerElement}
        side="right"
        size="lg"
        className="overflow-hidden bg-background"
        bodyClassName="flex flex-col p-0"
        closeLabel="关闭任务产物抽屉"
      >
        <header className="shrink-0 border-b border-divider px-5 py-4 pr-14">
          <DrawerTitle className="text-base font-semibold text-foreground">任务产物</DrawerTitle>
          <DrawerDescription className="mt-1 break-all font-mono text-xs text-muted-foreground">
            {taskId ? `任务 ID：${taskId}` : '请选择任务'}
          </DrawerDescription>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto">
          {taskId ? (
            <TaskPanelAssetBoundary>{asset => (
              <asset.TaskArtifactsView
                apiBaseUrl={TASK_API_BASE}
                taskApiBase={TASK_READ_API_BASE}
                taskId={taskId}
                ownerUserId={ownerUserId}
                previewPortalContainer={drawerElement}
              />
            )}</TaskPanelAssetBoundary>
          ) : null}
        </div>
      </DrawerContent>
    </Drawer>
  );
}
