import { PageHeader } from '@/components/Common/PageHeader';
import { Button } from '@/components/ui/Button';
import { Card, CardContent } from '@/components/ui/Card';
import { Segmented } from '@/components/ui/Segmented';
import { useHumanIdentity } from '@/hooks/useHumanIdentity';
import { useOwnedBots } from '@/pages/Workspace/hooks/useOwnedBots';
import { History, RefreshCw } from 'lucide-react';
import { useMemo, useState } from 'react';
import { MyTaskDrawers } from './components/MyTaskDrawers';
import { RoutineTaskTab } from './components/RoutineTaskTab';
import { UserTaskTab } from './components/UserTaskTab';
import { useMyTaskTasks } from './hooks/useMyTaskTasks';
import { ALL_ROUTINE_BOT_VALUE, makeRoutineKey, useRoutineTasks } from './hooks/useRoutineTasks';

export default function MyTaskPage() {
  const { identity: humanIdentity } = useHumanIdentity();
  const ownerUserId = humanIdentity?.userId.trim() ?? '';
  const {
    taskRecords,
    loading: userLoading,
    error: userError,
    refresh: refreshUserTasks,
  } = useMyTaskTasks(ownerUserId);
  const { chatBots, isLoading: ownedBotsLoading } = useOwnedBots(ownerUserId || null, Boolean(ownerUserId));
  const routineBots = useMemo(
    () =>
      chatBots
        .map((bot) => ({
          botId: bot.realBotId || bot.botId,
          botName: bot.displayName || bot.botId,
        }))
        .filter((item) => Boolean(item.botId.trim())),
    [chatBots],
  );
  const routineBotOptions = useMemo(
    () => [
      { value: ALL_ROUTINE_BOT_VALUE, label: '全部' },
      ...routineBots.map((bot) => ({ value: bot.botId, label: bot.botName })),
    ],
    [routineBots],
  );
  const [selectedRoutineBotId, setSelectedRoutineBotId] = useState(ALL_ROUTINE_BOT_VALUE);

  const [activeTab, setActiveTab] = useState<'user' | 'routine'>('user');
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const [selectedRoutineKey, setSelectedRoutineKey] = useState<string | null>(null);
  const [selectedRoutineHistoryId, setSelectedRoutineHistoryId] = useState<string | null>(null);

  const {
    routines,
    loading: routineLoading,
    error: routineError,
    refreshRoutines,
    selectedRoutine,
    selectedRoutineRuns,
    selectedRoutineRunsLoading,
    selectedRoutineRunsError,
    historyRuns,
    historyLoading,
    historyError,
    runRoutine,
  } = useRoutineTasks(
    selectedRoutineBotId,
    routineBots,
    selectedRoutineKey,
    Boolean(selectedRoutineHistoryId),
    activeTab === 'routine',
  );

  const openRoutineFromHistory = (botId: string, routineId: string) => {
    setSelectedRoutineHistoryId(null);
    setSelectedRoutineKey(makeRoutineKey(botId, routineId));
    setActiveTab('routine');
  };

  const handleRefresh = () => {
    void Promise.all([refreshUserTasks(), refreshRoutines()]);
  };

  return (
    <main className="app-scrollbar h-full min-h-0 overflow-y-auto">
      <div className="mx-auto flex w-full max-w-[1600px] flex-col gap-6 px-4 py-4 sm:px-6 sm:py-6 2xl:px-8">
        <PageHeader
          title="我的任务"
          description="汇总用户任务与定时任务两个 Tab 的执行进度、结果与详情入口。定时任务支持按单个 Bot 筛选，也可以选择「全部」查看当前用户拥有的所有 Bot 定时任务。"
          actions={
            <div className="flex items-end gap-2">
              <Button variant="secondary" leftIcon={<RefreshCw className="size-4" />} onClick={handleRefresh}>
                刷新
              </Button>
            </div>
          }
        />

        <Card>
          <CardContent className="space-y-4 pt-5">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <Segmented
                value={activeTab}
                onChange={(value) => setActiveTab(value as 'user' | 'routine')}
                options={[
                  { value: 'user', label: '用户任务' },
                  { value: 'routine', label: '定时任务' },
                ]}
                className="w-full max-w-md"
              />
              {activeTab === 'routine' ? (
                <Button
                  variant="secondary"
                  size="sm"
                  leftIcon={<History className="size-4" />}
                  onClick={() => setSelectedRoutineHistoryId('overview')}
                >
                  历史任务
                </Button>
              ) : null}
            </div>

            {activeTab === 'user' ? (
              <UserTaskTab
                taskRecords={taskRecords}
                loading={userLoading}
                error={userError}
                onRetry={() => void refreshUserTasks()}
                onSelectTask={setSelectedTaskId}
                selectedTaskId={selectedTaskId}
              />
            ) : (
              <RoutineTaskTab
                routines={routines}
                loading={ownedBotsLoading || routineLoading}
                error={routineError}
                botOptions={routineBotOptions}
                selectedBotId={selectedRoutineBotId}
                onChangeBotId={(botId) => {
                  setSelectedRoutineBotId(botId);
                  setSelectedRoutineKey(null);
                  setSelectedRoutineHistoryId(null);
                }}
                onRetry={() => void refreshRoutines()}
                onSelectRoutine={(routine) => setSelectedRoutineKey(makeRoutineKey(routine.botId, routine.id))}
                onRunRoutine={async (routine) => {
                  await runRoutine(routine);
                  await refreshRoutines();
                }}
              />
            )}
          </CardContent>
        </Card>
      </div>

      <MyTaskDrawers
        selectedTaskId={selectedTaskId}
        onCloseTask={() => setSelectedTaskId(null)}
        taskRecords={taskRecords}
        ownerUserId={ownerUserId}
        selectedRoutine={selectedRoutine}
        selectedRoutineKey={selectedRoutineKey}
        onCloseRoutine={() => setSelectedRoutineKey(null)}
        selectedRoutineRuns={selectedRoutineRuns}
        selectedRoutineRunsLoading={selectedRoutineRunsLoading}
        selectedRoutineRunsError={selectedRoutineRunsError}
        selectedRoutineHistoryId={selectedRoutineHistoryId}
        onCloseRoutineHistory={() => setSelectedRoutineHistoryId(null)}
        historyRuns={historyRuns}
        historyLoading={historyLoading}
        historyError={historyError}
        onOpenRoutineFromHistory={openRoutineFromHistory}
      />
    </main>
  );
}
