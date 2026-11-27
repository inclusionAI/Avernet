import { Badge, Button, Empty, Spin } from '@/components/ui';
import { DataTable, type DataTableColumn } from '@/components/ui/DataTable';
import type { TodoRecord } from '@/domain/todos/models';
import { TODO_STATUS_CLASS, TODO_STATUS_LABEL } from '@/domain/todos/status';
import { cn } from '@/utils/cn';
import { Bot, ChevronLeft, ChevronRight, Workflow } from 'lucide-react';
import { useMemo } from 'react';

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
}

function formatDateTime(value?: string): string {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { hour12: false });
}

function executorName(todo: TodoRecord): string {
  if (todo.templateId) return todo.templateName || todo.templateId;
  return todo.botName || todo.botId || '—';
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
}: TodoBoardProps) {
  const columns = useMemo<DataTableColumn<TodoRecord>[]>(
    () => [
      {
        id: 'title',
        header: '标题',
        width: '28%',
        cell: (todo) => <p className="m-0 truncate text-sm font-medium text-foreground">{todo.title}</p>,
      },
      {
        id: 'status',
        header: '状态',
        width: '10%',
        cell: (todo) => (
          <div className="flex items-center gap-1.5">
            <span
              aria-hidden
              className={cn(
                'size-2 shrink-0 rounded-full',
                todo.status === 'in_progress' && 'bg-info',
                todo.status === 'pending_action' && 'bg-warning',
                todo.status === 'interrupted' && 'bg-destructive',
                todo.status === 'completed' && 'bg-success',
                (todo.status === 'not_started' || todo.status === 'cancelled') && 'bg-muted-foreground/50',
              )}
            />
            <Badge className={cn('border-0 px-2 py-0.5 text-xs', TODO_STATUS_CLASS[todo.status])}>
              {TODO_STATUS_LABEL[todo.status]}
            </Badge>
          </div>
        ),
      },
      {
        id: 'executor',
        header: '执行方',
        width: '16%',
        cell: (todo) => (
          <div className="flex min-w-0 items-center gap-1.5 text-xs text-muted-foreground">
            {todo.templateId ? <Workflow className="size-3.5 shrink-0" aria-hidden /> : <Bot className="size-3.5 shrink-0" aria-hidden />}
            <span className="truncate">{executorName(todo)}</span>
          </div>
        ),
      },
      {
        id: 'owner',
        header: '负责人',
        width: '13%',
        cell: (todo) => <span className="truncate text-xs text-foreground">{todo.ownerName || todo.ownerId || '—'}</span>,
      },
      {
        id: 'source',
        header: '创建方式',
        width: '10%',
        cell: () => <span className="text-xs text-muted-foreground">—</span>,
      },
      {
        id: 'createdAt',
        header: '创建时间',
        width: '13%',
        cell: (todo) => <span className="text-xs text-muted-foreground">{formatDateTime(todo.createdAt)}</span>,
      },
      {
        id: 'completedAt',
        header: '完成时间',
        width: '10%',
        cell: () => <span className="text-xs text-muted-foreground">—</span>,
      },
    ],
    [],
  );

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

  return (
    <section role="region" aria-label="待办列表" className="space-y-3 overflow-hidden rounded-xl border border-border bg-card">
      <DataTable
        ariaLabel="待办列表"
        columns={columns}
        rows={todos}
        getRowKey={(todo) => todo.todoId}
        onRowClick={(todo) => onSelect(todo.todoId)}
        minWidth="min-w-[1120px]"
        rowHeight="h-14"
        empty={<div className="py-10 text-sm text-muted-foreground">没有符合条件的待办</div>}
      />
      {total > pageSize ? (
        <div className="flex items-center justify-end gap-2 border-t border-divider px-4 py-3 text-xs text-muted-foreground">
          <Button
            variant="outline"
            size="icon"
            className="size-7 rounded-lg"
            aria-label="上一页"
            disabled={page <= 1}
            onClick={() => onPageChange(Math.max(1, page - 1))}
          >
            <ChevronLeft className="size-3.5" aria-hidden />
          </Button>
          <span>{page} / {Math.max(1, Math.ceil(total / pageSize))}</span>
          <Button
            variant="outline"
            size="icon"
            className="size-7 rounded-lg"
            aria-label="下一页"
            disabled={page >= Math.ceil(total / pageSize)}
            onClick={() => onPageChange(Math.min(Math.ceil(total / pageSize), page + 1))}
          >
            <ChevronRight className="size-3.5" aria-hidden />
          </Button>
        </div>
      ) : null}
    </section>
  );
}
