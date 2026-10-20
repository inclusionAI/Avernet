import { TaskPanel } from '@/assets/TaskPanel';
import { TaskSwitcherBar } from '@/assets/TaskPanel/v2/TaskSwitcherBar';
import { Drawer, DrawerContent } from '@/components/ui/Drawer';
import { Empty } from '@/components/ui/Empty';
import type { TaskListItem } from '@/domain/tasks/models';
import { useTaskPanelNavBinding } from '@/hooks/taskPanelNavBinding';
import { toSwitcherItem } from '@/hooks/useTaskSwitcherTasks';
import { useTaskSidePanelStore } from '@/stores/taskSidePanelStore';
import type { ScheduledRoutineRecord, ScheduledRoutineRunRecord } from '@/services/scheduledTasks';
import { TASK_API_BASE, TASK_READ_API_BASE } from '@/services/tasks/taskConfig';
import { useMemo } from 'react';
import { getBotDisplayName, getUserTaskSourceLabel, getUserTaskTypeLabel } from '../userTaskUtils';
import { MyTaskRoutineDrawer } from './MyTaskRoutineDrawer';
import { MyTaskRoutineHistoryDrawer } from './MyTaskRoutineHistoryDrawer';

export interface MyTaskDrawersProps {
  selectedTaskId: string | null;
  onCloseTask: () => void;
  /** V2 多任务切换条:切至另一任务(由 MyTaskPage 持有 selectedTaskId 状态) */
  onSelectTask?: (taskId: string) => void;
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
  botNameMap: Record<string, string>;
}

export function MyTaskDrawers({
  selectedTaskId,
  onCloseTask,
  onSelectTask,
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
  botNameMap,
}: MyTaskDrawersProps) {
  // V2 导航按 taskId 分片缝合 taskSidePanelStore(抽屉/聊天两挂载点共享导航语义)。
  const navBinding = useTaskPanelNavBinding(selectedTaskId ?? '');
  const nav = selectedTaskId ? navBinding : undefined;
  // V2 多任务切换条:记录列表已在宿主侧加载,同映射复用;切换由宿主(setSelectedTaskId)承载。
  const switcherItems = useMemo(() => (taskRecords ?? []).map(toSwitcherItem), [taskRecords]);
  const handleSelectTask = useMemo(() => {
    if (!onSelectTask) return undefined;
    return (taskId: string) => {
      useTaskSidePanelStore.getState().setActiveTask(taskId);
      onSelectTask(taskId);
    };
  }, [onSelectTask]);
  const switcher =
    switcherItems.length && handleSelectTask ? (
      <TaskSwitcherBar items={switcherItems} activeTaskId={selectedTaskId ?? switcherItems[0].taskId} onSelectTask={handleSelectTask} />
    ) : null;
  const selectedTask = selectedTaskId ? taskRecords.find((record) => record.task_id === selectedTaskId) : null;
  const taskInfoFallback = selectedTask
    ? (() => {
        const record = selectedTask as TaskListItem & {
          task_info?: {
            execution_config?: { task_type?: string };
            create_time?: string;
            finish_time?: string | null;
            source_type?: string | null;
            owner_bot_id?: string | null;
          };
          create_time?: string;
          finish_time?: string | null;
        };
        const taskInfo = record.task_info;
        return {
          taskTypeLabel: getUserTaskTypeLabel(
            record.execution_config?.task_type ?? taskInfo?.execution_config?.task_type,
          ),
          sourceLabel: getUserTaskSourceLabel(record.source_type ?? taskInfo?.source_type),
          ownerBotName: getBotDisplayName(botNameMap, record.owner_bot_id ?? taskInfo?.owner_bot_id),
          createdAt: record.gmt_create ?? taskInfo?.create_time ?? record.create_time ?? '',
          finishedAt: record.gmt_modified ?? taskInfo?.finish_time ?? record.finish_time ?? null,
        };
      })()
    : undefined;

  return (
    <>
      <Drawer open={Boolean(selectedTaskId)} onOpenChange={(open) => !open && onCloseTask()}>
        <DrawerContent side="right" size="full" className="overflow-hidden bg-background p-0" bodyClassName="p-0">
          <div className="h-full min-h-0">
            {selectedTaskId ? (
              <TaskPanel
                apiBaseUrl={TASK_API_BASE}
                taskApiBase={TASK_READ_API_BASE}
                bcsBaseUrl=""
                userId={ownerUserId}
                taskId={selectedTaskId}
                taskInfoFallback={taskInfoFallback}
                nav={nav}
                switcher={switcher}
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
        botNameMap={botNameMap}
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
