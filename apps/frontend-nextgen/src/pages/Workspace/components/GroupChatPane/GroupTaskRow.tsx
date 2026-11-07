import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import { Button } from '@/components/ui';
import { formatTaskDuration, isTerminalTask, taskStatusLabels, type GroupTask } from '@/domain/groupTasks/projection';
import { CheckCircle2, ChevronRight, CircleHelp, Clock3, LoaderCircle, XCircle } from 'lucide-react';
import { useId, useState } from 'react';

function taskTime(timestamp: number | undefined, now: number): string {
  if (timestamp === undefined) return '时间未知';
  const date = new Date(timestamp);
  return date.toDateString() === new Date(now).toDateString()
    ? date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false })
    : date.toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric' });
}

/** Two-line activity row; full prompts and correlation metadata are disclosed on demand. */
export function GroupTaskRow({
  task,
  now,
  unread,
  pending,
  onCancel,
  onViewSource,
}: {
  task: GroupTask;
  now: number;
  unread: boolean;
  pending: boolean;
  onCancel: () => void;
  onViewSource: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const detailId = useId();
  const terminal = isTerminalTask(task.status);
  const duration = formatTaskDuration(
    task.status === 'queued' ? task.queuedAt : task.startedAt,
    terminal ? task.finishedAt ?? task.lastActivityAt : now,
  );
  const status = task.status === 'unknown' ? '待确认' : taskStatusLabels[task.status];
  const color =
    task.status === 'completed'
      ? 'text-success'
      : task.status === 'failed' || task.status === 'timed_out'
      ? 'text-destructive'
      : 'text-muted-foreground';
  const Icon =
    task.status === 'running'
      ? LoaderCircle
      : task.status === 'queued'
      ? Clock3
      : task.status === 'completed'
      ? CheckCircle2
      : task.status === 'unknown'
      ? CircleHelp
      : XCircle;
  const timeLabel =
    task.status === 'running'
      ? `运行 ${duration}`
      : task.status === 'queued'
      ? `已等待 ${duration}`
      : terminal && task.startedAt !== undefined
      ? `耗时 ${duration}`
      : taskTime(task.queuedAt, now);
  return (
    <article aria-label={`${task.summary}，${taskStatusLabels[task.status]}`} className="min-w-0">
      <div className="flex min-w-0 items-center gap-1">
        <Button
          variant="ghost"
          type="button"
          aria-expanded={expanded}
          aria-controls={detailId}
          aria-label={`${expanded ? '收起' : '展开'}任务：${task.summary}`}
          onClick={() => setExpanded((value) => !value)}
          className="flex h-auto min-h-16 min-w-0 flex-1 items-start gap-2.5 rounded-md px-2 py-3 text-left outline-none hover:bg-muted/40 focus-visible:ring-2 focus-visible:ring-ring"
        >
          <Icon className={`mt-0.5 size-4 shrink-0 ${color}`} aria-hidden />
          <span className="min-w-0 flex-1">
            <span className="flex min-w-0 items-center gap-2">
              <TooltipProvider>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <span className="min-w-0 flex-1 truncate text-sm font-medium text-foreground">{task.summary}</span>
                  </TooltipTrigger>
                  <TooltipContent>{task.summary}</TooltipContent>
                </Tooltip>
              </TooltipProvider>
              {unread ? <span className="shrink-0 text-xs text-primary">未读</span> : null}
              <span className={`shrink-0 text-xs ${color}`}>{status}</span>
            </span>
            <span className="mt-1 flex min-w-0 items-center justify-between gap-3 text-xs text-muted-foreground">
              <span className="truncate">{task.workerName}</span>
              <span className="shrink-0 tabular-nums">{timeLabel}</span>
            </span>
          </span>
          <ChevronRight
            className={`mt-0.5 size-3.5 shrink-0 text-muted-foreground ${expanded ? 'rotate-90' : ''}`}
            aria-hidden
          />
        </Button>
        {task.intentId && !terminal && (task.status === 'queued' || task.status === 'running' || pending) ? (
          <Button
            variant="ghost"
            size="sm"
            className="min-h-11 shrink-0 px-2 sm:min-h-8"
            disabled={pending}
            onClick={onCancel}
          >
            {pending ? '正在确认…' : task.status === 'queued' ? '取消' : '停止'}
          </Button>
        ) : null}
      </div>
      {expanded ? (
        <div
          id={detailId}
          role="region"
          aria-label={`任务详情：${task.summary}`}
          className="mb-3 ml-8 mr-2 rounded-md bg-muted/40 px-3 py-3 text-xs"
        >
          <p className="whitespace-pre-wrap break-words text-sm leading-relaxed text-foreground">{task.summary}</p>
          <dl className="mt-3 grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-2 text-muted-foreground">
            <dt>Worker</dt>
            <dd className="min-w-0 break-words text-foreground">{task.workerName}</dd>
            <dt>来源时间</dt>
            <dd className="tabular-nums">
              {task.queuedAt === undefined
                ? '时间未知'
                : new Date(task.queuedAt).toLocaleString('zh-CN', { hour12: false })}
            </dd>
            <dt>任务引用</dt>
            <dd className="min-w-0 break-all font-mono">{task.taskRef}</dd>
            {!terminal && task.status !== 'unknown' ? (
              <>
                <dt>最近活动</dt>
                <dd>最近活动：{Math.max(0, Math.floor((now - task.lastActivityAt) / 1000))} 秒前</dd>
              </>
            ) : null}
          </dl>
          {task.statusNote ? <p className="mt-3 leading-relaxed text-muted-foreground">{task.statusNote}</p> : null}
          {task.errorMessage ? <p className="mt-3 break-words text-destructive">{task.errorMessage}</p> : null}
          {task.source !== 'delivery' ? (
            <Button variant="ghost" size="sm" className="mt-2 min-h-11 px-0 sm:min-h-8" onClick={onViewSource}>
              查看来源消息
            </Button>
          ) : null}
        </div>
      ) : null}
    </article>
  );
}
