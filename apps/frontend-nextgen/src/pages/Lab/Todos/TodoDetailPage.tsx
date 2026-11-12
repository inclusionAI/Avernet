import { PageHeader } from '@/components/Common/PageHeader';
import { Badge, Button, Card, Empty, Segmented, Spin } from '@/components/ui';
import type { TodoArtifact } from '@/domain/todos/models';
import { TODO_STATUS_CLASS, TODO_STATUS_LABEL, canInteractWithTodo } from '@/domain/todos/status';
import { useSpaceContext } from '@/hooks/useSpaceContext';
import { useTodoDetail } from '@/hooks/useTodoDetail';
import { cn } from '@/utils/cn';
import { history, useParams } from '@umijs/max';
import { Bot, CalendarClock, ChevronDown, ChevronUp, FileText, History } from 'lucide-react';
import { useMemo, useState } from 'react';
import { TodoAcceptanceActions } from './components/TodoAcceptanceActions';
import { TodoArtifactList } from './components/TodoArtifactList';
import { TodoEventTimeline } from './components/TodoEventTimeline';
import { TodoInteractionPanel } from './components/TodoInteractionPanel';

type DetailView = 'events' | 'interactions' | 'artifacts';

function formatDateTime(value?: string): string {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { hour12: false });
}

function uniqueArtifacts(events: ReturnType<typeof useTodoDetail>['events']): TodoArtifact[] {
  const seen = new Set<string>();
  return events.flatMap((event) =>
    event.artifacts.filter((artifact) => {
      const key = artifact.artifactId || `${artifact.url}\u0000${artifact.name}`;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    }),
  );
}

function DetailItem({ label, value, icon }: { label: string; value: React.ReactNode; icon: React.ReactNode }) {
  return (
    <div className="flex items-start gap-2.5">
      <span className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-lg bg-muted text-muted-foreground">
        {icon}
      </span>
      <div className="min-w-0">
        <div className="text-[11px] text-muted-foreground">{label}</div>
        <div className="mt-0.5 break-words text-xs font-medium text-foreground">{value || '—'}</div>
      </div>
    </div>
  );
}

/** 独立待办详情页：围绕事件、互动和产物呈现执行过程。 */
export default function TodoDetailPage() {
  const { todoId } = useParams<{ todoId: string }>();
  const currentSpace = useSpaceContext((state) => state.currentSpace);
  const [view, setView] = useState<DetailView>('events');
  const [detailsExpanded, setDetailsExpanded] = useState(false);
  const detail = useTodoDetail(todoId || null);
  const artifacts = useMemo(() => uniqueArtifacts(detail.events), [detail.events]);
  const todo = detail.selectedTodo;

  return (
    <main className="app-scrollbar h-full min-h-0 overflow-y-auto">
      <div className="mx-auto flex w-full max-w-5xl flex-col gap-5 px-4 py-4 sm:px-6 sm:py-6 2xl:px-8">
        <Button
          variant="ghost"
          aria-label="返回待办列表"
          className="h-auto w-fit border-0 px-0 py-1 text-xs text-muted-foreground hover:bg-transparent hover:text-primary"
          onClick={() => history.push('/lab/todos')}
        >
          ← 返回待办列表
        </Button>

        {detail.loading && !todo ? (
          <Spin tip="加载待办详情中…" className="py-16" />
        ) : detail.error && !todo ? (
          <Empty
            title="待办详情加载失败"
            description={detail.error}
            action={
              <Button variant="secondary" onClick={() => void detail.refresh()}>
                重试
              </Button>
            }
          />
        ) : todo ? (
          <>
            <PageHeader
              title={todo.title}
              description={todo.description || '暂无描述'}
              eyebrow="待办详情"
              actions={
                <Badge className={cn('border', TODO_STATUS_CLASS[todo.status])}>
                  {TODO_STATUS_LABEL[todo.status]}
                </Badge>
              }
            />

            <Card className="overflow-hidden border-border/60 shadow-sm">
              <Button
                variant="ghost"
                className="h-auto w-full justify-between rounded-none border-0 px-4 py-3 text-xs hover:bg-muted/30"
                aria-expanded={detailsExpanded}
                onClick={() => setDetailsExpanded((expanded) => !expanded)}
                rightIcon={
                  detailsExpanded ? (
                    <ChevronUp className="size-4" aria-hidden />
                  ) : (
                    <ChevronDown className="size-4" aria-hidden />
                  )
                }
              >
                详情
              </Button>
              {detailsExpanded ? (
                <div className="grid gap-4 border-t border-divider px-4 py-4 sm:grid-cols-2 lg:grid-cols-4">
                  <DetailItem icon={<Bot className="size-4" />} label="Bot" value={todo.botName || todo.botId} />
                  <DetailItem
                    icon={<FileText className="size-4" />}
                    label="当前空间"
                    value={currentSpace?.spaceName || `空间 ${todo.spaceId}`}
                  />
                  <DetailItem
                    icon={<CalendarClock className="size-4" />}
                    label="创建时间"
                    value={formatDateTime(todo.createdAt)}
                  />
                  <DetailItem
                    icon={<History className="size-4" />}
                    label="更新时间"
                    value={formatDateTime(todo.updatedAt)}
                  />
                </div>
              ) : null}
            </Card>

            <Segmented
              aria-label="待办详情视图"
              value={view}
              options={[
                { value: 'events', label: '事件' },
                { value: 'interactions', label: '互动' },
                { value: 'artifacts', label: `产物 ${artifacts.length}` },
              ]}
              onChange={setView}
              className="w-full max-w-md"
              activeOptionClassName="bg-primary text-primary-foreground hover:bg-primary"
              inactiveOptionClassName="text-foreground/70"
            />

            <Card className="border-border/60 p-4 shadow-sm sm:p-5">
              {view === 'events' ? (
                <div className="space-y-6">
                  <TodoEventTimeline
                    events={detail.events}
                    loading={detail.eventsLoading}
                    error={detail.eventsError}
                    onRetry={detail.refreshEvents}
                  />
                  {todo.status === 'pending_acceptance' ? (
                    <TodoAcceptanceActions loading={detail.mutationLoading} onAccept={detail.accept} />
                  ) : null}
                </div>
              ) : null}

              {view === 'interactions' ? (
                <TodoInteractionPanel
                  interactions={detail.interactions}
                  enabled={canInteractWithTodo(todo.status)}
                  loading={detail.mutationLoading}
                  onSubmit={detail.interact}
                />
              ) : null}

              {view === 'artifacts' ? <TodoArtifactList artifacts={artifacts} /> : null}
            </Card>

            {detail.error ? (
              <p className="m-0 rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-destructive">
                {detail.error}
              </p>
            ) : null}
          </>
        ) : null}
      </div>
    </main>
  );
}
