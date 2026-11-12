import { Button, Empty, Spin } from '@/components/ui';
import type { TodoEvent } from '@/domain/todos/models';
import { Clock3, FileOutput } from 'lucide-react';

interface TodoEventTimelineProps {
  events: TodoEvent[];
  loading?: boolean;
  error?: string | null;
  onRetry?: () => Promise<void> | void;
}

function formatDateTime(value: string): string {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { hour12: false });
}

export function TodoEventTimeline({ events, loading = false, error, onRetry }: TodoEventTimelineProps) {
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
    <div className="space-y-3">
      {error ? (
        <div className="flex items-center justify-between gap-3 rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-destructive">
          <span>{error}</span>
          {onRetry ? (
            <Button variant="ghost" size="sm" onClick={() => void onRetry()}>
              重试
            </Button>
          ) : null}
        </div>
      ) : null}

      <ol className="m-0 list-none space-y-0 p-0">
        {events.map((event, index) => {
          const isLast = index === events.length - 1;
          return (
            <li key={event.eventId || `${event.executionId}-${event.eventSeq}`} className="relative flex gap-4 pb-6">
              {!isLast ? (
                <span className="absolute left-[7px] top-5 h-[calc(100%-0.5rem)] w-px bg-border" aria-hidden />
              ) : null}
              <span className="relative z-10 mt-1.5 size-4 shrink-0 rounded-full border-4 border-background bg-primary shadow-sm" aria-hidden />
              <div className="min-w-0 flex-1 rounded-xl border border-border/60 bg-card px-4 py-3 shadow-sm">
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div className="flex min-w-0 items-center gap-2">
                    <span className="rounded-md bg-primary/10 px-2 py-0.5 text-[11px] font-medium text-primary">
                      #{event.eventSeq}
                    </span>
                    <span className="truncate text-xs font-medium text-foreground">{event.eventType}</span>
                  </div>
                  <span className="flex shrink-0 items-center gap-1 text-[11px] text-muted-foreground">
                    <Clock3 className="size-3.5" aria-hidden />
                    {formatDateTime(event.createdAt)}
                  </span>
                </div>

                {event.content ? (
                  <p className="m-0 mt-3 whitespace-pre-wrap text-sm leading-6 text-foreground">{event.content}</p>
                ) : null}

                {event.artifacts.length > 0 ? (
                  <div className="mt-3 flex flex-wrap gap-2 border-t border-divider pt-3">
                    {event.artifacts.map((artifact) => (
                      <a
                        key={`${artifact.artifactId ?? artifact.url}-${artifact.name}`}
                        href={artifact.url}
                        target="_blank"
                        rel="noreferrer"
                        className="inline-flex items-center gap-1.5 rounded-lg border border-border bg-background px-2.5 py-1.5 text-xs text-primary transition-colors hover:bg-accent"
                      >
                        <FileOutput className="size-3.5" aria-hidden />
                        <span>{artifact.name}</span>
                      </a>
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
