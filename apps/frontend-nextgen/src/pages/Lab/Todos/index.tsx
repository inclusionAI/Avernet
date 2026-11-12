import { PageHeader } from '@/components/Common/PageHeader';
import type { TodoBotOption } from '@/domain/todos/models';
import { useHumanIdentity } from '@/hooks/useHumanIdentity';
import { useSpaceContext, useSpaceContextActions } from '@/hooks/useSpaceContext';
import { useTodos } from '@/hooks/useTodos';
import { botWorkshopService } from '@/services/botWorkshop';
import { useTodoStore } from '@/stores/todoStore';
import { safeReportError } from '@/utils/errorToast';
import { useEffect, useState } from 'react';
import { eligibleTodoBots } from './botScope';
import { TodoCreateModal } from './components/TodoCreateModal';
import { TodoDetailDrawer } from './components/TodoDetailDrawer';
import { TodoTable } from './components/TodoTable';
import { TodoToolbar } from './components/TodoToolbar';

/** 实验室待办工作台：当前空间内的待办列表、事件时序、互动和验收。 */
export default function LabTodosPage() {
  const { init } = useSpaceContextActions();
  const currentSpace = useSpaceContext((state) => state.currentSpace);
  const currentSpaceId = useSpaceContext((state) => state.currentSpaceId);
  const spaceInitialized = useSpaceContext((state) => state.initialized);
  const { identity: humanIdentity } = useHumanIdentity();
  const currentUserId = humanIdentity?.userId.trim() || undefined;
  const [selectedTodoId, setSelectedTodoId] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [bots, setBots] = useState<TodoBotOption[]>([]);
  const [botsLoading, setBotsLoading] = useState(false);

  const todos = useTodos();
  const selectedTodo = useTodoStore((state) => state.selectedTodo);

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

  useEffect(() => {
    if (selectedTodoId && currentSpaceId !== undefined && selectedTodo && selectedTodo.spaceId !== currentSpaceId) {
      setSelectedTodoId(null);
    }
  }, [currentSpaceId, selectedTodo, selectedTodoId]);

  return (
    <main className="app-scrollbar h-full min-h-0 overflow-y-auto">
      <div className="mx-auto flex w-full max-w-[1600px] flex-col gap-6 px-4 py-4 sm:px-6 sm:py-6 2xl:px-8">
        <PageHeader title="待办" description="当前空间的 Bot 待办。系统自动调度执行，完成后可查看事件、互动并提交验收。" />

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

        <TodoTable
          todos={todos.todos}
          total={todos.total}
          page={todos.page}
          pageSize={todos.pageSize}
          loading={todos.loading || !spaceInitialized}
          error={todos.error}
          onRetry={() => void todos.refresh()}
          onSelect={setSelectedTodoId}
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

      <TodoDetailDrawer
        todoId={selectedTodoId}
        currentSpace={currentSpace}
        onClose={() => setSelectedTodoId(null)}
        onListRefresh={todos.refresh}
      />
    </main>
  );
}
