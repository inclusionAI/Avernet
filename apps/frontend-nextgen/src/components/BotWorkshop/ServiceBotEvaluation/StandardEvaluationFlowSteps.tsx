import { Badge, type BadgeTone } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import type { StandardEvaluationStepState } from '@/services/botWorkshop/standardEvaluationWorkflow';

const stepStatusLabel = (status: string) => {
  if (status === 'running') return '执行中';
  if (status === 'done') return '已完成';
  if (status === 'failed') return '失败';
  return '待执行';
};

const stepStatusTone: Record<string, BadgeTone> = {
  pending: 'neutral',
  running: 'primary',
  done: 'success',
  failed: 'error',
};

const stepStatusClassName = (status: string) => {
  if (status === 'running') return 'border-primary/30 bg-primary/5';
  if (status === 'done') return 'border-success/30 bg-success/5';
  if (status === 'failed') return 'border-destructive/30 bg-destructive/5';
  return 'border-border bg-background';
};

export function StandardEvaluationFlowSteps({
  steps,
  running,
  enabled,
  canStart,
  retryingStep,
  onRetry,
}: {
  steps: StandardEvaluationStepState[];
  running: boolean;
  enabled: boolean;
  canStart: boolean;
  retryingStep?: string;
  onRetry: (step: StandardEvaluationStepState['key']) => void;
}) {
  return (
    <div className="space-y-2">
      {steps.map((step, index) => {
        const retrying = retryingStep === step.key;
        const canRetry = step.status === 'failed' && !running && enabled && canStart;

        return (
          <div
            key={step.key}
            className={`flex flex-col gap-3 rounded-lg border p-4 transition-colors sm:flex-row sm:items-center sm:justify-between ${stepStatusClassName(step.status)}`}
          >
            <div className="flex min-w-0 flex-1 items-start gap-3">
              <span className="mt-0.5 inline-flex size-7 shrink-0 items-center justify-center rounded-full bg-muted text-xs font-semibold text-muted-foreground">
                {index + 1}
              </span>
              <div className="min-w-0">
                <p className="m-0 text-sm font-medium text-foreground">{step.title}</p>
                <p className="m-0 mt-1 text-xs leading-5 text-muted-foreground">{step.description}</p>
                {step.error ? (
                  <p className="m-0 mt-1 text-xs leading-5 text-destructive">失败原因：{step.error}</p>
                ) : null}
              </div>
            </div>
            <div className="flex shrink-0 flex-col items-end gap-2 sm:flex-row sm:items-center">
              <Badge tone={stepStatusTone[step.status]}>{stepStatusLabel(step.status)}</Badge>
              <Button
                variant="secondary"
                size="sm"
                disabled={!canRetry || retrying}
                onClick={() => onRetry(step.key)}
              >
                {retrying ? '重试中…' : '重试'}
              </Button>
            </div>
          </div>
        );
      })}
    </div>
  );
}
