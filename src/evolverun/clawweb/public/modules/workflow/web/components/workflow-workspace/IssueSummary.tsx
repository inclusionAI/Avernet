import type { IssueGroupView } from './issue-groups';

export default function IssueSummary({ group }: { group: IssueGroupView }) {
  const statusText = group.aggregationStatus === 'queued' ? '正在生成聚合结论…'
    : group.aggregationStatus === 'failed' ? '聚合失败，单次分析已保留；下次分析时会重试。'
    : group.aggregationStatus === 'too_large' ? '诊断输入过多，暂不能生成完整聚合；请查看单次分析。'
    : '尚未生成聚合结论；下一次运行分析完成后更新。';
  const sources = group.summarySources ?? group.sources;
  return <section aria-label="聚合结论">
    <h4 className="text-xs font-semibold text-slate-900">聚合结论</h4>
    {group.aggregationStatus !== 'completed' && <p role="status" className="mt-2 text-xs text-amber-700">{statusText}</p>}
    {group.stale && <p className="mt-2 text-xs text-amber-700">以下为上次聚合，尚未覆盖最新分析。</p>}
    {group.summary && <>
      <p className="mt-2 whitespace-pre-wrap break-words text-sm leading-6 text-slate-600">{group.summary.summary}</p>
      <div className="mt-4 divide-y divide-slate-100">
        {group.summary.causes.map((cause, index) => {
          const linked = sources.filter(s => cause.sourceIds.includes(s.sourceId));
          const runIds = [...new Set(linked.map(s => s.flowId))];
          return <article key={index} className="py-3">
            <div className="flex flex-wrap items-center gap-2"><h5 className="text-sm font-medium text-slate-900">{cause.title}</h5>
              <span className="text-[10px] text-slate-500">{cause.certainty === 'supported' ? '有依据' : cause.certainty === 'hypothesis' ? '推测' : '待确认'}</span>
              <span className="text-[10px] text-slate-400">影响 {runIds.length} 个运行</span></div>
            <p className="mt-1 whitespace-pre-wrap text-xs leading-5 text-slate-600">{cause.conclusion}</p>
            <details className="mt-2 text-xs text-slate-500"><summary className="cursor-pointer">查看来源运行与依据</summary>
              {runIds.map(id => <p key={id} className="mt-2 break-all font-mono text-[10px]">{id}</p>)}
              {linked.map(s => <div key={s.sourceId} className="mt-2 border-l-2 border-slate-100 pl-2">
                <p className="text-[10px]">{s.analysisId}</p><p className="mt-1 leading-5">{s.reasoning}</p>
                {!!s.evidenceEventIds?.length && <p className="mt-1 break-all text-[10px]">证据：{s.evidenceEventIds.join('、')}</p>}
                {s.proposal && <p className="mt-1 text-xs text-slate-600">该诊断的候选建议（非聚合执行指令）：{s.proposal.summary}</p>}
              </div>)}
            </details>
          </article>;
        })}
      </div>
      {group.summary.unknowns.length > 0 && <div className="mt-2 rounded bg-amber-50 p-3 text-xs leading-5 text-amber-800">
        <p className="font-medium">待确认</p>{group.summary.unknowns.map((text, i) => <p key={i}>{text}</p>)}
      </div>}
    </>}
  </section>;
}
