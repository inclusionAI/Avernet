import { Button } from '@/components/ui/Button';
import { Empty } from '@/components/ui/Empty';
import { Input } from '@/components/ui/Input';
import { Segmented } from '@/components/ui/Segmented';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/Select';
import { HELP_STATUS } from '@/domain/communityHelp/model';
import { HELP_SOURCES, type HelpBoard, type HelpSource, type HelpTaskStatus } from '@/domain/communityHelp/types';
import { useCommunityHelpMock } from '@/hooks/useCommunityHelpMock';
import { Bot, CircleHelp, Layers3, LayoutGrid, List, Plus, Search, Settings2 } from 'lucide-react';
import { useState } from 'react';
import { HelpBoardDialog } from './HelpBoardDialog';
import { HelpTaskCard } from './HelpTaskCard';
import { HelpTaskDialog } from './HelpTaskDialog';
import { HelpTaskList } from './HelpTaskList';
import { HelpTaskPanel } from './HelpTaskPanel';

export function CommunityHelpCenter() {
  const help = useCommunityHelpMock();
  const [boardDialog, setBoardDialog] = useState<{ board: HelpBoard | null } | null>(null);
  const [taskDialogOpen, setTaskDialogOpen] = useState(false);
  const [view, setView] = useState<'card' | 'list'>('card');
  return (
    <section className="min-w-0 space-y-5" aria-label="求助_new 工作台">
      <nav className="flex flex-wrap items-center gap-3" aria-label="求助模版导航">
        <div className="min-w-0 max-w-full overflow-x-auto">
          <Segmented
            value={help.board.id}
            onChange={help.selectBoard}
            aria-label="求助模版"
            options={help.boards.map((board) => ({
              value: board.id,
              label: board.name,
              icon: <Layers3 className="size-3.5" aria-hidden />,
            }))}
          />
        </div>
        <Button
          variant="ghost"
          size="sm"
          className="text-muted-foreground"
          leftIcon={<Plus className="size-3.5" aria-hidden />}
          onClick={() => setBoardDialog({ board: null })}
        >
          新增模版
        </Button>
      </nav>
      <div className="rounded-2xl border border-border bg-card p-5 sm:p-6">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
          <div className="flex min-w-0 flex-1 items-start gap-3">
            <div className="flex size-11 shrink-0 items-center justify-center rounded-xl bg-primary/10 text-primary">
              <CircleHelp className="size-6" aria-hidden />
            </div>
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <h3 className="break-words text-base font-semibold">{help.board.name}</h3>
                <span className="rounded-md bg-muted px-2 py-0.5 text-xs text-muted-foreground">AiCoding</span>
              </div>
              <p className="mt-1 break-words text-xs leading-6 text-muted-foreground">
                {help.board.description || '为开发问题组建 Bot 协作团队，跟踪从需求到交付的完整过程。'}
              </p>
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button
              variant="outline"
              leftIcon={<Settings2 className="size-3.5" aria-hidden />}
              onClick={() => setBoardDialog({ board: help.board })}
            >
              模版设置
            </Button>
            <Button leftIcon={<Plus className="size-3.5" aria-hidden />} onClick={() => setTaskDialogOpen(true)}>
              新建求助任务
            </Button>
          </div>
        </div>
        <div className="mt-5 flex flex-wrap gap-2 border-t border-border pt-4" aria-label="模版协作团队">
          <span className="flex items-center gap-1.5 py-1 text-xs text-muted-foreground">
            <Bot className="size-3.5" aria-hidden />
            协作团队
          </span>
          {help.board.collaborationMode === 'open' ? (
            <span className="rounded-md bg-primary/10 px-2.5 py-1 text-xs text-primary">开放协作 · 不限定特定 Bot</span>
          ) : (
            help.board.members.map((member) => (
              <span
                key={member.botId}
                className="max-w-full break-words rounded-md border border-border bg-background px-2.5 py-1 text-xs"
              >
                {help.bots.find((bot) => bot.id === member.botId)?.name}{' '}
                <span className="text-muted-foreground">· {member.roles.join(' / ')}</span>
              </span>
            ))
          )}
        </div>
      </div>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4" aria-label="模版任务统计">
        {(Object.keys(HELP_STATUS) as HelpTaskStatus[]).map((status) => (
          <div key={status} className="rounded-xl border border-border bg-card px-4 py-3">
            <span className="text-xs text-muted-foreground">{HELP_STATUS[status].label}</span>
            <p className="mt-1 text-2xl font-semibold tabular-nums">
              {help.boardTasks.filter((task) => task.status === status).length}
            </p>
          </div>
        ))}
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <div className="min-w-0 max-w-full overflow-x-auto">
          <Segmented<HelpTaskStatus | 'all'>
            value={help.status}
            onChange={help.setStatus}
            aria-label="求助任务状态"
            className="bg-background"
            options={[
              { value: 'all', label: '全部任务' },
              ...(Object.keys(HELP_STATUS) as HelpTaskStatus[]).map((value) => ({
                value,
                label: HELP_STATUS[value].label,
              })),
            ]}
          />
        </div>
        <div className="flex w-full flex-wrap gap-2 sm:ml-auto sm:w-auto">
          <Select value={help.source} onValueChange={(value) => help.setSource(value as HelpSource | 'all')}>
            <SelectTrigger aria-label="筛选任务来源" className="w-36">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">全部来源</SelectItem>
              {HELP_SOURCES.map((source) => (
                <SelectItem key={source} value={source}>
                  {source}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <div className="relative min-w-40 flex-1 sm:w-56">
            <Search
              className="pointer-events-none absolute left-3 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground"
              aria-hidden
            />
            <Input
              value={help.query}
              onChange={(event) => help.setQuery(event.target.value)}
              aria-label="搜索求助任务"
              placeholder="搜索任务、描述或来源编号…"
              className="h-9 pl-9"
            />
          </div>
        </div>
      </div>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted-foreground">
          <span>共 {help.tasks.length} 个任务</span>
          <span>按创建顺序展示 · Mock 数据</span>
        </div>
        <Segmented
          value={view}
          onChange={setView}
          aria-label="任务视图"
          options={[
            { value: 'card', label: '卡片视图', icon: <LayoutGrid className="size-3.5" aria-hidden /> },
            { value: 'list', label: '列表视图', icon: <List className="size-3.5" aria-hidden /> },
          ]}
        />
      </div>
      {help.tasks.length ? (
        view === 'card' ? (
          <div className="grid gap-4 lg:grid-cols-2 2xl:grid-cols-3">
            {help.tasks.map((task) => (
              <HelpTaskCard key={task.id} task={task} onOpen={() => help.selectTask(task.id)} />
            ))}
          </div>
        ) : (
          <HelpTaskList tasks={help.tasks} onOpen={help.selectTask} />
        )
      ) : (
        <Empty
          title={help.boardTasks.length ? '没有匹配的求助任务' : '这个模版还没有求助任务'}
          description="创建一项求助，让配置好的 Bot 团队开始模拟协作。"
          action={
            help.boardTasks.length ? (
              <Button variant="outline" onClick={help.clearFilters}>
                清除筛选
              </Button>
            ) : (
              <Button onClick={() => setTaskDialogOpen(true)}>新建求助任务</Button>
            )
          }
        />
      )}
      {boardDialog && (
        <HelpBoardDialog
          board={boardDialog.board}
          bots={help.bots}
          onClose={() => setBoardDialog(null)}
          onSave={help.saveBoard}
        />
      )}
      {taskDialogOpen && (
        <HelpTaskDialog board={help.board} onClose={() => setTaskDialogOpen(false)} onCreate={help.createTask} />
      )}
      {help.selectedTask && (
        <HelpTaskPanel
          key={help.selectedTask.id}
          task={help.selectedTask}
          boardName={help.board.name}
          onClose={() => help.selectTask(null)}
          onAdvance={help.advanceTask}
        />
      )}
    </section>
  );
}
