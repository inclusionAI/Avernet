import { BotSubpageShell } from '@/components/BotWorkshop/BotSubpageShell';
import { Button } from '@/components/ui/Button';
import { Empty } from '@/components/ui/Empty';
import { MyTaskDrawers } from '@/pages/MyTask/components/MyTaskDrawers';
import { RoutineTaskTab } from '@/pages/MyTask/components/RoutineTaskTab';
import { makeRoutineKey, useRoutineTasks, type RoutineBotTarget } from '@/pages/MyTask/hooks/useRoutineTasks';
import { history, useLocation } from '@umijs/max';
import React, { useMemo, useState } from 'react';

const DEFAULT_PAGE = 1;
const DEFAULT_PAGE_SIZE = 10;

/**
 * 从 Bot 管理列表进入的受限任务实例页。
 * 列表、执行、详情和运行历史均复用原「任务列表」的定时任务实现；请求仍由
 * `/openapi/v1/bots/{bot_id}/routines` 的 MEMBER 权限校验，URL 不是授权凭据。
 * 页面壳走 BotSubpageShell（D6：RoutineTaskTab 内部不改，仅壳层白卡包裹）。
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
    <BotSubpageShell
      onBack={() => history.push('/bot-workshop')}
      botName={botName}
      title="任务实例"
      subtitle={`Bot「${botName}」的定时任务与执行实例`}
    >
      <section className="rounded-xl border border-border/60 bg-card p-4 sm:p-6">
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
      </section>
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
    </BotSubpageShell>
  );
};

export default BotTaskInstancesPage;