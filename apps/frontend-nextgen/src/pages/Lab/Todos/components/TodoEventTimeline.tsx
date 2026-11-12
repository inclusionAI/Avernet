import { Button, Empty, Spin } from '@/components/ui';
import type { TodoArtifact, TodoEvent } from '@/domain/todos/models';
import { cn } from '@/utils/cn';
import { ArrowRight, Bot, FileText, Link2, UserRound } from 'lucide-react';

interface TodoEventTimelineProps {
  events: TodoEvent[];
  botName?: string;
  loading?: boolean;
  error?: string | null;
  onRetry?: () => Promise<void> | void;
}

function formatDateTime(value: string): string {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  const hour = String(date.getHours()).padStart(2, '0');
  const minute = String(date.getMinutes()).padStart(2, '0');
  return `${month}-${day} ${hour}:${minute}`;
}

function actorLabel(event: TodoEvent, botName?: string): string {
  if (event.actorType === 'bot') return botName || event.actorId || 'Bot';
  if (event.actorType === 'human') return event.actorId || 'Human';
  return event.actorId || event.actorType || '系统';
}

function isExternalLink(artifact: TodoArtifact): boolean {
  const fileLike = /\.[a-z0-9]{2,6}(?:$|[?#])/i.test(artifact.name) || /\.[a-z0-9]{2,6}(?:$|[?#])/i.test(artifact.url);
  return !fileLike;
}

function EventArtifact({ artifact }: { artifact: TodoArtifact }) {
  const externalLink = isExternalLink(artifact);
  const Icon = externalLink ? Link2 : FileText;
  return (
    <a
      href={artifact.url}
      target="_blank"
      rel="noreferrer"
      className="group mt-4 flex min-h-[88px] items-center gap-4 rounded-2xl border border-border bg-background px-5 py-4 text-foreground transition-colors hover:border-foreground/20 hover:bg-muted/20"
    >
      <span className="flex size-[52px] shrink-0 items-center justify-center rounded-2xl bg-muted text-foreground">
        <Icon className="size-6" aria-hidden />
      </span>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-base font-semibold group-hover:text-primary">{artifact.name}</span>
        <span className="mt-1 block truncate text-sm text-muted-foreground">
          {externalLink ? '外部链接' : '文件产物'}
        </span>
      </span>
      <ArrowRight
        className="size-6 shrink-0 text-muted-foreground transition-transform group-hover:translate-x-0.5"
        aria-hidden
      />
    </a>
  );
}

export function TodoEventTimeline({ events, botName, loading = false, error, onRetry }: TodoEventTimelineProps) {
  if (loading && events.length === 0) return <Spin tip="加载事件中…" className="py-8" />;
  if (error && events.length === 0) {
    return (
      <Empty
        compact
        title="事件加载失败"
        description={error}
        action={
          onRetry ? (
            <Button variant="secondary" onClick={() => void onRetry()}>
              重试
            </Button>
          ) : undefined
        }
      />
    );
  }
  if (events.length === 0) {
    return <Empty compact title="暂无事件记录" description="Bot 开始执行后，过程和结果会显示在这里。" />;
  }

  return (
    <div>
      {error ? (
        <div className="mb-4 flex items-center justify-between gap-3 rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-destructive">
          <span>{error}</span>
          {onRetry ? (
            <Button variant="ghost" size="sm" onClick={() => void onRetry()}>
              重试
            </Button>
          ) : null}
        </div>
      ) : null}

      <ol className="m-0 list-none p-0">
        {events.map((event, index) => {
          const isLast = index === events.length - 1;
          const awaitingAcceptance = event.status === 'pending_acceptance';
          const actorIsBot = event.actorType === 'bot';
          return (
            <li
              key={event.eventId || `${event.executionId}-${event.eventSeq}`}
              className="grid grid-cols-[24px_minmax(0,1fr)] gap-x-3 pb-10 sm:grid-cols-[128px_28px_minmax(0,1fr)] sm:gap-x-0"
            >
              <time className="hidden pt-0.5 pr-6 text-right text-base tabular-nums text-muted-foreground sm:block">
                {formatDateTime(event.createdAt)}
              </time>

              <span className="relative flex justify-center" aria-hidden>
                {!isLast || awaitingAcceptance ? (
                  <span
                    className={cn(
                      'absolute top-3.5 bottom-[-2.5rem] w-px',
                      awaitingAcceptance ? 'bg-warning/35' : 'bg-success/35',
                    )}
                  />
                ) : null}
                <span
                  className={cn(
                    'relative z-10 mt-1 size-4 rounded-full border-[3px] bg-background',
                    awaitingAcceptance ? 'border-warning' : 'border-success',
                  )}
                />
              </span>

              <div className="min-w-0">
                <time className="mb-1.5 block text-sm tabular-nums text-muted-foreground sm:hidden">
                  {formatDateTime(event.createdAt)}
                </time>
                <div className="flex flex-wrap items-center gap-2.5">
                  <span className="text-lg font-medium leading-7 text-foreground">
                    {event.content || event.eventType}
                  </span>
                  <span className="inline-flex items-center gap-1.5 rounded-md bg-muted/70 px-2.5 py-1.5 text-sm text-muted-foreground">
                    {actorIsBot ? (
                      <Bot className="size-4" aria-hidden />
                    ) : (
                      <UserRound className="size-4" aria-hidden />
                    )}
                    {actorLabel(event, botName)}
                  </span>
                  {awaitingAcceptance ? (
                    <span className="rounded-md bg-warning/10 px-2.5 py-1.5 text-sm font-medium text-warning">等待中</span>
                  ) : null}
                </div>

                {event.artifacts.length > 0 ? (
                  <div className="mt-1">
                    {event.artifacts.map((artifact) => (
                      <EventArtifact
                        key={`${artifact.artifactId ?? artifact.url}-${artifact.name}`}
                        artifact={artifact}
                      />
                    ))}
                  </div>
                ) : null}
              </div>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
