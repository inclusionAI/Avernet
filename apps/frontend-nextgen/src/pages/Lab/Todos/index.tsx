import { PageHeader } from '@/components/Common/PageHeader';
import type { BotTeamRecord, TodoBotOption, TodoExecutorOption, TodoOwner } from '@/domain/todos/models';
import { useHumanIdentity } from '@/hooks/useHumanIdentity';
import { useTodoSpaces } from '@/hooks/useTodoSpaces';
import { useTodos } from '@/hooks/useTodos';
import { loadBotTeams, loadTodoBots, loadTodoOwners } from '@/services/todos/todoService';
import { safeReportError } from '@/utils/errorToast';
import { history } from '@umijs/max';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { eligibleTodoBots } from './botScope';
import { BotTeamPage } from './components/BotTeamPage';
import { TodoAutomationPage } from './components/TodoAutomationPage';
import { TodoBoard } from './components/TodoBoard';
import { TodoCreateModal } from './components/TodoCreateModal';
import { TodoDimaImportModal } from './components/TodoDimaImportModal';
import { TodoToolbar, todoExecutorFilterValue } from './components/TodoToolbar';

type TodoWorkbenchView = 'list' | 'bot-teams' | 'automation';

/** 实验室待办工作台：待办列表、Bot 小队、Dima 导入与自动化入口。 */
export default function LabTodosPage() {
  const {
    currentSpace,
    currentSpaceId,
    initialized: spaceInitialized,
    loading: spaceLoading,
    error: spaceError,
    refresh: refreshSpaces,
  } = useTodoSpaces();
  const { identity: humanIdentity } = useHumanIdentity();
  const currentUserId = humanIdentity?.userId.trim() || undefined;
  const currentUserName = humanIdentity?.displayName.trim() || currentUserId;
  const [view, setView] = useState<TodoWorkbenchView>('list');
  const [createOpen, setCreateOpen] = useState(false);
  const [dimaImportOpen, setDimaImportOpen] = useState(false);
  const [executorFilter, setExecutorFilter] = useState<string>();
  const [bots, setBots] = useState<TodoBotOption[]>([]);
  const [botTeams, setBotTeams] = useState<BotTeamRecord[]>([]);
  const [owners, setOwners] = useState<TodoOwner[]>([]);

  const todos = useTodos({ currentSpaceId, initialized: spaceInitialized });

  useEffect(() => {
    const isTeamSpace = currentSpace?.spaceType === 'TEAM';
    const personalScopeReady = currentSpace?.spaceType === 'PERSONAL' && Boolean(currentUserId);
    if (!spaceInitialized || !currentSpace || currentSpaceId === undefined || (!isTeamSpace && !personalScopeReady)) {
      setBots([]);
      return;
    }
    let aborted = false;
    void loadTodoBots(currentSpaceId)
      .then((items) => {
        if (aborted) return;
        setBots(eligibleTodoBots(items, currentSpace, currentUserId));
      })
      .catch((cause) => {
        if (aborted) return;
        setBots([]);
        safeReportError(cause, { title: '当前空间 Bot 加载失败' });
      });
    return () => {
      aborted = true;
    };
  }, [currentSpace, currentSpaceId, currentUserId, spaceInitialized]);

  const refreshBotTeams = useCallback(async () => {
    if (currentSpaceId === undefined) {
      setBotTeams([]);
      return;
    }
    try {
      const page = await loadBotTeams(currentSpaceId, 'active');
      setBotTeams(page.items);
    } catch (cause) {
      setBotTeams([]);
      safeReportError(cause, { title: 'Bot 小队加载失败' });
    }
  }, [currentSpaceId]);

  useEffect(() => {
    if (!spaceInitialized) return;
    void refreshBotTeams();
  }, [refreshBotTeams, spaceInitialized]);

  useEffect(() => {
    const fallbackOwner: TodoOwner[] = currentUserId
      ? [{ userId: currentUserId, userName: currentUserName || currentUserId }]
      : [];
    if (!spaceInitialized || currentSpaceId === undefined || !currentUserId) {
      setOwners(fallbackOwner);
      return;
    }
    let aborted = false;
    void loadTodoOwners(currentSpaceId)
      .then((members) => {
        if (aborted) return;
        setOwners(members.length ? members : fallbackOwner);
      })
      .catch((cause) => {
        if (aborted) return;
        setOwners(fallbackOwner);
        safeReportError(cause, { title: '空间成员加载失败' });
      });
    return () => {
      aborted = true;
    };
  }, [currentSpaceId, currentUserId, currentUserName, spaceInitialized]);

  const executorOptions = useMemo<TodoExecutorOption[]>(
    () => [
      ...bots.map((bot) => ({ type: 'bot' as const, id: bot.botId, name: bot.botName })),
      ...botTeams.flatMap((team) =>
        team.publishedRevisionId
          ? [
              {
                type: 'bot_team' as const,
                id: team.botTeamId,
                revisionId: team.publishedRevisionId,
                name: team.name,
                description: team.description,
              },
            ]
          : [],
      ),
    ],
    [botTeams, bots],
  );

  useEffect(() => {
    if (!executorFilter) return;
    if (!executorOptions.some((executor) => todoExecutorFilterValue(executor) === executorFilter)) {
      setExecutorFilter(undefined);
    }
  }, [executorFilter, executorOptions]);

  const visibleTodos = useMemo(() => {
    if (!executorFilter) return todos.todos;
    const separator = executorFilter.indexOf(':');
    if (separator < 0) return todos.todos;
    const type = executorFilter.slice(0, separator);
    const id = executorFilter.slice(separator + 1);
    return todos.todos.filter((todo) => (type === 'bot_team' ? todo.templateId === id : todo.botId === id));
  }, [executorFilter, todos.todos]);

  if (view === 'bot-teams') {
    return (
      <main className="app-scrollbar h-full min-h-0 overflow-y-auto">
        <div className="mx-auto flex w-full max-w-[1600px] flex-col gap-4 px-6 py-6">
          <BotTeamPage
            spaceId={currentSpaceId}
            bots={bots}
            onBack={() => setView('list')}
            onChanged={() => void refreshBotTeams()}
          />
        </div>
      </main>
    );
  }

  if (view === 'automation') {
    return (
      <main className="app-scrollbar h-full min-h-0 overflow-y-auto">
        <div className="mx-auto flex w-full max-w-[1600px] flex-col gap-4 px-6 py-6">
          <TodoAutomationPage spaceId={currentSpaceId} bots={bots} onBack={() => setView('list')} />
        </div>
      </main>
    );
  }

  return (
    <main className="app-scrollbar h-full min-h-0 overflow-y-auto">
      <div className="mx-auto flex w-full max-w-[1600px] flex-col gap-4 px-6 py-6">
        <PageHeader
          title="待办"
          compact
          description="在同一空间内，让 Bot 与团队协作，与团队一起完成工作"
        />

        <TodoToolbar
          executors={executorOptions}
          keyword={todos.keyword}
          status={todos.status}
          executorFilter={executorFilter}
          total={todos.total}
          onKeywordChange={todos.setKeyword}
          onStatusChange={todos.setStatus}
          onExecutorFilterChange={setExecutorFilter}
          onAutomation={() => setView('automation')}
          onBotTeams={() => setView('bot-teams')}
          onImportDima={() => setDimaImportOpen(true)}
          onCreate={() => setCreateOpen(true)}
        />

        <TodoBoard
          todos={visibleTodos}
          total={todos.total}
          page={todos.page}
          pageSize={todos.pageSize}
          loading={
            todos.loading ||
            spaceLoading ||
            (!spaceInitialized && !spaceError)
          }
          error={spaceInitialized ? todos.error : (spaceError ?? todos.error)}
          onRetry={() => {
            if (!spaceInitialized) {
              void refreshSpaces();
              return;
            }
            void todos.refresh();
          }}
          onSelect={(todoId) => history.push(`/lab/todos/${encodeURIComponent(todoId)}`)}
          onPageChange={todos.setPage}
        />
      </div>

      <TodoCreateModal
        open={createOpen}
        executorOptions={executorOptions}
        owners={owners}
        loading={todos.mutationLoading}
        onOpenChange={setCreateOpen}
        onSubmit={async (input) => Boolean(await todos.create(input))}
      />

      <TodoDimaImportModal
        open={dimaImportOpen}
        spaceId={currentSpaceId}
        executorOptions={executorOptions}
        owners={owners}
        onOpenChange={setDimaImportOpen}
        onImported={todos.refresh}
      />
    </main>
  );
}
