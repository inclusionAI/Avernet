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
  labelClassName?: string;
  attentionDotClassName?: string;
}

const TODO_GROUPS: TodoGroup[] = [
  {
    key: 'pending',
    label: '待处理',
    statuses: ['pending', 'pending_acceptance'],
    labelClassName: 'font-semibold text-destructive',
    attentionDotClassName: 'bg-destructive',
  },
  {
    key: 'in_progress',
    label: '进行中',
    statuses: ['in_progress'],
  },
  {
    key: 'completed',
    label: '已完成',
    statuses: ['completed'],
  },
];

const TODO_DOT_CLASS: Record<TodoStatus, string> = {
  pending: 'bg-warning',
  in_progress: 'bg-info',
  pending_acceptance: 'bg-warning',
  completed: 'bg-success',
};

function formatDate(value: string): string {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' }).replaceAll('/', '-');
}

function TodoCard({ todo, onSelect }: { todo: TodoRecord; onSelect: (todoId: string) => void }) {
  return (
    <Card
      role="article"
      aria-label={todo.title}
      className="overflow-hidden rounded-xl border-border bg-card shadow-sm transition-[border-color,box-shadow] hover:border-foreground/20 hover:shadow-md"
    >
      <Button
        variant="ghost"
        className="h-auto w-full flex-col items-stretch gap-0 rounded-xl border-0 p-4 text-left font-normal normal-case hover:bg-muted/20"
        onClick={() => onSelect(todo.todoId)}
      >
        <span className="flex w-full items-start gap-2">
          <span className={cn('mt-1 size-2 shrink-0 rounded-full', TODO_DOT_CLASS[todo.status])} aria-hidden />
          <span className="min-w-0 truncate text-sm font-medium leading-5 text-foreground">{todo.title}</span>
        </span>

        <span className="mt-2 line-clamp-2 w-full whitespace-normal text-xs leading-[18px] text-muted-foreground">
          {todo.description || '暂无描述'}
        </span>

        <span className="mt-2.5 flex w-full flex-wrap items-center justify-between gap-x-3 gap-y-2 border-t border-divider pt-2.5 text-xs text-muted-foreground">
          <span className="flex min-w-0 items-center gap-2">
            <Bot className="size-3 shrink-0 text-foreground" aria-hidden />
            <span className="truncate">{todo.botName || todo.botId || '—'}</span>
          </span>
          <span className="flex shrink-0 items-center gap-2.5">
            <span className="flex items-center gap-2">
              <CalendarDays className="size-3" aria-hidden />
              {formatDate(todo.updatedAt)}
            </span>
            <Badge className={cn('shrink-0 border-0 px-2 py-0.5 text-xs', TODO_STATUS_CLASS[todo.status])}>
              {TODO_STATUS_LABEL[todo.status]}
            </Badge>
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
    completed: false,
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
    <section role="region" aria-label="待办分组" className="space-y-3">
      {TODO_GROUPS.map((group) => {
        const items = todos.filter((todo) => group.statuses.includes(todo.status));
        const isExpanded = expanded[group.key];
        return (
          <section key={group.key} aria-labelledby={`todo-group-${group.key}`} className="space-y-4">
            <Button
              variant="ghost"
              className="h-auto w-fit justify-start gap-2 rounded-md border-0 px-0 py-1 text-sm hover:bg-transparent"
              aria-expanded={isExpanded}
              onClick={() => setExpanded((current) => ({ ...current, [group.key]: !current[group.key] }))}
            >
              {isExpanded ? (
                <ChevronDown className="size-3.5 text-muted-foreground" aria-hidden />
              ) : (
                <ChevronRight className="size-3.5 text-muted-foreground" aria-hidden />
              )}
              <span id={`todo-group-${group.key}`} className={cn('text-sm font-medium', group.labelClassName)}>
                {group.label}
              </span>
              <span className="text-xs tabular-nums text-muted-foreground">({items.length})</span>
              {group.attentionDotClassName && items.length > 0 ? (
                <span className={cn('ml-1 size-2.5 rounded-full', group.attentionDotClassName)} aria-hidden />
              ) : null}
            </Button>

            {isExpanded ? (
              items.length > 0 ? (
                <div className="grid gap-[10px] [grid-template-columns:repeat(auto-fill,minmax(min(100%,420px),1fr))]">
                  {items.map((todo) => (
                    <TodoCard key={todo.todoId} todo={todo} onSelect={onSelect} />
                  ))}
                </div>
              ) : (
                <p className="m-0 rounded-xl border border-dashed border-border px-4 py-5 text-center text-xs text-muted-foreground">
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
