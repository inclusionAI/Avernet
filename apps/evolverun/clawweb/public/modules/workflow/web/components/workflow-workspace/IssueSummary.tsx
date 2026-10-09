import type { IssueGroupView } from './issue-groups';

export default function IssueSummary({ group }: { group: IssueGroupView }) {
  const statusText = group.aggregationStatus === 'queued' ? '摘要生成中'
    : group.aggregationStatus === 'failed' ? '聚合失败或等待超时'
    : group.aggregationStatus === 'too_large' ? '聚合输入超出限制'
    : group.aggregationStatus === 'completed' ? '摘要已生成' : '摘要尚未生成';
  const sources = group.summarySources ?? group.sources;
  const coveredRuns = new Set(sources.flatMap(source => source.flowIds?.length ? source.flowIds : [source.flowId])).size;
  const currentRuns = new Set(group.flowIds ?? group.sources.flatMap(source => source.flowIds?.length ? source.flowIds : [source.flowId])).size;
  const originalReasons = [...group.sources].filter(source => source.reasoning?.trim())
    .sort((a, b) => b.completedAtMs - a.completedAtMs);
  return <section aria-label="问题原因总览">
    <h4 className="text-sm font-semibold text-slate-900">问题原因</h4>
    <div role="status" className="mt-3 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-xs leading-5 text-slate-600">
      <p className={group.stale || group.aggregationStatus !== 'completed' ? 'font-medium text-amber-800' : 'font-medium'}>
        {statusText}{group.stale ? ' · 摘要待更新' : ''}
      </p>
      {group.summary && <p>摘要依据 {coveredRuns} 个运行 · 当前关联 {currentRuns} 个运行</p>}
      <details className="mt-1"><summary className="cursor-pointer">摘要说明</summary>
        {group.stale && <p className="mt-1">保留历史摘要，尚未覆盖最新分析。原因依据与当前修复建议的来源范围可能不同。</p>}
        {group.aggregationStatus === 'failed' && <p className="mt-1">本次聚合失败或排队超过等待时间，具体原因需查看聚合任务日志；原始分析仍保留在“证据与历史”。</p>}
        {group.aggregationStatus === 'too_large' && <p className="mt-1">诊断输入超过聚合限制，可先查看“证据与历史”中的单次分析。</p>}
        <p className="mt-1">模型归纳仅用于解释问题；实际修复范围以“修复建议”中明确勾选的建议为准。</p>
        {!!group.aggregationInputSummary?.compactSources && <p className="mt-1">聚合输入 {group.aggregationInputSummary.totalSources} 条诊断：
          {group.aggregationInputSummary.fullSources} 条完整输入，{group.aggregationInputSummary.compactSources} 条精简输入。</p>}
      </details>
    </div>
    {!group.summary && <div className="mt-4 space-y-3">
      <h5 className="text-sm font-semibold text-slate-900">原始诊断 · 尚未汇总</h5>
      {originalReasons.length ? <>
        <p className="text-xs text-slate-500">以下按来源分别展示，不代表这些运行具有相同原因。</p>
        {originalReasons.slice(0, 3).map(source => <article key={source.sourceId} className="rounded-lg border border-slate-200 p-3">
          <p className="mb-2 break-all text-xs text-slate-500">运行 {source.flowId}
            {source.completedAtMs > 0 && <> · {new Date(source.completedAtMs).toLocaleString()}</>}</p>
          <p className="whitespace-pre-wrap break-words text-sm leading-6 text-slate-800">{source.reasoning.length > 360 ? `${source.reasoning.slice(0, 360)}…` : source.reasoning}</p>
          {source.reasoning.length > 360 && <details className="mt-2 text-xs text-slate-600"><summary className="cursor-pointer">完整原始诊断</summary>
            <p className="mt-2 whitespace-pre-wrap break-words leading-6">{source.reasoning}</p></details>}
        </article>)}
        {originalReasons.length > 3 && <p className="text-xs text-slate-500">展示最近 3 条，共 {originalReasons.length} 条；其余可在“证据与历史”查看。</p>}
      </> : <p className="text-sm text-slate-600">原始诊断未记录原因，可在“证据与历史”核对运行事件；目前不能给出原因结论。</p>}
    </div>}
    {group.summary && <>
      <p className="mt-3 text-sm leading-6 text-slate-700">{group.summary.summary.length > 180 ? `${group.summary.summary.slice(0, 180)}…` : group.summary.summary}</p>
      {group.summary.summary.length > 180 && <details className="mt-2 text-xs text-slate-500"><summary className="cursor-pointer">展开完整摘要</summary>
        <p className="mt-2 whitespace-pre-wrap break-words leading-6">{group.summary.summary}</p></details>}
      <div className="mt-4 divide-y divide-slate-100">
        {group.summary.causes.map((cause, index) => {
          const linked = sources.filter(s => cause.sourceIds.includes(s.sourceId));
          const runIds = [...new Set(linked.map(s => s.flowId))];
          return <article key={index} className="py-3">
            <div className="flex flex-wrap items-center gap-2"><h5 className="text-sm font-medium text-slate-900">{cause.title}</h5>
              <span className="text-[10px] text-slate-500">{cause.certainty === 'supported' ? '有依据' : cause.certainty === 'hypothesis' ? '推测' : '待确认'}</span>
              <span className="text-[10px] text-slate-400">依据覆盖 {runIds.length} 个运行</span></div>
            <details className="mt-2 text-xs text-slate-600"><summary className="cursor-pointer">展开原因分析</summary>
              <p className="mt-2 whitespace-pre-wrap leading-5">{cause.conclusion}</p></details>
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
      {group.summary.unknowns.length > 0 && <details className="mt-2 rounded bg-amber-50 p-3 text-xs leading-5 text-amber-800">
        <summary className="cursor-pointer font-medium">待确认（{group.summary.unknowns.length} 项）</summary>{group.summary.unknowns.map((text, i) => <p key={i}>{text}</p>)}
      </details>}
    </>}
  </section>;
}
