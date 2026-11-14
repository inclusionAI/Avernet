import { Button, Empty, Spin } from '@/components/ui';
import type { TodoEvent, TodoEventArtifact } from '@/domain/todos/models';
import { cn } from '@/utils/cn';
import { ArrowRight, FileText, Link2 } from 'lucide-react';

interface TodoEventTimelineProps {
  events: TodoEvent[];
  todoTitle: string;
  todoDescription?: string;
  loading?: boolean;
  error?: string | null;
  onRetry?: () => Promise<void> | void;
}

interface PresentedEvent {
  event: TodoEvent;
  title: string;
  detail?: string;
}

const EVENT_CONTENT_PREVIEW_LENGTH = 20;

const STATUS_ONLY_EVENT_TYPES = new Set([
  'task.assigned',
  'task.started',
  'task.accepted',
  'task.rejected',
  'task.cancelled',
  'execution.status',
]);

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

function contentPreview(value: string): string {
  const normalized = value.replace(/\s+/g, ' ').trim();
  const characters = Array.from(normalized);
  if (characters.length <= EVENT_CONTENT_PREVIEW_LENGTH) return normalized;
  return `${characters.slice(0, EVENT_CONTENT_PREVIEW_LENGTH).join('')}…`;
}

function presentEvent(event: TodoEvent, todoTitle: string, todoDescription?: string): PresentedEvent | null {
  const content = event.content.trim();
  const preview = contentPreview(content);
  if (event.eventType === 'task.created') {
    return {
      event,
      title: `发起待办：${todoTitle}`,
      detail: contentPreview(content || todoDescription || '') || undefined,
    };
  }
  if (event.eventType === 'user_interaction.posted') {
    return { event, title: preview ? `补充要求：${preview}` : '补充待办要求' };
  }
  if (event.eventType === 'task.progress.updated') {
    return { event, title: preview ? `更新进展：${preview}` : '更新执行进展' };
  }
  if (event.eventType === 'task.submitted') {
    return { event, title: '提交执行结果' };
  }
  if (event.eventType === 'task.completed') {
    return { event, title: content ? '输出：文本结果' : '输出执行结果' };
  }
  if (STATUS_ONLY_EVENT_TYPES.has(event.eventType) || !content) return null;
  return { event, title: preview };
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

export function TodoEventTimeline({
  events,
  todoTitle,
  todoDescription,
  loading = false,
  error,
  onRetry,
}: TodoEventTimelineProps) {
  const presentedEvents = events
    .map((event) => presentEvent(event, todoTitle, todoDescription))
    .filter((event): event is PresentedEvent => event !== null);

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
  if (presentedEvents.length === 0) {
    return <Empty compact title="暂无事件记录" description="待办的发起、要求和输出会显示在这里。" />;
  }

  return (
    <section role="region" aria-label="待办事件">
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
        {presentedEvents.map(({ event, title, detail }, index) => {
          const isLast = index === presentedEvents.length - 1;
          return (
            <li
              key={event.eventId || `${event.executionId}-${event.eventSeq}`}
              className={cn(
                'grid grid-cols-[24px_minmax(0,1fr)] gap-x-3 sm:grid-cols-[128px_28px_minmax(0,1fr)] sm:gap-x-0',
                isLast ? 'pb-0' : 'pb-4',
              )}
            >
              <time className="hidden pt-1 pr-6 text-right text-xs tabular-nums text-muted-foreground sm:block">
                {formatDateTime(event.createdAt)}
              </time>

              <span className="relative flex justify-center" aria-hidden>
                {!isLast ? <span className="absolute top-3 bottom-[-1rem] w-px bg-success/35" /> : null}
                <span className="relative z-10 mt-1 size-3 rounded-full border-2 border-success bg-background" />
              </span>

              <div className="min-w-0">
                <time className="mb-1 block text-xs tabular-nums text-muted-foreground sm:hidden">
                  {formatDateTime(event.createdAt)}
                </time>
                <p className="m-0 whitespace-pre-wrap break-words text-sm font-medium leading-5 text-foreground">
                  {title}
                </p>
                {detail ? (
                  <p className="m-0 mt-1 whitespace-pre-wrap break-words text-sm leading-5 text-muted-foreground">
                    {detail}
                  </p>
                ) : null}

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
    </section>
  );
}
