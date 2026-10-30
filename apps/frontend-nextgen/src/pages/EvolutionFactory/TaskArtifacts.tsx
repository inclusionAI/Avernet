import { Button } from '@/components/ui/Button';
import { Modal, ModalContent, ModalHeader, ModalTitle } from '@/components/ui/Modal';
import { formatTime, type Step, type Task } from '@/domain/evolution/types';
import { FileText } from 'lucide-react';
import { stepName } from './presentation';
import { Section } from './shared';
import { outputHighlights, StepOutput } from './StepOutput';

export function TaskArtifacts({
  task,
  step,
  selected,
  onSelect,
  outputOpen,
  setOutputOpen,
  onViewDiff,
  diff,
  busy,
  operationError,
}: {
  task: Task;
  step?: Step;
  selected: string;
  onSelect: (stepId: string) => void;
  outputOpen: boolean;
  setOutputOpen: (open: boolean) => void;
  onViewDiff: () => void;
  diff?: string;
  busy: boolean;
  operationError: string;
}) {
  return (
    <Section title="产出物">
      <p className="mb-4 text-xs leading-relaxed text-muted-foreground">
        按执行步骤归档，查看诊断结论、目标规划和每轮优化结果。
      </p>
      <div className="grid gap-3 md:grid-cols-3">
        {task.steps
          ?.filter((item) => item.output)
          .map((item) => (
            <Button
              variant="ghost"
              style={{ height: 'auto', whiteSpace: 'normal' }}
              key={item.stepId}
              type="button"
              aria-label={`查看产出物 · ${stepName(item.stepType)} · ${item.stepId}`}
              aria-pressed={selected === item.stepId}
              className={`flex min-w-0 flex-col items-start rounded-xl border p-4 text-left transition-colors hover:border-primary hover:bg-primary/[0.02] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${
                selected === item.stepId ? 'border-primary/50 bg-primary/[0.03]' : 'border-border bg-card'
              }`}
              onClick={() => {
                onSelect(item.stepId);
              }}
            >
              <span className="mb-3 flex h-9 w-9 items-center justify-center rounded-lg bg-primary/5 text-primary">
                <FileText size={18} />
              </span>
              <strong className="text-sm">
                {stepName(item.stepType)}
                {item.roundNo !== null && item.roundNo !== undefined ? ` · 第 ${item.roundNo} 轮` : ''}
              </strong>
              <p className="mt-2 text-xs leading-relaxed text-muted-foreground">{item.summary || '查看实际产出'}</p>
              <div className="mb-4 mt-3 flex flex-wrap gap-1.5">
                {outputHighlights(item.output!).map((label) => (
                  <span key={label} className="rounded-md bg-muted px-2 py-1 text-xs text-muted-foreground">
                    {label}
                  </span>
                ))}
              </div>
              <div className="mt-auto flex w-full items-center justify-between gap-2 border-t border-divider pt-3">
                <span className="text-xs text-muted-foreground">{formatTime(item.completedAt)}</span>
                <span className="text-xs font-medium text-primary">查看详情 →</span>
              </div>
            </Button>
          ))}
      </div>
      {!task.steps?.some((item) => item.output) && <p className="text-sm text-muted-foreground">暂无已生成的产出物</p>}
      <Modal open={outputOpen && Boolean(step?.output)} onOpenChange={setOutputOpen}>
        <ModalContent size="xl" className="overflow-hidden" aria-describedby={undefined}>
          <ModalHeader>
            <ModalTitle className="text-base">产出物 · {step ? stepName(step.stepType) : ''}</ModalTitle>
          </ModalHeader>
          <div className="max-h-[calc(100vh-10rem)] min-h-0 overflow-y-auto pr-1">
            {step?.output && (
              <StepOutput
                key={step.stepId}
                output={step.output}
                stepId={step.stepId}
                showTitle={false}
                onViewDiff={
                  step.status === 'succeeded' && step.stepType === 'optimize' && step.output.diff
                    ? onViewDiff
                    : undefined
                }
                diff={diff}
                diffLoading={busy}
                diffError={operationError}
              />
            )}
          </div>
        </ModalContent>
      </Modal>
    </Section>
  );
}
