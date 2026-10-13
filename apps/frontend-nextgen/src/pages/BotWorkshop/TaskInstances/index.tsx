import { PageHeader } from '@/components/Common/PageHeader';
import { Button } from '@/components/ui/Button';
import { Empty } from '@/components/ui/Empty';
import { MyTaskDrawers } from '@/pages/MyTask/components/MyTaskDrawers';
import { RoutineTaskTab } from '@/pages/MyTask/components/RoutineTaskTab';
import { makeRoutineKey, useRoutineTasks, type RoutineBotTarget } from '@/pages/MyTask/hooks/useRoutineTasks';
import { history, useLocation } from '@umijs/max';
import { ArrowLeft } from 'lucide-react';
import React, { useMemo, useState } from 'react';

const DEFAULT_PAGE = 1;
const DEFAULT_PAGE_SIZE = 10;

/**
 * 从 Bot 管理列表进入的受限任务实例页。
 * 列表、执行、详情和运行历史均复用原「任务列表」的定时任务实现；请求仍由
 * `/openapi/v1/bots/{bot_id}/routines` 的 MEMBER 权限校验，URL 不是授权凭据。
 */
const BotTaskInstancesPage: React.FC = () => {
  const params = new URLSearchParams(useLocation().search);
  const botId = params.get('bot_id')?.trim() ?? '';
  const botName = params.get('bot_name')?.trim() || botId;
  const routineBots = useMemo<RoutineBotTarget[]>(() => (botId ? [{ botId, botName }] : []), [botId, botName]);
  const [page, setPage] = useState(DEFAULT_PAGE);
  const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE);
  const [selectedRoutineKey, setSelectedRoutineKey] = useState<string | null>(null);
  const [selectedRoutineHistoryId, setSelectedRoutineHistoryId] = useState<string | null>(null);
  const {
    routines,
    loading,
    error,
    total,
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
    botId,
    routineBots,
    page,
    pageSize,
    selectedRoutineKey,
    Boolean(selectedRoutineHistoryId),
    Boolean(botId),
  );

  if (!botId) {
    return (
      <Empty
        title="缺少 Bot 标识"
        description="请从 Bot 管理列表重新进入任务实例。"
        action={<Button onClick={() => history.push('/bot-workshop')}>返回 Bot 管理</Button>}
      />
    );
  }

  return (
    <main className="app-scrollbar h-full min-h-0 overflow-y-auto">
      <div className="mx-auto flex w-full max-w-[1600px] flex-col gap-6 px-4 py-4 sm:px-6 sm:py-6 2xl:px-8">
        <div className="flex items-start gap-3">
          <Button
            variant="ghost"
            size="icon"
            aria-label="返回 Bot 管理"
            onClick={() => history.push('/bot-workshop')}
            leftIcon={<ArrowLeft className="size-4" />}
          />
          <PageHeader title="任务实例" description={`仅展示 Bot「${botName}」的定时任务与执行实例`} />
        </div>
        <RoutineTaskTab
          routines={routines}
          total={total}
          page={page}
          pageSize={pageSize}
          loading={loading}
          error={error}
          botOptions={routineBots.map((bot) => ({ value: bot.botId, label: bot.botName }))}
          selectedBotId={botId}
          showBotSelector={false}
          onChangeBotId={() => {}}
          onRetry={() => void refreshRoutines()}
          onSelectRoutine={(routine) => setSelectedRoutineKey(makeRoutineKey(routine.botId, routine.id, routine.runtimeStage))}
          onRunRoutine={async (routine) => {
            await runRoutine(routine);
            await refreshRoutines();
          }}
          onPageChange={setPage}
          onPageSizeChange={(nextPageSize) => {
            setPageSize(nextPageSize);
            setPage(DEFAULT_PAGE);
          }}
          botNameMap={{ [botId.toLowerCase()]: botName }}
        />
      </div>
      <MyTaskDrawers
        selectedTaskId={null}
        onCloseTask={() => {}}
        taskRecords={[]}
        ownerUserId=""
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
        onOpenRoutineFromHistory={(targetBotId, routineId) => {
          setSelectedRoutineHistoryId(null);
          setSelectedRoutineKey(makeRoutineKey(targetBotId, routineId));
        }}
        botNameMap={{ [botId.toLowerCase()]: botName }}
      />
    </main>
  );
};

export default BotTaskInstancesPage;
