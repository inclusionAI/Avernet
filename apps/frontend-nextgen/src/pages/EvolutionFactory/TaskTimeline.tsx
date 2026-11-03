import { Button } from '@/components/ui/Button';
import { formatTime, type Task } from '@/domain/evolution/types';
import { stepName } from './presentation';
import { Section, StatusBadge, typeTone } from './shared';

export function TaskTimeline({ task, onSelect }: { task: Task; onSelect: (stepId: string) => void }) {
  return (
    <Section title="执行时间线">
      <ol className="space-y-0">
        {task.steps?.map((item, index) => (
          <li key={item.stepId} className="relative flex gap-4 pb-6 last:pb-0">
            {index < (task.steps?.length ?? 0) - 1 && (
              <span aria-hidden="true" className="absolute left-3.5 top-8 bottom-0 w-px bg-divider" />
            )}
            <span
              className={`relative flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-xs ${
                ['failed', 'canceled'].includes(item.status)
                  ? 'bg-destructive/10 text-destructive'
                  : ['succeeded', 'completed'].includes(item.status)
                  ? 'bg-success/10 text-success'
                  : typeTone(task.task_type)
              }`}
            >
              {index + 1}
            </span>
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-3">
                <Button
                  variant="ghost"
                  style={{ height: 'auto', whiteSpace: 'normal' }}
                  type="button"
                  className="font-medium"
                  onClick={() => {
                    onSelect(item.stepId);
                  }}
                >
                  {stepName(item.stepType)}
                  {item.roundNo !== null && item.roundNo !== undefined ? ` · 第 ${item.roundNo} 轮` : ''}
                </Button>
                <StatusBadge status={item.status} />
                <span className="text-xs text-muted-foreground">
                  {formatTime(item.startedAt)} → {formatTime(item.completedAt)}
                </span>
              </div>
              <p className="mt-2 text-sm text-muted-foreground">{item.summary || '等待执行结果'}</p>
              {item.error?.message && (
                <p className="mt-2 text-sm text-destructive">
                  {item.error.code}：{item.error.message}
                </p>
              )}
            </div>
          </li>
        ))}
      </ol>
      {!task.steps?.length && <p className="text-sm text-muted-foreground">暂无执行步骤</p>}
    </Section>
  );
}
