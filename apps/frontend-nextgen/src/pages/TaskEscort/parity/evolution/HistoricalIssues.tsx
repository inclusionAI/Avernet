import { Button } from '@/components/ui/Button';
import { historicalVersions } from '@/domain/taskEscortParity/evolution';
import type { TaskEscortParityContext } from '@/domain/taskEscortParity/types';
import { useTaskEscortEvolutionRead } from '@/hooks/taskEscortParity/useTaskEscortEvolution';
import { evolutionService } from '@/services/taskEscortParity/evolutionService';
import { useCallback, useState } from 'react';
import { NoData, ReadPager, ReadState } from './ReadPrimitives';
function Archive({
  context,
  onOpen,
}: {
  context: TaskEscortParityContext;
  onOpen: (signature: string, snapshotKey: string) => void;
}) {
  const [page, setPage] = useState(1);
  const read = useCallback(
    (signal: AbortSignal) =>
      evolutionService.groups(
        context.workflowId,
        context.userId!,
        { versionScope: 'historical', page, pageSize: 20, sort: 'recent' },
        signal,
      ),
    [context.workflowId, context.userId, page],
  );
  const result = useTaskEscortEvolutionRead(context, 'historical-issues', read);
  return (
    <div className="mt-4 space-y-3">
      <ReadState {...result} />
      {result.data && (
        <>
          {historicalVersions(result.data.groups).map((v) => (
            <details key={v.snapshotKey} className="rounded-lg border border-border p-3">
              <summary className="cursor-pointer text-sm">
                {v.label} · 本页 {v.groups.length} 个问题
              </summary>
              {v.groups.map((g) => (
                <article key={g.signature} className="space-y-2 border-t border-border py-3">
                  <p className="break-all text-xs text-muted-foreground">{g.signature}</p>
                  <Button variant="outline" size="sm" onClick={() => onOpen(g.signature, v.snapshotKey)}>
                    查看历史问题 {g.signature}
                  </Button>
                  <p className="text-xs text-muted-foreground">仅显示此运行版本来源；历史建议只作当前优化目标参考。</p>
                </article>
              ))}
            </details>
          ))}
          {!result.data.groups.length && <NoData text="暂无历史问题" />}
          <ReadPager
            label="历史问题"
            page={page}
            totalPages={result.data.page.totalPages}
            total={result.data.page.total}
            onChange={setPage}
          />
        </>
      )}
    </div>
  );
}

export function HistoricalIssues({
  context,
  onOpen,
}: {
  context: TaskEscortParityContext;
  onOpen: (signature: string, snapshotKey: string) => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <details
      className="rounded-xl border border-border bg-card p-4"
      onToggle={(e) => {
        if (e.target === e.currentTarget) setOpen(e.currentTarget.open);
      }}
    >
      <summary className="cursor-pointer text-sm font-semibold">历史版本问题</summary>
      {open && <Archive context={context} onOpen={onOpen} />}
    </details>
  );
}
