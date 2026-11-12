import { Badge, Button, Card, Empty, Spin } from '@/components/ui';
import type { TodoArtifact } from '@/domain/todos/models';
import { TODO_STATUS_CLASS, TODO_STATUS_LABEL, canInteractWithTodo } from '@/domain/todos/status';
import { useSpaceContext } from '@/hooks/useSpaceContext';
import { useTodoDetail } from '@/hooks/useTodoDetail';
import { cn } from '@/utils/cn';
import { history, useParams } from '@umijs/max';
import { ArrowLeft, Bot, CalendarClock, ChevronDown, ChevronRight, FileText, History } from 'lucide-react';
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
    <div className="flex items-start gap-3">
      <span className="mt-0.5 flex size-9 shrink-0 items-center justify-center rounded-xl bg-muted text-muted-foreground">
        {icon}
      </span>
      <div className="min-w-0">
        <div className="text-xs text-muted-foreground">{label}</div>
        <div className="mt-1 break-words text-sm font-medium text-foreground">{value || '—'}</div>
      </div>
    </div>
  );
}

interface DetailTabProps {
  active: boolean;
  children: React.ReactNode;
  onClick: () => void;
}

function DetailTab({ active, children, onClick }: DetailTabProps) {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      className={cn(
        '-mb-px border-x-0 border-t-0 border-b-2 bg-transparent px-1 pb-4 text-xl font-semibold outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2',
        active ? 'border-foreground text-foreground' : 'border-transparent text-muted-foreground hover:text-foreground',
      )}
      onClick={onClick}
    >
      {children}
    </button>
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
    <main className="app-scrollbar h-full min-h-0 overflow-y-auto bg-background">
      {detail.loading && !todo ? (
        <Spin tip="加载待办详情中…" className="py-24" />
      ) : detail.error && !todo ? (
        <div className="mx-auto w-full max-w-6xl px-5 py-16">
          <Empty
            title="待办详情加载失败"
            description={detail.error}
            action={
              <Button variant="secondary" onClick={() => void detail.refresh()}>
                重试
              </Button>
            }
          />
        </div>
      ) : todo ? (
        <>
          <header className="border-b border-divider bg-background px-4 py-4 sm:px-6">
            <div className="mx-auto flex w-full max-w-[1600px] items-center gap-5">
              <Button
                variant="ghost"
                size="icon"
                aria-label="返回待办列表"
                className="size-[52px] rounded-2xl bg-muted/70 hover:bg-muted"
                onClick={() => history.push('/lab/todos')}
              >
                <ArrowLeft className="size-6" aria-hidden />
              </Button>
              <h1 className="m-0 min-w-0 truncate text-2xl font-semibold tracking-tight text-foreground sm:text-3xl">
                {todo.title}
              </h1>
              <Badge className={cn('shrink-0 border-0 px-3 py-1.5 text-base', TODO_STATUS_CLASS[todo.status])}>
                {TODO_STATUS_LABEL[todo.status]}
              </Badge>
            </div>
          </header>

          <div className="mx-auto flex w-full max-w-[1400px] flex-col gap-7 px-4 py-8 sm:px-6 sm:py-11">
            <section aria-label="待办概要" className="space-y-7">
              <p className="m-0 whitespace-pre-wrap text-lg leading-8 text-foreground">
                {todo.description || '暂无描述'}
              </p>

              <div>
                <Button
                  variant="ghost"
                  className="h-auto gap-2.5 rounded-md border-0 px-0 py-1 text-base text-muted-foreground hover:bg-transparent hover:text-foreground"
                  aria-expanded={detailsExpanded}
                  onClick={() => setDetailsExpanded((expanded) => !expanded)}
                  leftIcon={
                    detailsExpanded ? (
                      <ChevronDown className="size-5" aria-hidden />
                    ) : (
                      <ChevronRight className="size-5" aria-hidden />
                    )
                  }
                >
                  详情
                </Button>
                {detailsExpanded ? (
                  <div className="mt-4 grid gap-6 rounded-2xl border border-border bg-muted/15 p-6 sm:grid-cols-2 lg:grid-cols-4">
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
              </div>
            </section>

            <Card className="overflow-hidden rounded-2xl border-border shadow-none">
              <div
                role="group"
                aria-label="待办详情视图"
                className="flex items-center gap-12 border-b border-divider px-8 pt-6"
              >
                <DetailTab active={view === 'events'} onClick={() => setView('events')}>
                  事件
                </DetailTab>
                <DetailTab active={view === 'interactions'} onClick={() => setView('interactions')}>
                  互动
                </DetailTab>
                <DetailTab active={view === 'artifacts'} onClick={() => setView('artifacts')}>
                  产物 {artifacts.length}
                </DetailTab>
              </div>

              <div className="px-5 py-7 sm:px-8 sm:py-8">
                {view === 'events' ? (
                  <div className="space-y-1">
                    <TodoEventTimeline
                      events={detail.events}
                      botName={todo.botName}
                      loading={detail.eventsLoading}
                      error={detail.eventsError}
                      onRetry={detail.refreshEvents}
                    />
                    {todo.status === 'pending_acceptance' ? (
                      <div className="relative pl-[37px] sm:pl-[156px]">
                        <span
                          aria-hidden
                          className="absolute bottom-0 left-[11.5px] top-[-2.5rem] w-px bg-warning/35 sm:left-[141.5px]"
                        />
                        <div className="relative z-10">
                          <TodoAcceptanceActions loading={detail.mutationLoading} onAccept={detail.accept} />
                        </div>
                      </div>
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
              </div>
            </Card>

            {detail.error ? (
              <p className="m-0 rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-destructive">
                {detail.error}
              </p>
            ) : null}
          </div>
        </>
      ) : null}
    </main>
  );
}
