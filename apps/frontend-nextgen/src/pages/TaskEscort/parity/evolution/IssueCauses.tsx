import { displayDate } from '@/domain/taskEscortParity/evolution';
import type { IssueGroup } from '@/domain/taskEscortParity/evolutionTypes';
import { JsonDetails, NoData } from './ReadPrimitives';
export function IssueCauses({ group }: { group: IssueGroup }) {
  const sources = group.summarySources ?? group.sources;
  const covered = new Set(sources.flatMap((s) => (s.flowIds.length ? s.flowIds : [s.flowId]))).size;
  return (
    <section aria-label="问题原因总览" className="space-y-4">
      <h3 className="font-semibold">问题原因</h3>
      <div className="rounded-lg border border-border bg-muted/40 p-3 text-xs leading-6">
        <p>
          {group.summaryScopeNote}
          {group.summary && (group.stale || group.aggregationStatus !== 'completed') ? '展示上轮摘要 · ' : ''}
          {(
            {
              queued: '摘要生成中',
              failed: '聚合失败或等待超时',
              too_large: '聚合输入超出限制',
              completed: '摘要已生成',
              not_generated: '摘要尚未生成',
            } as Record<string, string>
          )[group.aggregationStatus] ?? group.aggregationStatus}
          {group.stale ? ' · 摘要待更新' : ''}
        </p>
        {group.summary && (
          <p>
            摘要依据 {covered} 个运行 · 当前关联 {group.flowIds.length} 个运行
          </p>
        )}
        <p>模型归纳仅解释问题，不代表所有原因均已解决；修复范围与摘要来源可能不同。</p>
        {group.inputSummary && <JsonDetails label="聚合输入覆盖" value={group.inputSummary} />}
      </div>
      {group.summary ? (
        <>
          <p className="whitespace-pre-wrap break-words text-sm leading-6">{group.summary.summary}</p>
          {group.summary.causes.map((c, i) => (
            <article key={i} className="space-y-2 border-t border-border py-3 text-sm">
              <h4 className="font-medium">
                {c.title}{' '}
                <span className="text-xs text-muted-foreground">
                  {({ supported: '有依据', hypothesis: '推测', unknown: '待确认' } as Record<string, string>)[
                    c.certainty
                  ] ?? c.certainty}
                </span>
              </h4>
              <p className="whitespace-pre-wrap">{c.conclusion}</p>
              <p className="break-all text-xs text-muted-foreground">来源 ID：{c.sourceIds.join('、') || '未提供'}</p>
              <details className="text-xs">
                <summary className="cursor-pointer">查看来源运行与依据</summary>
                {c.sourceIds.map((id) => {
                  const s = sources.find((v) => v.sourceId === id);
                  return (
                    <div key={id} className="mt-2 break-all border-l border-border pl-3">
                      {s ? (
                        <>
                          <p>
                            运行 {s.flowId} · 分析 {s.analysisId} · {s.sourceId}
                          </p>
                          <p className="whitespace-pre-wrap">{s.reasoning ?? '未记录单次原因'}</p>
                          <p>证据：{s.evidenceEventIds.join('、') || '未提供'}</p>
                        </>
                      ) : (
                        <p>{id}：本次响应未包含该来源</p>
                      )}
                    </div>
                  );
                })}
              </details>
            </article>
          ))}
          <section aria-label="待确认">
            <h4 className="text-sm font-medium">待确认</h4>
            {group.summary.unknowns.length ? (
              group.summary.unknowns.map((u, i) => (
                <p className="mt-2 text-sm text-muted-foreground" key={i}>
                  {u}
                </p>
              ))
            ) : (
              <p className="text-xs text-muted-foreground">本摘要未列出待确认项，不等于不存在未知。</p>
            )}
          </section>
        </>
      ) : (
        <>
          <h4 className="font-medium">原始诊断 · 尚未汇总</h4>
          <p className="text-xs text-muted-foreground">按来源分别展示，不代表这些运行具有相同原因。</p>
          {group.sources
            .filter((s) => s.reasoning?.trim())
            .map((s) => (
              <article key={s.sourceId} className="space-y-2 rounded-lg border border-border p-3">
                <p className="break-all text-xs text-muted-foreground">
                  运行 {s.flowId} · {s.analysisId} · {displayDate(s.completedAtMs)}
                </p>
                <p className="whitespace-pre-wrap break-words text-sm leading-6">{s.reasoning}</p>
              </article>
            ))}
          {!group.sources.some((s) => s.reasoning?.trim()) && (
            <NoData text="原始诊断未记录原因，目前不能给出原因结论" />
          )}
        </>
      )}
    </section>
  );
}
