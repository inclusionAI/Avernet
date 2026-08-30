import { TaskPanel } from '@/assets/TaskPanel';
import { Drawer, DrawerContent } from '@/components/ui/Drawer';
import { Empty } from '@/components/ui/Empty';
import type { TaskListItem } from '@/domain/tasks/models';
import type { ScheduledRoutineRecord, ScheduledRoutineRunRecord } from '@/services/scheduledTasks';
import { TASK_API_BASE } from '@/services/tasks/taskConfig';
import { MyTaskRoutineDrawer } from './MyTaskRoutineDrawer';
import { MyTaskRoutineHistoryDrawer } from './MyTaskRoutineHistoryDrawer';

export interface MyTaskDrawersProps {
  selectedTaskId: string | null;
  onCloseTask: () => void;
  taskRecords: TaskListItem[];
  ownerUserId: string;
  selectedRoutine: ScheduledRoutineRecord | null;
  selectedRoutineKey: string | null;
  onCloseRoutine: () => void;
  selectedRoutineRuns: ScheduledRoutineRunRecord[];
  selectedRoutineRunsLoading: boolean;
  selectedRoutineRunsError: string | null;
  selectedRoutineHistoryId: string | null;
  onCloseRoutineHistory: () => void;
  historyRuns: ScheduledRoutineRunRecord[];
  historyLoading: boolean;
  historyError: string | null;
  onOpenRoutineFromHistory: (botId: string, routineId: string) => void;
}

export function MyTaskDrawers({
  selectedTaskId,
  onCloseTask,
  taskRecords,
  ownerUserId,
  selectedRoutine,
  selectedRoutineKey,
  onCloseRoutine,
  selectedRoutineRuns,
  selectedRoutineRunsLoading,
  selectedRoutineRunsError,
  selectedRoutineHistoryId,
  onCloseRoutineHistory,
  historyRuns,
  historyLoading,
  historyError,
  onOpenRoutineFromHistory,
}: MyTaskDrawersProps) {
  const selectedTask = taskRecords.find((item) => item.task_id === selectedTaskId) ?? null;

  return (
    <>
      <Drawer open={Boolean(selectedTaskId)} onOpenChange={(open) => !open && onCloseTask()}>
        <DrawerContent side="right" size="full" className="p-0">
          <div className="-m-6 h-[calc(100%+3rem)]">
            {selectedTask ? (
              <TaskPanel
                apiBaseUrl={TASK_API_BASE}
                bcsBaseUrl=""
                userId={ownerUserId}
                taskId={selectedTask.task_id}
                style={{ height: '100%', minHeight: 0 }}
              />
            ) : (
              <div className="flex h-full items-center justify-center">
                <Empty title="任务不存在" description="请从列表中重新选择一个用户任务。" />
              </div>
            )}
          </div>
        </DrawerContent>
      </Drawer>

      <MyTaskRoutineDrawer
        selectedRoutine={selectedRoutine}
        selectedRoutineKey={selectedRoutineKey}
        onCloseRoutine={onCloseRoutine}
        selectedRoutineRuns={selectedRoutineRuns}
        selectedRoutineRunsLoading={selectedRoutineRunsLoading}
        selectedRoutineRunsError={selectedRoutineRunsError}
      />

      <MyTaskRoutineHistoryDrawer
        selectedRoutineHistoryId={selectedRoutineHistoryId}
        onCloseRoutineHistory={onCloseRoutineHistory}
        historyRuns={historyRuns}
        historyLoading={historyLoading}
        historyError={historyError}
        onOpenRoutineFromHistory={onOpenRoutineFromHistory}
      />
    </>
  );
}
