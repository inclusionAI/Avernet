import { Button, Empty, Spin } from '@/components/ui';
import type { TodoEvent, TodoEventArtifact } from '@/domain/todos/models';
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

function isExternalLink(artifact: TodoEventArtifact): boolean {
  const fileLike =
    /\.[a-z0-9]{2,6}(?:$|[?#])/i.test(artifact.name) || /\.[a-z0-9]{2,6}(?:$|[?#])/i.test(artifact.url);
  return !fileLike;
}

function EventArtifact({ artifact }: { artifact: TodoEventArtifact }) {
  const externalLink = isExternalLink(artifact);
  const Icon = externalLink ? Link2 : FileText;
  return (
    <a
      href={artifact.url}
      target="_blank"
      rel="noreferrer"
      className="group mt-3 flex min-h-14 items-center gap-3 rounded-xl border border-border bg-background px-4 py-3 text-foreground transition-colors hover:border-foreground/20 hover:bg-muted/20"
    >
      <span className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-muted text-foreground">
        <Icon className="size-4" aria-hidden />
      </span>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-sm font-medium group-hover:text-primary">{artifact.name}</span>
        <span className="mt-0.5 block truncate text-xs text-muted-foreground">
          {externalLink ? '外部链接' : '文件产物'}
        </span>
      </span>
      <ArrowRight className="size-4 shrink-0 text-muted-foreground transition-transform group-hover:translate-x-0.5" aria-hidden />
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
              className={cn(
                'grid grid-cols-[24px_minmax(0,1fr)] gap-x-3 sm:grid-cols-[128px_28px_minmax(0,1fr)] sm:gap-x-0',
                isLast && awaitingAcceptance ? 'pb-10' : isLast ? 'pb-0' : 'pb-4',
              )}
            >
              <time className="hidden pt-1 pr-6 text-right text-xs tabular-nums text-muted-foreground sm:block">
                {formatDateTime(event.createdAt)}
              </time>

              <span className="relative flex justify-center" aria-hidden>
                {!isLast || awaitingAcceptance ? (
                  <span
                    className={cn(
                      'absolute top-3 w-px',
                      awaitingAcceptance && isLast ? 'bottom-[-2.5rem]' : 'bottom-[-1rem]',
                      awaitingAcceptance ? 'bg-warning/35' : 'bg-success/35',
                    )}
                  />
                ) : null}
                <span
                  className={cn(
                    'relative z-10 mt-1 size-3 rounded-full border-2 bg-background',
                    awaitingAcceptance ? 'border-warning' : 'border-success',
                  )}
                />
              </span>

              <div className="min-w-0">
                <time className="mb-1 block text-xs tabular-nums text-muted-foreground sm:hidden">
                  {formatDateTime(event.createdAt)}
                </time>
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-sm font-medium leading-5 text-foreground">
                    {event.content || event.eventType}
                  </span>
                  <span className="inline-flex items-center gap-1 rounded-md bg-muted/70 px-2 py-1 text-xs text-muted-foreground">
                    {actorIsBot ? <Bot className="size-3" aria-hidden /> : <UserRound className="size-3" aria-hidden />}
                    {actorLabel(event, botName)}
                  </span>
                  {awaitingAcceptance ? (
                    <span className="rounded-md bg-warning/10 px-2 py-1 text-xs font-medium text-warning">等待中</span>
                  ) : null}
                </div>

                {event.artifacts.length > 0 ? (
                  <div>
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
