import { PageHeader } from '@/components/Common/PageHeader';
import { Card, CardContent } from '@/components/ui/Card';
import { Skeleton } from '@/components/ui/Skeleton';
import type { IdentityView } from '@/domain/collaboration';
import { useHumanIdentity } from '@/hooks/useHumanIdentity';
import { useWorkIdentityAccess } from '@/hooks/useWorkIdentityAccess';
import { useOwnedBots } from '@/pages/Workspace/hooks/useOwnedBots';
import { useEffect, useMemo, useState } from 'react';
import { MyTaskDrawers } from './components/MyTaskDrawers';
import { RoutineTaskTab } from './components/RoutineTaskTab';
import { UserTaskTab } from './components/UserTaskTab';
import { useMyTaskTasks } from './hooks/useMyTaskTasks';
import { makeRoutineKey, useRoutineTasks, type RoutineBotTarget } from './hooks/useRoutineTasks';
import type { UserTaskStatusFilter } from './userTaskUtils';

const DEFAULT_PAGE = 1;
const DEFAULT_PAGE_SIZE = 10;

function normalizeBotNameKey(value?: string | null): string {
  return value?.trim().toLowerCase() ?? '';
}

function getActiveRoutineBot(activeIdentity: IdentityView | null): RoutineBotTarget | null {
  if (activeIdentity?.kind !== 'bot') return null;
  const botId = activeIdentity.id.split(':', 1)[0]?.trim() ?? '';
  if (!botId) return null;
  return { botId, botName: activeIdentity.displayName || botId };
}

export default function MyTaskPage() {
  const { identity: humanIdentity, status: humanIdentityStatus } = useHumanIdentity();
  const { activeIdentity, activeIdentityKind } = useWorkIdentityAccess();
  const ownerUserId = humanIdentity?.userId.trim() ?? '';
  const isUserIdentity = activeIdentityKind === 'user';
  const isBotIdentity = activeIdentityKind === 'bot';
  const currentRoutineBot = useMemo(() => getActiveRoutineBot(activeIdentity), [activeIdentity]);
  const routineBots = useMemo(() => (currentRoutineBot ? [currentRoutineBot] : []), [currentRoutineBot]);
  const selectedRoutineBotId = currentRoutineBot?.botId ?? '';

  const [userTaskPage, setUserTaskPage] = useState(DEFAULT_PAGE);
  const [userTaskPageSize, setUserTaskPageSize] = useState(DEFAULT_PAGE_SIZE);
  const [userTaskStatusFilter, setUserTaskStatusFilter] = useState<UserTaskStatusFilter>('all');
  const [routinePage, setRoutinePage] = useState(DEFAULT_PAGE);
  const [routinePageSize, setRoutinePageSize] = useState(DEFAULT_PAGE_SIZE);
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const [selectedRoutineKey, setSelectedRoutineKey] = useState<string | null>(null);
  const [selectedRoutineHistoryId, setSelectedRoutineHistoryId] = useState<string | null>(null);

  const {
    taskRecords,
    total: userTaskTotal,
    loading: userLoading,
    error: userError,
    refresh: refreshUserTasks,
  } = useMyTaskTasks(
    ownerUserId,
    userTaskPage,
    userTaskPageSize,
    userTaskStatusFilter,
    isUserIdentity && Boolean(ownerUserId),
  );
  const { chatBots, isLoading: ownedBotsLoading } = useOwnedBots(
    ownerUserId || null,
    isUserIdentity && Boolean(ownerUserId),
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
    if (currentRoutineBot) map[normalizeBotNameKey(currentRoutineBot.botId)] = currentRoutineBot.botName;
    return map;
  }, [chatBots, currentRoutineBot]);
  const routineBotOptions = useMemo(
    () => routineBots.map((bot) => ({ value: bot.botId, label: bot.botName })),
    [routineBots],
  );

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
    isBotIdentity && Boolean(selectedRoutineBotId) && Boolean(ownerUserId),
  );

  useEffect(() => {
    setUserTaskPage(DEFAULT_PAGE);
    setUserTaskStatusFilter('all');
    setRoutinePage(DEFAULT_PAGE);
    setSelectedTaskId(null);
    setSelectedRoutineKey(null);
    setSelectedRoutineHistoryId(null);
  }, [activeIdentity?.id]);

  const openRoutineFromHistory = (botId: string, routineId: string) => {
    setSelectedRoutineHistoryId(null);
    setSelectedRoutineKey(makeRoutineKey(botId, routineId));
  };

  return (
    <main className="app-scrollbar h-full min-h-0 overflow-y-auto">
      <div className="mx-auto flex w-full max-w-[1600px] flex-col gap-6 px-4 py-4 sm:px-6 sm:py-6 2xl:px-8">
        <PageHeader title="我的任务" />

        <Card>
          <CardContent className="space-y-4 pt-5">
            {isUserIdentity ? (
              <UserTaskTab
                taskRecords={taskRecords}
                total={userTaskTotal}
                page={userTaskPage}
                pageSize={userTaskPageSize}
                loading={humanIdentityStatus === 'loading' || ownedBotsLoading || userLoading}
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
            ) : isBotIdentity ? (
              <RoutineTaskTab
                routines={routines}
                total={routineTotal}
                page={routinePage}
                pageSize={routinePageSize}
                loading={humanIdentityStatus === 'loading' || routineLoading}
                error={routineError}
                botOptions={routineBotOptions}
                selectedBotId={selectedRoutineBotId}
                showBotSelector={false}
                onChangeBotId={() => {}}
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
            ) : (
              <div role="status" className="space-y-3 py-2">
                <span className="sr-only">工作身份加载中</span>
                <Skeleton.Block className="h-12 w-full rounded-lg" />
                <Skeleton.Block className="h-64 w-full rounded-lg" />
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      <MyTaskDrawers
        selectedTaskId={isUserIdentity ? selectedTaskId : null}
        onCloseTask={() => setSelectedTaskId(null)}
        taskRecords={taskRecords}
        ownerUserId={ownerUserId}
        selectedRoutine={isBotIdentity ? selectedRoutine : null}
        selectedRoutineKey={isBotIdentity ? selectedRoutineKey : null}
        onCloseRoutine={() => setSelectedRoutineKey(null)}
        selectedRoutineRuns={selectedRoutineRuns}
        selectedRoutineRunsLoading={selectedRoutineRunsLoading}
        selectedRoutineRunsError={selectedRoutineRunsError}
        selectedRoutineHistoryId={isBotIdentity ? selectedRoutineHistoryId : null}
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
