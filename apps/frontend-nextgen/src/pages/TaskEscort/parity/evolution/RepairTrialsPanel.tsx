import { Button } from '@/components/ui/Button';
import type { TaskEscortParityContext } from '@/domain/taskEscortParity/types';
import { useTaskEscortEvolutionRead } from '@/hooks/taskEscortParity/useTaskEscortEvolution';
import { repairService } from '@/services/taskEscortParity/repairService';
import { useCallback, useState } from 'react';
import { JsonDetails, NoData, ReadState } from './ReadPrimitives';
export function RepairTrialsPanel({ context, taskId }: { context: TaskEscortParityContext; taskId: string }) {
  const [open, setOpen] = useState(false),
    [offset, setOffset] = useState(0);
  const read = useCallback(
    (signal: AbortSignal) => repairService.trials(taskId, offset, context.userId!, signal),
    [taskId, offset, context.userId],
  );
  const result = useTaskEscortEvolutionRead(context, `trials:${taskId}:${offset}`, read, open);
  return (
    <details
      className="rounded-lg border border-border p-3"
      onToggle={(e) => {
        if (e.target === e.currentTarget) setOpen(e.currentTarget.open);
      }}
    >
      <summary className="cursor-pointer text-sm font-medium">试运行历史（只读）</summary>
      <ReadState {...result} />
      {result.data && (
        <>
          <p className="mt-3 text-xs text-muted-foreground">
            {result.data.configured ? '服务端已配置试运行' : '服务端未配置试运行'}
            。启动试运行可能调用有写入副作用的工具，本页不会启动。
          </p>
          {result.data.trials.map((t, i) => (
            <JsonDetails key={String(t.id ?? i)} label={`实际试运行 ${String(t.id ?? i + 1)}`} value={t} />
          ))}
          {!result.data.trials.length && <NoData text="暂无试运行记录" />}
          <div className="mt-3 flex gap-2">
            <Button variant="outline" size="sm" disabled={offset === 0} onClick={() => setOffset(0)}>
              首批试运行
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={result.data.nextOffset === null}
              onClick={() => {
                if (result.data && result.data.nextOffset !== null) setOffset(result.data.nextOffset);
              }}
            >
              下一批试运行
            </Button>
            <Button size="sm" disabled>
              启动试运行
            </Button>
          </div>
        </>
      )}
    </details>
  );
}
