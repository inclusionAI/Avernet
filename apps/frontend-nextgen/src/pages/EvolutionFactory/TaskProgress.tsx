import type { Step } from '@/domain/evolution/types';
import { activeStatus, statusLabel } from '@/domain/evolution/types';
import { CheckCircle2 } from 'lucide-react';
import { finishedStep, progressStages, stepName } from './presentation';

export function TaskProgress({ steps }: { steps: Step[] }) {
  const completed = steps.filter(finishedStep).length;
  const stages = progressStages(steps);
  if (!steps.length) return <p className="text-sm text-muted-foreground">暂无执行步骤</p>;
  return (
    <div>
      <div className="mb-5 flex items-center gap-3">
        <div
          role="progressbar"
          aria-label="已生成步骤完成比例"
          aria-valuemin={0}
          aria-valuemax={steps.length}
          aria-valuenow={completed}
          className="h-1.5 flex-1 overflow-hidden rounded-full bg-muted"
        >
          <div className="h-full rounded-full bg-primary" style={{ width: `${(completed / steps.length) * 100}%` }} />
        </div>
        <span className="text-xs text-muted-foreground">
          已完成 {completed} / {steps.length} 个已生成步骤
        </span>
      </div>
      <ol className="flex overflow-x-auto pb-1" aria-label="执行阶段">
        {stages.map((step, index) => {
          const done = finishedStep(step);
          const running = activeStatus(step.status) || step.status.startsWith('waiting');
          const failed = ['failed', 'canceled', 'cancelled'].includes(step.status);
          return (
            <li key={step.stepType} className="relative min-w-28 flex-1 px-3 text-center">
              {index < stages.length - 1 && (
                <span
                  aria-hidden="true"
                  className={`absolute top-4 left-[calc(50%+24px)] right-[calc(-50%+24px)] h-px ${
                    done ? 'bg-primary/40' : 'bg-border'
                  }`}
                />
              )}
              <span
                className={`relative mx-auto mb-2 flex h-8 w-8 items-center justify-center rounded-full border text-sm ${
                  done
                    ? 'border-primary/40 bg-primary/5 text-primary'
                    : running
                    ? 'border-primary bg-primary text-primary-foreground'
                    : failed
                    ? 'border-destructive/30 bg-destructive/5 text-destructive'
                    : 'border-border bg-card text-muted-foreground'
                }`}
              >
                {done ? <CheckCircle2 size={16} /> : index + 1}
              </span>
              <p className="text-sm font-medium">{stepName(step.stepType)}</p>
              <p className="mt-1 text-xs text-muted-foreground">
                {statusLabel(step.status)}
                {step.roundNo !== null && step.roundNo !== undefined ? ` · 第 ${step.roundNo} 轮` : ''}
              </p>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
