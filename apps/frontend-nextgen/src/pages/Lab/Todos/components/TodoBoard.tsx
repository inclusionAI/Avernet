import { Badge, Button, Card, Empty, Pagination, Spin } from '@/components/ui';
import type { TodoRecord, TodoStatus } from '@/domain/todos/models';
import { TODO_STATUS_CLASS, TODO_STATUS_LABEL } from '@/domain/todos/status';
import { cn } from '@/utils/cn';
import { Bot, CalendarDays, ChevronDown, ChevronRight } from 'lucide-react';
import { useState } from 'react';

interface TodoBoardProps {
  todos: TodoRecord[];
  total: number;
  page: number;
  pageSize: number;
  loading: boolean;
  error: string | null;
  onRetry: () => void;
  onSelect: (todoId: string) => void;
  onPageChange: (page: number) => void;
  onPageSizeChange: (pageSize: number) => void;
}

type TodoGroupKey = 'pending' | 'in_progress' | 'completed';

interface TodoGroup {
  key: TodoGroupKey;
  label: string;
  statuses: TodoStatus[];
  dotClassName: string;
}

const TODO_GROUPS: TodoGroup[] = [
  {
    key: 'pending',
    label: '待处理',
    statuses: ['pending', 'pending_acceptance'],
    dotClassName: 'bg-warning',
  },
  {
    key: 'in_progress',
    label: '进行中',
    statuses: ['in_progress'],
    dotClassName: 'bg-primary',
  },
  {
    key: 'completed',
    label: '已完成',
    statuses: ['completed'],
    dotClassName: 'bg-success',
  },
];

function formatDate(value: string): string {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleDateString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit' });
}

function TodoCard({ todo, onSelect }: { todo: TodoRecord; onSelect: (todoId: string) => void }) {
  return (
    <Card
      role="article"
      aria-label={todo.title}
      className="overflow-hidden border-border/60 shadow-sm transition-all hover:-translate-y-0.5 hover:border-primary/30 hover:shadow-md"
    >
      <Button
        variant="ghost"
        className="h-auto w-full flex-col items-stretch gap-3 rounded-xl border-0 p-4 text-left font-normal hover:bg-muted/30"
        onClick={() => onSelect(todo.todoId)}
      >
        <span className="flex w-full items-start justify-between gap-3">
          <span className="min-w-0 text-sm font-semibold leading-5 text-foreground">{todo.title}</span>
          <Badge className={cn('shrink-0 border', TODO_STATUS_CLASS[todo.status])}>
            {TODO_STATUS_LABEL[todo.status]}
          </Badge>
        </span>
        <span className="line-clamp-2 min-h-10 w-full whitespace-normal text-xs leading-5 text-muted-foreground">
          {todo.description || '暂无描述'}
        </span>
        <span className="flex w-full flex-wrap items-center justify-between gap-x-4 gap-y-2 border-t border-divider pt-3 text-xs text-muted-foreground">
          <span className="flex min-w-0 items-center gap-1.5">
            <Bot className="size-3.5 shrink-0" aria-hidden />
            <span className="truncate">{todo.botName || todo.botId || '—'}</span>
          </span>
          <span className="flex items-center gap-1.5">
            <CalendarDays className="size-3.5" aria-hidden />
            {formatDate(todo.updatedAt)}
          </span>
        </span>
      </Button>
    </Card>
  );
}

export function TodoBoard({
  todos,
  total,
  page,
  pageSize,
  loading,
  error,
  onRetry,
  onSelect,
  onPageChange,
  onPageSizeChange,
}: TodoBoardProps) {
  const [expanded, setExpanded] = useState<Record<TodoGroupKey, boolean>>({
    pending: true,
    in_progress: true,
    completed: true,
  });

  if (loading) return <Spin tip="加载待办中…" className="py-16" />;
  if (error) {
    return (
      <Empty
        title="待办加载失败"
        description={error}
        action={
          <Button variant="secondary" onClick={onRetry}>
            重试
          </Button>
        }
      />
    );
  }
  if (todos.length === 0) {
    return <Empty title="暂无待办" description="创建一个待办，让当前空间的 Bot 开始处理。" />;
  }

  return (
    <section role="region" aria-label="待办分组" className="space-y-5">
      {TODO_GROUPS.map((group) => {
        const items = todos.filter((todo) => group.statuses.includes(todo.status));
        const isExpanded = expanded[group.key];
        return (
          <section key={group.key} aria-labelledby={`todo-group-${group.key}`} className="space-y-3">
            <Button
              variant="ghost"
              className="h-auto w-full justify-start gap-2 rounded-lg border-0 px-1 py-1.5 hover:bg-muted/40"
              aria-expanded={isExpanded}
              onClick={() => setExpanded((current) => ({ ...current, [group.key]: !current[group.key] }))}
            >
              {isExpanded ? (
                <ChevronDown className="size-4 text-muted-foreground" aria-hidden />
              ) : (
                <ChevronRight className="size-4 text-muted-foreground" aria-hidden />
              )}
              <span className={cn('size-2 rounded-full', group.dotClassName)} aria-hidden />
              <span id={`todo-group-${group.key}`} className="text-sm font-semibold text-foreground">
                {group.label}
              </span>
              <span className="rounded-full bg-muted px-2 py-0.5 text-xs tabular-nums text-muted-foreground">
                {items.length}
              </span>
            </Button>

            {isExpanded ? (
              items.length > 0 ? (
                <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
                  {items.map((todo) => (
                    <TodoCard key={todo.todoId} todo={todo} onSelect={onSelect} />
                  ))}
                </div>
              ) : (
                <p className="m-0 rounded-lg border border-dashed border-border/60 px-4 py-5 text-center text-xs text-muted-foreground">
                  暂无{group.label}待办
                </p>
              )
            ) : null}
          </section>
        );
      })}

      <Pagination
        current={page}
        pageSize={pageSize}
        total={total}
        onChange={onPageChange}
        onPageSizeChange={onPageSizeChange}
        layout="between"
      />
    </section>
  );
}
