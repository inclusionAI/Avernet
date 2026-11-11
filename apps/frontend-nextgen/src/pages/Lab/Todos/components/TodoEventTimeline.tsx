import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { Empty } from '@/components/ui/Empty';
import { Spin } from '@/components/ui/Spin';
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
  if (loading && events.length === 0) return <Spin tip="加载事件时序中…" className="py-8" />;
  if (error && events.length === 0) {
    return (
      <Empty
        compact
        title="事件时序加载失败"
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
  if (events.length === 0)
    return <Empty compact title="暂无事件记录" description="Bot 开始执行后，过程和结果会显示在这里。" />;
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
      {events.map((event) => (
        <Card key={event.eventId || `${event.executionId}-${event.eventSeq}`} className="rounded-lg p-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex items-center gap-2 text-xs font-medium text-foreground">
              <span className="rounded-md bg-primary/10 px-2 py-1 text-primary">#{event.eventSeq}</span>
              <span>{event.eventType}</span>
            </div>
            <span className="flex items-center gap-1 text-xs text-muted-foreground">
              <Clock3 className="size-3.5" aria-hidden />
              {formatDateTime(event.createdAt)}
            </span>
          </div>
          {event.content ? (
            <p className="mt-3 whitespace-pre-wrap text-xs leading-5 text-muted-foreground">{event.content}</p>
          ) : null}
          {event.artifacts.length > 0 ? (
            <div className="mt-3 space-y-2 border-t border-divider pt-3">
              <div className="text-xs font-medium text-foreground">产物</div>
              {event.artifacts.map((artifact) => (
                <a
                  key={`${artifact.artifactId ?? artifact.url}-${artifact.name}`}
                  href={artifact.url}
                  target="_blank"
                  rel="noreferrer"
                  className="flex items-center gap-2 text-xs text-primary hover:underline"
                >
                  <FileOutput className="size-3.5" aria-hidden />
                  <span>{artifact.name}</span>
                </a>
              ))}
            </div>
          ) : null}
        </Card>
      ))}
    </div>
  );
}
