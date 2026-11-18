import { Button } from '@/components/ui/Button';
import type { RepairItem, RepairRevision } from '@/domain/taskEscortParity/repairTypes';
import type { TaskEscortParityContext } from '@/domain/taskEscortParity/types';
import { useTaskEscortEvolutionRead } from '@/hooks/taskEscortParity/useTaskEscortEvolution';
import { repairService } from '@/services/taskEscortParity/repairService';
import { useCallback, useState } from 'react';
import { JsonDetails, NoData, ReadState } from './ReadPrimitives';
function FrozenItem({
  context,
  revision,
  item,
}: {
  context: TaskEscortParityContext;
  revision: RepairRevision;
  item: RepairItem;
}) {
  const [open, setOpen] = useState(false);
  const read = useCallback(
    (signal: AbortSignal) =>
      repairService.material(revision.taskId, revision.revision, item.itemId, context.userId!, signal),
    [revision.taskId, revision.revision, item.itemId, context.userId],
  );
  const result = useTaskEscortEvolutionRead(
    context,
    `frozen:${revision.taskId}:${revision.revision}:${item.itemId}`,
    read,
    open && !!revision.input.materials,
  );
  return (
    <article className="space-y-3 rounded-lg border border-border bg-card p-4">
      <h4 className="break-words text-sm font-medium">
        {typeof item.proposal?.summary === 'string' ? item.proposal.summary : item.itemId}
      </h4>
      <p className="break-all text-xs text-muted-foreground">
        目标 {item.itemId} · 内容版本 {item.contentRevision}
      </p>
      {revision.input.materials ? (
        <>
          <Button variant="outline" size="sm" onClick={() => setOpen(true)} disabled={result.loading}>
            读取冻结材料 {item.itemId}
          </Button>
          <ReadState {...result} />
          {result.data && <JsonDetails label="原始冻结修改、来源与证据" value={result.data} open />}
        </>
      ) : (
        <JsonDetails label="原始冻结修改、来源与证据" value={item} open />
      )}
    </article>
  );
}

export function RepairTargets({ context, revision }: { context: TaskEscortParityContext; revision: RepairRevision }) {
  return (
    <section className="space-y-4" aria-label="优化目标">
      <h3 className="font-semibold">本版冻结优化目标 · {revision.input.items.length} 项</h3>
      <p className="text-xs text-muted-foreground">这些目标取自创建本修复版本时冻结的材料，不会以当前在线证据替代。</p>
      {revision.input.items.map((item) => (
        <FrozenItem key={item.itemId} context={context} revision={revision} item={item} />
      ))}
      {!revision.input.items.length && <NoData text="本版未提供优化目标" />}
      <JsonDetails label="本版冻结基线" value={revision.input.baseline} />
      {revision.error && <JsonDetails label="本版实际错误详情" value={revision.error} open />}
    </section>
  );
}
