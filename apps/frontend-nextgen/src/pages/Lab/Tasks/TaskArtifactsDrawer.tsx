import { TaskArtifactsList, TaskPanelFetcher } from '@/assets/TaskPanel';
import { Button } from '@/components/ui/Button';
import { Drawer, DrawerContent, DrawerDescription, DrawerTitle } from '@/components/ui/Drawer';
import { Empty } from '@/components/ui/Empty';
import { Spin } from '@/components/ui/Spin';
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
        <header className="shrink-0 border-b border-border px-5 py-4 pr-14">
          <DrawerTitle className="text-base font-semibold text-foreground">任务产物</DrawerTitle>
          <DrawerDescription className="mt-1 break-all font-mono text-xs text-muted-foreground">
            {taskId ? `任务 ID：${taskId}` : '请选择任务'}
          </DrawerDescription>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto">
          {taskId ? (
            <TaskPanelFetcher
              apiBaseUrl={TASK_API_BASE}
              taskApiBase={TASK_READ_API_BASE}
              taskId={taskId}
              userId={ownerUserId}
              includeActionLog={false}
            >
              {({ task, loading, error, retry }) => {
                if (loading && !task) return <Spin tip="加载任务产物中…" className="py-16" />;
                if (error && !task) {
                  return (
                    <Empty
                      title="任务产物加载失败"
                      description={error}
                      action={
                        <Button variant="secondary" onClick={retry}>
                          重试
                        </Button>
                      }
                    />
                  );
                }
                if (!task) return null;
                return (
                  <TaskArtifactsList
                    task={task}
                    apiBaseUrl={TASK_API_BASE}
                    title="产物列表"
                    previewPortalContainer={drawerElement}
                  />
                );
              }}
            </TaskPanelFetcher>
          ) : null}
        </div>
      </DrawerContent>
    </Drawer>
  );
}
