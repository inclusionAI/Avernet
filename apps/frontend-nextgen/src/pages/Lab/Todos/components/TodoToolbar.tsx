import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/Select';
import type { TodoExecutorOption, TodoStatus } from '@/domain/todos/models';
import { TODO_STATUS_LABEL } from '@/domain/todos/status';
import { Bot, Download, Plus, Search, Workflow } from 'lucide-react';

interface TodoToolbarProps {
  executors: TodoExecutorOption[];
  keyword: string;
  status?: TodoStatus;
  executorFilter?: string;
  total: number;
  onKeywordChange: (value: string) => void;
  onStatusChange: (value: TodoStatus | undefined) => void;
  onExecutorFilterChange: (value: string | undefined) => void;
  onAutomation: () => void;
  onBotTeams: () => void;
  onImportDima: () => void;
  onCreate: () => void;
}

const STATUS_VALUES: TodoStatus[] = [
  'not_started',
  'in_progress',
  'pending_action',
  'interrupted',
  'completed',
  'cancelled',
];

export function todoExecutorFilterValue(executor: Pick<TodoExecutorOption, 'type' | 'id'>): string {
  return `${executor.type}:${executor.id}`;
}

export function TodoToolbar({
  executors,
  keyword,
  status,
  executorFilter,
  total,
  onKeywordChange,
  onStatusChange,
  onExecutorFilterChange,
  onAutomation,
  onBotTeams,
  onImportDima,
  onCreate,
}: TodoToolbarProps) {
  const bots = executors.filter((item) => item.type === 'bot');
  const botTeams = executors.filter((item) => item.type === 'bot_team');

  return (
    <section role="region" aria-label="待办筛选">
      <div className="flex flex-wrap items-center gap-3">
        <Select
          value={status ?? 'all'}
          onValueChange={(value) => onStatusChange(value === 'all' ? undefined : (value as TodoStatus))}
        >
          <SelectTrigger aria-label="待办状态" className="h-8 w-40 rounded-lg text-xs">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">全部状态 ({total})</SelectItem>
            {STATUS_VALUES.map((value) => (
              <SelectItem key={value} value={value}>
                {TODO_STATUS_LABEL[value]}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        <Select
          value={executorFilter ?? 'all'}
          onValueChange={(value) => onExecutorFilterChange(value === 'all' ? undefined : value)}
        >
          <SelectTrigger aria-label="负责人" className="h-8 w-40 rounded-lg text-xs">
            <SelectValue placeholder="全部负责人" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">全部负责人</SelectItem>
            {bots.length ? (
              <SelectGroup>
                <div className="px-2 pb-1 pt-2 text-[11px] font-medium text-muted-foreground">Bot</div>
                {bots.map((executor) => (
                  <SelectItem key={todoExecutorFilterValue(executor)} value={todoExecutorFilterValue(executor)}>
                    {executor.name}
                  </SelectItem>
                ))}
              </SelectGroup>
            ) : null}
            {botTeams.length ? (
              <SelectGroup>
                <div className="px-2 pb-1 pt-2 text-[11px] font-medium text-muted-foreground">Bot 小队</div>
                {botTeams.map((executor) => (
                  <SelectItem key={todoExecutorFilterValue(executor)} value={todoExecutorFilterValue(executor)}>
                    {executor.name}
                  </SelectItem>
                ))}
              </SelectGroup>
            ) : null}
          </SelectContent>
        </Select>

        <div className="relative w-48">
          <Search
            className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground"
            aria-hidden
          />
          <Input
            value={keyword}
            onChange={(event) => onKeywordChange(event.target.value)}
            placeholder="搜索待办标题"
            aria-label="搜索待办"
            className="h-8 rounded-lg pl-8 text-xs"
          />
        </div>

        <div className="ml-auto flex flex-wrap items-center gap-3">
          <Button
            variant="ghost"
            className="h-8 px-1 text-xs font-semibold"
            leftIcon={<Workflow className="size-3.5" aria-hidden />}
            onClick={onAutomation}
          >
            自动化
          </Button>
          <Button
            variant="ghost"
            className="h-8 px-1 text-xs font-semibold"
            leftIcon={<Bot className="size-3.5" aria-hidden />}
            onClick={onBotTeams}
          >
            Bot 小队
          </Button>
          <span className="h-4 w-px bg-border" aria-hidden />
          <Button
            variant="outline"
            className="h-8 rounded-lg px-3.5 text-xs font-semibold"
            leftIcon={<Download className="size-3.5" aria-hidden />}
            onClick={onImportDima}
          >
            从 Dima 导入
          </Button>
          <Button
            className="h-8 rounded-lg px-4 text-xs font-semibold"
            leftIcon={<Plus className="size-3.5" aria-hidden />}
            onClick={onCreate}
          >
            发起待办
          </Button>
        </div>
      </div>
    </section>
  );
}
