import { PageHeader } from '@/components/Common/PageHeader';
import type { TodoBotOption } from '@/domain/todos/models';
import { useHumanIdentity } from '@/hooks/useHumanIdentity';
import { useSpaceContext, useSpaceContextActions } from '@/hooks/useSpaceContext';
import { useTodos } from '@/hooks/useTodos';
import { botWorkshopService } from '@/services/botWorkshop';
import { safeReportError } from '@/utils/errorToast';
import { history } from '@umijs/max';
import { useEffect, useState } from 'react';
import { eligibleTodoBots } from './botScope';
import { TodoBoard } from './components/TodoBoard';
import { TodoCreateModal } from './components/TodoCreateModal';
import { TodoToolbar } from './components/TodoToolbar';

/** 实验室待办工作台：当前空间内的待办列表、事件时序、互动和验收。 */
export default function LabTodosPage() {
  const { init } = useSpaceContextActions();
  const currentSpace = useSpaceContext((state) => state.currentSpace);
  const currentSpaceId = useSpaceContext((state) => state.currentSpaceId);
  const spaceInitialized = useSpaceContext((state) => state.initialized);
  const { identity: humanIdentity } = useHumanIdentity();
  const currentUserId = humanIdentity?.userId.trim() || undefined;
  const [createOpen, setCreateOpen] = useState(false);
  const [bots, setBots] = useState<TodoBotOption[]>([]);
  const [botsLoading, setBotsLoading] = useState(false);

  const todos = useTodos();

  useEffect(() => {
    if (!spaceInitialized) void init();
  }, [init, spaceInitialized]);

  useEffect(() => {
    const isTeamSpace = currentSpace?.spaceType === 'TEAM';
    const personalScopeReady = currentSpace?.spaceType === 'PERSONAL' && Boolean(currentUserId);
    if (!spaceInitialized || !currentSpace || currentSpaceId === undefined || (!isTeamSpace && !personalScopeReady)) {
      setBots([]);
      return;
    }
    let aborted = false;
    setBotsLoading(true);
    botWorkshopService
      .list({ spaceId: String(currentSpaceId), page: 1, pageSize: 100 })
      .then((result) => {
        if (aborted) return;
        setBots(eligibleTodoBots(result.items, currentSpace, currentUserId));
      })
      .catch((cause) => {
        if (aborted) return;
        setBots([]);
        safeReportError(cause, { title: '当前空间 Bot 加载失败' });
      })
      .finally(() => {
        if (!aborted) setBotsLoading(false);
      });
    return () => {
      aborted = true;
    };
  }, [currentSpace, currentSpaceId, currentUserId, spaceInitialized]);

  return (
    <main className="app-scrollbar h-full min-h-0 overflow-y-auto">
      <div className="mx-auto flex w-full max-w-[1600px] flex-col gap-4 px-6 py-6">
        <PageHeader
          title="待办"
          compact
          description="当前空间的 Bot 待办。系统自动调度执行，完成后可查看事件、互动并提交验收。"
        />

        <TodoToolbar
          space={currentSpace}
          bots={bots}
          botsLoading={botsLoading}
          keyword={todos.keyword}
          status={todos.status}
          botId={todos.botId}
          onKeywordChange={todos.setKeyword}
          onStatusChange={todos.setStatus}
          onBotIdChange={todos.setBotId}
          onCreate={() => setCreateOpen(true)}
        />

        <TodoBoard
          todos={todos.todos}
          total={todos.total}
          page={todos.page}
          pageSize={todos.pageSize}
          loading={todos.loading || !spaceInitialized}
          error={todos.error}
          onRetry={() => void todos.refresh()}
          onSelect={(todoId) => history.push(`/lab/todos/${encodeURIComponent(todoId)}`)}
          onPageChange={todos.setPage}
          onPageSizeChange={todos.setPageSize}
        />
      </div>

      <TodoCreateModal
        open={createOpen}
        bots={bots}
        loading={botsLoading}
        onOpenChange={setCreateOpen}
        onSubmit={async (input) => Boolean(await todos.create(input))}
      />
    </main>
  );
}
