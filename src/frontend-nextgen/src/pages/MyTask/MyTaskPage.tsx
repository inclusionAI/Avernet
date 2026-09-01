import { PageHeader } from '@/components/Common/PageHeader';
import { Card, CardContent } from '@/components/ui/Card';
import { Segmented } from '@/components/ui/Segmented';
import { useHumanIdentity } from '@/hooks/useHumanIdentity';
import { useOwnedBots } from '@/pages/Workspace/hooks/useOwnedBots';
import { useMemo, useState } from 'react';
import { MyTaskDrawers } from './components/MyTaskDrawers';
import { RoutineTaskTab } from './components/RoutineTaskTab';
import { UserTaskTab } from './components/UserTaskTab';
import { useMyTaskTasks } from './hooks/useMyTaskTasks';
import { ALL_ROUTINE_BOT_VALUE, makeRoutineKey, useRoutineTasks } from './hooks/useRoutineTasks';
import type { UserTaskStatusFilter } from './userTaskUtils';

const DEFAULT_PAGE = 1;
const DEFAULT_PAGE_SIZE = 10;

function normalizeBotNameKey(value?: string | null): string {
  return value?.trim().toLowerCase() ?? '';
}

export default function MyTaskPage() {
  const { identity: humanIdentity } = useHumanIdentity();
  const ownerUserId = humanIdentity?.userId.trim() ?? '';
  const [userTaskPage, setUserTaskPage] = useState(DEFAULT_PAGE);
  const [userTaskPageSize, setUserTaskPageSize] = useState(DEFAULT_PAGE_SIZE);
  const [userTaskStatusFilter, setUserTaskStatusFilter] = useState<UserTaskStatusFilter>('all');
  const [routinePage, setRoutinePage] = useState(DEFAULT_PAGE);
  const [routinePageSize, setRoutinePageSize] = useState(DEFAULT_PAGE_SIZE);

  const {
    taskRecords,
    total: userTaskTotal,
    loading: userLoading,
    error: userError,
    refresh: refreshUserTasks,
  } = useMyTaskTasks(ownerUserId, userTaskPage, userTaskPageSize, userTaskStatusFilter);
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
  const botNameMap = useMemo(() => {
    const map: Record<string, string> = {};
    chatBots.forEach((bot) => {
      const botName = bot.displayName || bot.botId;
      const botIdKey = normalizeBotNameKey(bot.botId);
      const realBotIdKey = normalizeBotNameKey(bot.realBotId);
      if (botIdKey) map[botIdKey] = botName;
      if (realBotIdKey) map[realBotIdKey] = botName;
    });
    return map;
  }, [chatBots]);
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
    total: routineTotal,
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
    routinePage,
    routinePageSize,
    selectedRoutineKey,
    Boolean(selectedRoutineHistoryId),
    // Bot 身份没有可用的 user_id（owner 聚合接口与 per-bot 接口都按 user 鉴权），保持静默空列表。
    activeTab === 'routine' && Boolean(ownerUserId),
  );

  const openRoutineFromHistory = (botId: string, routineId: string) => {
    setSelectedRoutineHistoryId(null);
    setSelectedRoutineKey(makeRoutineKey(botId, routineId));
    setActiveTab('routine');
  };

  return (
    <main className="app-scrollbar h-full min-h-0 overflow-y-auto">
      <div className="mx-auto flex w-full max-w-[1600px] flex-col gap-6 px-4 py-4 sm:px-6 sm:py-6 2xl:px-8">
        <PageHeader title="我的任务" />

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
            </div>

            {activeTab === 'user' ? (
              <UserTaskTab
                taskRecords={taskRecords}
                total={userTaskTotal}
                page={userTaskPage}
                pageSize={userTaskPageSize}
                loading={userLoading}
                error={userError}
                statusFilter={userTaskStatusFilter}
                onStatusFilterChange={(status) => {
                  setUserTaskStatusFilter(status);
                  setUserTaskPage(DEFAULT_PAGE);
                }}
                onRetry={() => void refreshUserTasks()}
                onSelectTask={setSelectedTaskId}
                selectedTaskId={selectedTaskId}
                onPageChange={setUserTaskPage}
                onPageSizeChange={(nextPageSize) => {
                  setUserTaskPageSize(nextPageSize);
                  setUserTaskPage(DEFAULT_PAGE);
                }}
                botNameMap={botNameMap}
              />
            ) : (
              <RoutineTaskTab
                routines={routines}
                total={routineTotal}
                page={routinePage}
                pageSize={routinePageSize}
                loading={ownedBotsLoading || routineLoading}
                error={routineError}
                botOptions={routineBotOptions}
                selectedBotId={selectedRoutineBotId}
                onChangeBotId={(botId) => {
                  setSelectedRoutineBotId(botId);
                  setSelectedRoutineKey(null);
                  setSelectedRoutineHistoryId(null);
                  setRoutinePage(DEFAULT_PAGE);
                }}
                onRetry={() => void refreshRoutines()}
                onSelectRoutine={(routine) =>
                  setSelectedRoutineKey(makeRoutineKey(routine.botId, routine.id, routine.runtimeStage))
                }
                onRunRoutine={async (routine) => {
                  await runRoutine(routine);
                  await refreshRoutines();
                }}
                onPageChange={setRoutinePage}
                onPageSizeChange={(nextPageSize) => {
                  setRoutinePageSize(nextPageSize);
                  setRoutinePage(DEFAULT_PAGE);
                }}
                botNameMap={botNameMap}
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
        botNameMap={botNameMap}
      />
    </main>
  );
}
