import { Button } from '@/components/ui/Button';
import { displayDate } from '@/domain/taskEscortParity/evolution';
import type { IssueGroup } from '@/domain/taskEscortParity/evolutionTypes';
import type { TaskEscortParityContext } from '@/domain/taskEscortParity/types';
import { useTaskEscortEvolutionRead } from '@/hooks/taskEscortParity/useTaskEscortEvolution';
import { evolutionService } from '@/services/taskEscortParity/evolutionService';
import { useCallback, useState } from 'react';
import { JsonDetails, NoData, ReadPager, ReadState } from './ReadPrimitives';
import { SuggestionHistory } from './SuggestionHistory';
export function IssueEvidence({
  context,
  group,
  snapshotKey,
}: {
  context: TaskEscortParityContext;
  group: IssueGroup;
  snapshotKey?: string;
}) {
  const requestedRun = context.query.runId,
    requestedAnalysis = context.query.analysisId;
  const index = group.sources.findIndex(
    (s) => (!requestedRun || s.flowId === requestedRun) && (!requestedAnalysis || s.analysisId === requestedAnalysis),
  );
  const [selected, setSelected] = useState(index >= 0 ? index : requestedRun || requestedAnalysis ? -1 : 0);
  const [page, setPage] = useState(Math.floor(Math.max(0, selected) / 10) + 1);
  const source = group.sources[selected];
  const run = source?.flowId ?? (snapshotKey === undefined ? requestedRun ?? '' : ''),
    analysisId = source?.analysisId ?? (snapshotKey === undefined ? requestedAnalysis : undefined);
  const read = useCallback(
    (signal: AbortSignal) => evolutionService.analysis(context.workflowId, run, analysisId, context.userId!, signal),
    [context.workflowId, context.userId, run, analysisId],
  );
  const result = useTaskEscortEvolutionRead(
    context,
    `analysis:${run}:${analysisId}`,
    read,
    !!run && analysisId !== 'legacy',
  );
  return (
    <div className="space-y-5">
      <section aria-label="相关分析记录">
        <h3 className="font-medium">相关分析记录 · {group.sources.length} 条</h3>
        {selected < 0 && (
          <p role="status" className="mt-2 text-xs text-muted-foreground">
            所选运行或分析不在该问题来源中；不会自动切到最新分析。
          </p>
        )}
        <div className="mt-3 divide-y divide-border rounded-lg border border-border">
          {group.sources.slice((page - 1) * 10, page * 10).map((s, i) => (
            <div key={`${s.sourceId}:${i}`} className="flex flex-wrap items-center gap-2 p-3">
              <Button
                variant="ghost"
                className="min-w-0 flex-1 justify-start whitespace-normal text-left"
                aria-pressed={selected === (page - 1) * 10 + i}
                aria-label={`选择分析 ${s.flowId} ${s.analysisId}`}
                onClick={() => setSelected((page - 1) * 10 + i)}
              >
                <span className="break-all text-xs">
                  {displayDate(s.completedAtMs)} · 运行 {s.flowId} · {s.analysisId}
                </span>
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={() =>
                  context.navigate('runs', {
                    runId: s.flowId,
                    analysisId: s.analysisId,
                    issueSignature: group.signature,
                  })
                }
              >
                打开运行 {s.flowId}
              </Button>
            </div>
          ))}
        </div>
        <ReadPager
          label="分析"
          page={page}
          totalPages={Math.ceil(group.sources.length / 10)}
          total={group.sources.length}
          onChange={setPage}
        />
      </section>
      {run && (
        <section className="space-y-3" aria-label="所选分析详情">
          <h3 className="break-all font-medium">所选分析详情 · {analysisId ?? '未指定分析 ID'}</h3>
          <ReadState {...result} />
          {result.data ? (
            <>
              <p className="text-xs text-muted-foreground">
                {result.data.status} · 证据完整性 {result.data.evidenceStatus ?? '未知'}
              </p>
              {(['facts', 'inferences', 'unknowns'] as const).map((key, i) => (
                <section key={key}>
                  <h4 className="text-sm font-medium">{['事实', '推断', '未知'][i]}</h4>
                  {result.data![key].length ? (
                    result.data![key].map((s, j) => (
                      <p className="mt-2 whitespace-pre-wrap break-words text-sm" key={j}>
                        {s}
                      </p>
                    ))
                  ) : (
                    <p className="text-xs text-muted-foreground">未提供</p>
                  )}
                </section>
              ))}
              <JsonDetails label="分析诊断与证据事件" value={result.data.diagnoses} />
            </>
          ) : (
            !result.loading &&
            !result.error && (
              <NoData
                text={analysisId === 'legacy' ? '历史诊断未提供完整分析记录' : '所选分析不存在；不会回退到最新分析'}
              />
            )
          )}
        </section>
      )}
      {snapshotKey === undefined ? (
        <SuggestionHistory context={context} signature={group.signature} />
      ) : (
        <p className="text-xs text-muted-foreground">旧建议与应用记录未提供版本归属，本历史版本不展示跨版本记录。</p>
      )}
    </div>
  );
}
