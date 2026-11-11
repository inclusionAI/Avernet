import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import {
  Drawer,
  DrawerContent,
  DrawerDescription,
  DrawerFooter,
  DrawerHeader,
  DrawerTitle,
} from '@/components/ui/Drawer';
import { Empty } from '@/components/ui/Empty';
import { Spin } from '@/components/ui/Spin';
import type { Space } from '@/domain/admin/models';
import { TODO_STATUS_CLASS, TODO_STATUS_LABEL, canInteractWithTodo } from '@/domain/todos/status';
import { useTodoDetail } from '@/hooks/useTodoDetail';
import { cn } from '@/utils/cn';
import { Bot, CalendarClock, CheckCircle2, CirclePlay, FileText, History, Pencil, RotateCcw } from 'lucide-react';
import { TodoAcceptanceActions } from './TodoAcceptanceActions';
import { TodoEventTimeline } from './TodoEventTimeline';
import { TodoInteractionPanel } from './TodoInteractionPanel';

interface TodoDetailDrawerProps {
  todoId: string | null;
  currentSpace?: Space;
  onClose: () => void;
  onEdit: () => void;
  onListRefresh: () => Promise<void> | void;
}

function formatDateTime(value?: string): string {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { hour12: false });
}

function InfoItem({ label, value, icon }: { label: string; value: React.ReactNode; icon: React.ReactNode }) {
  return (
    <div className="space-y-1.5">
      <div className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
        <span className="flex size-5 items-center justify-center rounded-md border border-border bg-background text-primary">
          {icon}
        </span>
        <span>{label}</span>
      </div>
      <div className="break-words text-xs text-foreground">{value || '—'}</div>
    </div>
  );
}

/** 待办详情：产品信息、Bot 执行事件、互动和验收集中在一个抽屉内。 */
export function TodoDetailDrawer({ todoId, currentSpace, onClose, onEdit, onListRefresh }: TodoDetailDrawerProps) {
  const detail = useTodoDetail(todoId, onListRefresh);
  const {
    selectedTodo,
    interactions,
    events,
    loading,
    eventsLoading,
    mutationLoading,
    error,
    eventsError,
    refresh,
    refreshEvents,
    run,
    interact,
    accept,
  } = detail;
  const open = Boolean(todoId);

  return (
    <Drawer open={open} onOpenChange={(nextOpen) => !nextOpen && onClose()}>
      <DrawerContent side="right" size="lg" className="bg-background text-foreground" closeLabel="关闭待办详情">
        <DrawerHeader className="mb-5 border-b border-divider pb-4">
          <DrawerTitle className="pr-8 text-lg font-semibold tracking-tight text-foreground">
            {selectedTodo?.title ?? '待办详情'}
          </DrawerTitle>
          <DrawerDescription className="mt-1 pr-8 text-xs text-muted-foreground">
            {selectedTodo ? `待办 ID：${selectedTodo.todoId}` : '正在加载待办详情'}
          </DrawerDescription>
        </DrawerHeader>

        {loading && !selectedTodo ? (
          <Spin tip="加载待办详情中…" className="py-16" />
        ) : error && !selectedTodo ? (
          <Empty
            title="待办详情加载失败"
            description={error}
            action={
              <Button variant="secondary" onClick={() => void refresh()}>
                重试
              </Button>
            }
          />
        ) : selectedTodo ? (
          <div className="space-y-6">
            <section className="space-y-4 rounded-lg border border-border bg-muted/20 p-4">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <h2 className="m-0 break-words text-base font-semibold text-foreground">{selectedTodo.title}</h2>
                  <p className="m-0 mt-2 whitespace-pre-wrap text-xs leading-5 text-muted-foreground">
                    {selectedTodo.description || '暂无描述'}
                  </p>
                </div>
                <Badge className={cn('border', TODO_STATUS_CLASS[selectedTodo.status])}>
                  {TODO_STATUS_LABEL[selectedTodo.status]}
                </Badge>
              </div>

              <div className="grid gap-4 border-t border-divider pt-4 sm:grid-cols-2">
                <InfoItem
                  icon={<Bot className="size-3.5" />}
                  label="Bot"
                  value={selectedTodo.botName || selectedTodo.botId}
                />
                <InfoItem
                  icon={<FileText className="size-3.5" />}
                  label="当前空间"
                  value={currentSpace?.spaceName || `空间 ${selectedTodo.spaceId}`}
                />
                <InfoItem
                  icon={<CalendarClock className="size-3.5" />}
                  label="创建时间"
                  value={formatDateTime(selectedTodo.createdAt)}
                />
                <InfoItem
                  icon={<History className="size-3.5" />}
                  label="更新时间"
                  value={formatDateTime(selectedTodo.updatedAt)}
                />
              </div>

              {selectedTodo.status === 'draft' ? (
                <div className="flex justify-end gap-2">
                  <Button variant="outline" onClick={onEdit} leftIcon={<Pencil className="size-4" aria-hidden />}>
                    编辑待办
                  </Button>
                  <Button
                    onClick={() => void run()}
                    loading={mutationLoading}
                    leftIcon={<CirclePlay className="size-4" aria-hidden />}
                  >
                    启动执行
                  </Button>
                </div>
              ) : null}
            </section>

            <section className="space-y-3">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <h3 className="m-0 text-sm font-semibold text-foreground">事件时序</h3>
                  <p className="m-0 mt-1 text-xs text-muted-foreground">Bot 的执行过程和结果由执行服务记录。</p>
                </div>
                {eventsLoading ? <span className="text-xs text-muted-foreground">刷新中…</span> : null}
              </div>
              <TodoEventTimeline events={events} loading={eventsLoading} error={eventsError} onRetry={refreshEvents} />
            </section>

            <TodoInteractionPanel
              interactions={interactions}
              enabled={canInteractWithTodo(selectedTodo.status)}
              loading={mutationLoading}
              onSubmit={interact}
            />

            {canInteractWithTodo(selectedTodo.status) ? (
              <TodoAcceptanceActions loading={mutationLoading} onAccept={accept} />
            ) : null}

            {error ? (
              <p className="m-0 rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-destructive">
                {error}
              </p>
            ) : null}
          </div>
        ) : null}

        <DrawerFooter className="justify-end border-t border-divider pt-4">
          {selectedTodo && selectedTodo.status === 'in_progress' ? (
            <span className="mr-auto flex items-center gap-1.5 text-xs text-muted-foreground">
              <RotateCcw className="size-3.5 animate-spin" aria-hidden /> Bot 正在执行
            </span>
          ) : null}
          {selectedTodo && selectedTodo.status === 'completed' ? (
            <span className="mr-auto flex items-center gap-1.5 text-xs text-success">
              <CheckCircle2 className="size-3.5" aria-hidden /> 已完成
            </span>
          ) : null}
          <Button variant="outline" onClick={onClose}>
            关闭
          </Button>
        </DrawerFooter>
      </DrawerContent>
    </Drawer>
  );
}
