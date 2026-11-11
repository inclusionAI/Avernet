import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { Empty } from '@/components/ui/Empty';
import { Pagination } from '@/components/ui/Pagination';
import { Spin } from '@/components/ui/Spin';
import type { TodoRecord } from '@/domain/todos/models';
import { TODO_STATUS_CLASS, TODO_STATUS_LABEL } from '@/domain/todos/status';
import { cn } from '@/utils/cn';
import { Eye } from 'lucide-react';

interface TodoTableProps {
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

function formatDateTime(value: string): string {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { hour12: false });
}

export function TodoTable({
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
}: TodoTableProps) {
  return (
    <div className="space-y-3">
      <Card className="overflow-hidden rounded-lg shadow-sm">
        <div className="overflow-x-auto">
          <table className="min-w-full border-separate border-spacing-0">
            <thead>
              <tr className="bg-muted/30 text-left text-xs font-medium text-muted-foreground">
                <th className="h-10 px-4 py-3">待办</th>
                <th className="h-10 px-4 py-3">Bot</th>
                <th className="h-10 px-4 py-3">状态</th>
                <th className="h-10 px-4 py-3">更新时间</th>
                <th className="h-10 px-4 py-3 text-right">操作</th>
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr>
                  <td colSpan={5} className="p-0"><Spin tip="加载待办中…" className="py-16" /></td>
                </tr>
              ) : error ? (
                <tr>
                  <td colSpan={5} className="p-0">
                    <Empty title="待办加载失败" description={error} action={<Button variant="secondary" onClick={onRetry}>重试</Button>} />
                  </td>
                </tr>
              ) : todos.length === 0 ? (
                <tr>
                  <td colSpan={5} className="p-0"><Empty title="暂无待办" description="创建一个待办，让当前空间的 Bot 开始处理。" /></td>
                </tr>
              ) : (
                todos.map((todo) => (
                  <tr key={todo.todoId} className="border-b border-divider text-xs transition-colors hover:bg-muted/50">
                    <td className="max-w-[28rem] border-t border-divider px-4 py-3 align-top">
                      <button type="button" className="block max-w-full text-left" onClick={() => onSelect(todo.todoId)}>
                        <div className="truncate font-medium text-foreground hover:text-primary">{todo.title}</div>
                        <div className="mt-1 line-clamp-2 text-muted-foreground">{todo.description || '暂无描述'}</div>
                      </button>
                    </td>
                    <td className="border-t border-divider px-4 py-3 align-top text-muted-foreground">{todo.botName || todo.botId || '—'}</td>
                    <td className="border-t border-divider px-4 py-3 align-top">
                      <Badge className={cn('border', TODO_STATUS_CLASS[todo.status])}>{TODO_STATUS_LABEL[todo.status]}</Badge>
                    </td>
                    <td className="border-t border-divider px-4 py-3 align-top text-muted-foreground">{formatDateTime(todo.updatedAt)}</td>
                    <td className="border-t border-divider px-4 py-3 text-right align-top">
                      <Button variant="ghost" size="sm" onClick={() => onSelect(todo.todoId)} leftIcon={<Eye className="size-4" aria-hidden />}>查看</Button>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </Card>
      <Pagination
        current={page}
        pageSize={pageSize}
        total={total}
        onChange={onPageChange}
        onPageSizeChange={onPageSizeChange}
        layout="between"
      />
    </div>
  );
}
