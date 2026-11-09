import { Button } from '@/components/ui/Button';
import { HELP_STATUS, helpTaskProgress } from '@/domain/communityHelp/model';
import type { HelpTask } from '@/domain/communityHelp/types';
import { cn } from '@/utils/cn';
import { ArrowUpRight, Bot, GitBranch } from 'lucide-react';

export function HelpStatusBadge({ status }: { status: HelpTask['status'] }) {
  const tone = HELP_STATUS[status];
  return (
    <span
      className={cn(
        'inline-flex shrink-0 items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium',
        tone.className,
      )}
    >
      <span className="size-1.5 rounded-full bg-current" aria-hidden />
      {tone.label}
    </span>
  );
}

export function HelpProgress({ task }: { task: HelpTask }) {
  const percent = helpTaskProgress(task);
  const completed = task.stages.filter((stage) => stage.status === 'done').length;
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between gap-2 text-xs">
        <span className="text-muted-foreground">
          阶段进度 · {completed}/{task.stages.length}
        </span>
        <span className={cn('font-semibold tabular-nums', task.status === 'done' ? 'text-success' : 'text-primary')}>
          {percent}%
        </span>
      </div>
      <div
        role="progressbar"
        aria-label={`${task.title}的完成进度`}
        aria-valuenow={percent}
        aria-valuemin={0}
        aria-valuemax={100}
        className="h-1.5 overflow-hidden rounded-full bg-muted"
      >
        <div
          className={cn('h-full rounded-full transition-all', task.status === 'done' ? 'bg-success' : 'bg-primary')}
          style={{ width: `${percent}%` }}
        />
      </div>
    </div>
  );
}

export function HelpTaskCard({ task, onOpen }: { task: HelpTask; onOpen: () => void }) {
  const current = task.stages.find((stage) => stage.status !== 'done');
  const botNames = [...new Set(task.stages.flatMap((stage) => stage.botNames))];
  return (
    <article className="flex min-w-0 flex-col rounded-xl border border-border bg-card p-5 transition-shadow hover:shadow-sm">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <span className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
          <GitBranch className="size-3.5" aria-hidden />
          {task.source}
        </span>
        <HelpStatusBadge status={task.status} />
      </div>
      <Button
        variant="link"
        onClick={onOpen}
        className="h-auto justify-start whitespace-normal break-words p-0 text-left text-sm font-semibold leading-6 text-foreground no-underline hover:text-primary"
      >
        {task.title}
      </Button>
      <p className="mb-4 mt-2 line-clamp-2 text-xs leading-6 text-muted-foreground">{task.description}</p>
      <div className="mt-auto">
        <HelpProgress task={task} />
      </div>
      <div className="mt-3 flex min-h-8 items-center gap-2 rounded-lg bg-muted/40 px-2.5 py-2 text-xs">
        <Bot className="size-3.5 shrink-0 text-primary" aria-hidden />
        <span className="min-w-0 break-words text-muted-foreground">
          {current ? `${current.role} · ${current.botNames.join('、')}` : '所有协作阶段已完成'}
        </span>
      </div>
      <footer className="mt-4 flex flex-wrap items-center justify-between gap-2 border-t border-border pt-3">
        <span className="text-xs text-muted-foreground">
          {botNames.length} 个 Bot · {task.artifacts.length} 份示例产物
        </span>
        <Button
          variant="ghost"
          size="sm"
          className="px-0 text-primary"
          onClick={onOpen}
          rightIcon={<ArrowUpRight className="size-3.5" aria-hidden />}
        >
          查看进度
        </Button>
      </footer>
    </article>
  );
}
