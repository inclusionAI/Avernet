import type { TaskEscortParityContext } from '@/domain/taskEscortParity/types';
import { useTaskEscortEvolutionRead } from '@/hooks/taskEscortParity/useTaskEscortEvolution';
import { evolutionService } from '@/services/taskEscortParity/evolutionService';
import { useCallback, useState } from 'react';
import { JsonDetails, NoData, ReadPager, ReadState } from './ReadPrimitives';
/** Legacy suggestions are independent records, never treated as an issue-summary execution. */
export function SuggestionHistory({ context, signature }: { context: TaskEscortParityContext; signature: string }) {
  const [page, setPage] = useState(1);
  const read = useCallback(
    (signal: AbortSignal) =>
      evolutionService.suggestions(context.workflowId, context.userId!, { limit: 20, offset: (page - 1) * 20 }, signal),
    [context.workflowId, context.userId, page],
  );
  const result = useTaskEscortEvolutionRead(context, 'legacy-suggestions', read);
  const ids = JSON.stringify(result.data?.suggestions.filter((s) => s.signature === signature).map((s) => s.id) ?? []);
  const taskRead = useCallback(
    (signal: AbortSignal) => evolutionService.applyTasks(JSON.parse(ids), context.userId!, signal),
    [ids, context.userId],
  );
  const tasks = useTaskEscortEvolutionRead(context, 'legacy-application-history', taskRead, !!result.data);
  return (
    <details className="rounded-lg border border-border p-3">
      <summary className="cursor-pointer text-sm font-medium">历史建议与应用记录（独立于本次修复）</summary>
      <p className="mt-3 text-xs text-muted-foreground">
        按工作流全部历史建议分页，每页 20 条；仅展示本页与此问题签名匹配的记录。应用或验证不代表所有聚合原因均已解决。
      </p>
      <ReadState {...result} />
      <ReadState {...tasks} />
      {result.data && (
        <>
          {result.data.suggestions
            .filter((s) => s.signature === signature)
            .map((s) => (
              <article key={s.id} className="mt-3 border-t border-border py-3">
                <p className="text-sm">{s.description}</p>
                <p className="text-xs text-muted-foreground">
                  {s.id} · {s.status} · {s.verificationStatus ?? '验证状态未知'}
                </p>
                <JsonDetails label="历史建议完整内容" value={s} />
                {tasks.data
                  ?.filter((t) => t.suggestionId === s.id)
                  .map((t) => (
                    <JsonDetails key={t.taskId} label={`应用记录 ${t.taskId} · ${t.status}`} value={t} />
                  ))}
              </article>
            ))}
          {!result.data.suggestions.some((s) => s.signature === signature) && (
            <NoData text="本页没有匹配此问题的历史建议" />
          )}
          <ReadPager
            label="历史建议"
            page={page}
            totalPages={Math.ceil(result.data.total / 20)}
            total={result.data.total}
            onChange={setPage}
          />
        </>
      )}
    </details>
  );
}
