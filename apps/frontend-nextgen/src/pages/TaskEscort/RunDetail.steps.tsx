import type { RunStep } from '@/hooks/taskEscortWorkspace/useRunDetail';
import { ArrowDown, ArrowRight, Check, Circle, CircleX, Clock3, GitBranch, LoaderCircle, Pause } from 'lucide-react';
import type { RunStatus } from './types';
import { Button, StatusBadge } from './ui';
const stepIcons = {
  success: Check,
  failed: CircleX,
  running: LoaderCircle,
  waiting: Clock3,
  cancelled: Pause,
  unknown: Circle,
};
const stepColors: Record<RunStatus, string> = {
  success: 'border-success/25 bg-success/5 text-success',
  failed: 'border-destructive/30 bg-destructive/5 text-destructive',
  running: 'border-primary/30 bg-primary/5 text-primary',
  waiting: 'border-border bg-muted/30 text-muted-foreground',
  cancelled: 'border-border bg-muted/30 text-muted-foreground',
  unknown: 'border-border bg-muted/30 text-muted-foreground',
};

export function RunStepGraph({
  steps,
  selected,
  onSelect,
}: {
  steps: RunStep[];
  selected: RunStep;
  onSelect: (id: string) => void;
}) {
  return (
    <section aria-labelledby="run-dag-heading">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h3 id="run-dag-heading" className="flex items-center gap-2 text-sm font-semibold">
          <GitBranch className="size-4 text-primary" />
          节点路径
        </h3>
        <span className="text-[11px] text-muted-foreground">点击节点查看状态 · 示意拓扑</span>
      </div>
      <ol
        className="flex flex-col items-stretch gap-2 rounded-lg border border-border p-4 sm:flex-row sm:items-center sm:overflow-x-auto"
        aria-label="运行节点 DAG"
      >
        {steps.map((step, index) => {
          const Icon = stepIcons[step.status];
          return (
            <li key={step.id} className="flex min-w-0 flex-1 flex-col items-center gap-2 sm:flex-row">
              <Button
                variant="outline"
                className={`h-auto min-h-20 w-full min-w-0 flex-col gap-2 whitespace-normal px-2 py-3 text-center text-xs ${
                  stepColors[step.status]
                } ${selected.id === step.id ? 'ring-2 ring-primary/30 ring-offset-2' : ''}`}
                aria-pressed={selected.id === step.id}
                aria-label={`查看节点 ${step.name}`}
                onClick={() => onSelect(step.id)}
              >
                <Icon
                  className={`size-4 ${step.status === 'running' ? 'motion-safe:animate-spin' : ''}`}
                  aria-hidden="true"
                />
                <span className="break-words leading-5">{step.name}</span>
              </Button>
              {index < steps.length - 1 && (
                <>
                  <ArrowRight className="hidden size-3 shrink-0 text-muted-foreground/60 sm:block" aria-hidden="true" />
                  <ArrowDown className="size-3 text-muted-foreground/60 sm:hidden" aria-hidden="true" />
                </>
              )}
            </li>
          );
        })}
      </ol>
      <div
        className="mt-3 flex flex-wrap items-center justify-between gap-3 rounded-lg bg-muted/40 px-4 py-3"
        aria-live="polite"
      >
        <div>
          <p className="text-xs font-medium">{selected.name}</p>
          <p className="mt-1 text-xs text-muted-foreground">{selected.detail}</p>
        </div>
        <StatusBadge status={selected.status} />
      </div>
    </section>
  );
}
export function RunStepTimeline({ steps }: { steps: RunStep[] }) {
  return (
    <section aria-labelledby="run-timeline-heading">
      <h3 id="run-timeline-heading" className="mb-4 flex items-center gap-2 text-sm font-semibold">
        <Clock3 className="size-4 text-primary" />
        执行时间线
      </h3>
      <ol className="space-y-0">
        {steps.map((step, index) => (
          <li key={step.id} className="relative flex gap-3 pb-5 last:pb-0">
            {index < steps.length - 1 && (
              <span className="absolute bottom-0 left-[9px] top-5 w-px bg-border" aria-hidden="true" />
            )}
            <span
              className={`relative z-10 flex size-5 shrink-0 items-center justify-center rounded-full border ${
                stepColors[step.status]
              }`}
            >
              <Circle className="size-1.5 fill-current" aria-hidden="true" />
            </span>
            <div className="min-w-0 flex-1">
              <div className="flex items-center justify-between gap-2">
                <p className="text-xs font-medium">{step.name}</p>
                <span className="mono text-[10px] text-muted-foreground">#{String(index + 1).padStart(2, '0')}</span>
              </div>
              <p className="mt-1 text-[11px] leading-5 text-muted-foreground">{step.detail}</p>
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}
