import { Badge } from '@/components/ui';
import type { TodoRecord } from '@/domain/todos/models';
import { TODO_STATUS_CLASS, TODO_STATUS_LABEL } from '@/domain/todos/status';
import { cn } from '@/utils/cn';
import { TodoDetailItem } from './TodoDetailPrimitives';

interface TodoDetailSidebarProps {
  todo: TodoRecord;
  currentSpaceName?: string;
}

function formatDateTime(value?: string): string {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { hour12: false });
}

export function TodoDetailSidebar({ todo, currentSpaceName }: TodoDetailSidebarProps) {
  const executorName = todo.templateId
    ? todo.templateName || todo.templateId
    : todo.botName || todo.botId;

  return (
    <aside className="w-full shrink-0 lg:w-64" aria-label="待办详情">
      <div className="sticky top-6 rounded-xl border border-border bg-card p-4">
        <h2 className="m-0 mb-3 text-sm font-semibold text-foreground">详情</h2>
        <div className="space-y-2.5">
          <TodoDetailItem
            label="状态"
            value={
              <Badge className={cn('border-0 px-1.5 py-0.5 text-[10px]', TODO_STATUS_CLASS[todo.status])}>
                {TODO_STATUS_LABEL[todo.status]}
              </Badge>
            }
          />
          <TodoDetailItem label="执行方" value={executorName} />
          <TodoDetailItem label="负责人" value={todo.ownerName || todo.ownerId || '—'} />
          <TodoDetailItem label="当前空间" value={currentSpaceName || `空间 ${todo.spaceId}`} />
          <TodoDetailItem label="创建时间" value={formatDateTime(todo.createdAt)} />
          <TodoDetailItem label="更新时间" value={formatDateTime(todo.updatedAt)} />
        </div>
      </div>
    </aside>
  );
}
