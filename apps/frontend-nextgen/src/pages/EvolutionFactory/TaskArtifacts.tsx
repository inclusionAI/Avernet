import { Button } from '@/components/ui/Button';
import { Modal, ModalContent, ModalHeader, ModalTitle } from '@/components/ui/Modal';
import { formatTime, type Step, type Task } from '@/domain/evolution/types';
import { ChartNoAxesCombined, FileText, GitCompare } from 'lucide-react';
import { object, present } from './OutputPrimitives';
import { stepName } from './presentation';
import { Section } from './shared';
import { StepOutput } from './StepOutput';

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
      <div className="grid gap-3 md:grid-cols-3">
        {task.steps
          ?.filter((item) => item.output)
          .map((item) => {
            const output = item.output!;
            const changes = present(object(output.diff));
            const comparison = present(object(output.scoreComparison));
            const title = changes
              ? '变更集'
              : comparison
              ? '跑分对比'
              : present(object(output.diagnosis))
              ? '诊断报告'
              : typeof output.benchRunId === 'string' && output.benchRunId
              ? '评测结果'
              : stepName(item.stepType);
            const Icon = changes ? GitCompare : comparison ? ChartNoAxesCombined : FileText;
            return (
              <Button
                variant="ghost"
                style={{ height: 'auto', whiteSpace: 'normal' }}
                key={item.stepId}
                type="button"
                aria-label={`查看产出物 · ${stepName(item.stepType)} · ${item.stepId}`}
                aria-pressed={selected === item.stepId}
                className={`flex min-h-[100px] min-w-0 items-start justify-start gap-3 rounded-lg border p-4 text-left transition-colors hover:border-primary hover:bg-primary/5 ${
                  selected === item.stepId ? 'border-primary/50 bg-primary/5' : 'border-border bg-card'
                }`}
                onClick={() => {
                  onSelect(item.stepId);
                }}
              >
                <span className="flex size-5 shrink-0 items-center justify-center text-primary">
                  <Icon aria-hidden="true" size={20} />
                </span>
                <span className="min-w-0 flex-1">
                  <strong className="block text-[13px] font-medium">{title}</strong>
                  <span className="mt-1 block line-clamp-2 text-xs font-normal leading-[18px] text-muted-foreground">
                    {item.summary || '查看实际产出'}
                  </span>
                  <span className="mt-2 block truncate text-[11px] font-normal text-muted-foreground">
                    {stepName(item.stepType)}
                    {item.roundNo !== null && item.roundNo !== undefined ? ` · 第 ${item.roundNo} 轮` : ''}
                    {' · '}
                    {formatTime(item.completedAt)}
                  </span>
                </span>
              </Button>
            );
          })}
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
