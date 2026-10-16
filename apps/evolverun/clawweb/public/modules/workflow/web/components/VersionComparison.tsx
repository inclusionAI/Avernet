import { useEffect, useState } from 'react'
import type { WorkflowVersionComparison, VersionObservation } from '../../server/contracts/workflow-version-comparison.js'

const formatTime = (ms: number) => new Date(ms).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false })
export function VersionComparison({ workflowId }: { workflowId: string }) {
  return <VersionComparisonPanel key={workflowId} workflowId={workflowId} />
}

function VersionComparisonPanel({ workflowId }: { workflowId: string }) {
  const [selection, setSelection] = useState<{ baseline?: number; current?: number }>(() => {
    const query = new URLSearchParams(window.location.search)
    if (query.has('workflowId') && query.get('workflowId') !== workflowId) return {}
    const read = (key: string) => /^\d+$/.test(query.get(key) ?? '') ? Number(query.get(key)) : undefined
    return { baseline: read('baseline'), current: read('current') }
  })
  const [data, setData] = useState<WorkflowVersionComparison | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [retry, setRetry] = useState(0)
  useEffect(() => {
    let cancelled = false
    setData(null); setLoading(true); setError('')
    const query = new URLSearchParams()
    if (selection.baseline !== undefined) query.set('baseline', String(selection.baseline))
    if (selection.current !== undefined) query.set('current', String(selection.current))
    fetch(`/api/workflows/${encodeURIComponent(workflowId)}/version-comparison?${query}`).then(async response => {
      if (!response.ok) throw new Error(response.status === 503
        ? '版本对比服务未就绪，请联系管理员检查服务配置。' : '版本对比加载失败')
      return response.json()
    }).then(json => { if (!cancelled) setData(json.data) }).catch((error: unknown) => { if (!cancelled) setError(error instanceof Error ? error.message : '版本对比加载失败') })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [workflowId, selection.baseline, selection.current, retry])
  const label = (version: WorkflowVersionComparison['versions'][number]) => {
    const kind = version.source?.kind
    const source = kind === 'auto_optimization' ? '自动优化 · 人工确认发布' : kind === 'manual_optimization' ? '手动优化' : kind === 'rollback' ? '回滚' : '来源未知'
    return `#${version.deployNumber} · ${source}${version.active ? ' · 当前生效' : ''}`
  }
  const observation = (title: string, sample: VersionObservation | null) => sample && (
    <div className="rounded-lg bg-slate-50 p-3">
      <div className="text-xs text-slate-500">{title} #{sample.deployNumber}</div>
      <div className="text-xl font-semibold text-slate-900">{sample.successRate === null ? '暂无终态样本' : `${sample.successRate.toFixed(1)}%`}</div>
      <div className="text-xs text-slate-500">成功 {sample.succeededRuns}/{sample.terminalRuns} · 失败 {sample.failedRuns}</div>
      <div className="mt-1 text-[10px] text-slate-500">{formatTime(sample.fromMs)} — {formatTime(sample.toMs)}</div>
    </div>
  )
  if (loading) return <p role="status" className="py-4 text-xs text-slate-400">正在加载版本对比…</p>
  if (error) return <p className="py-4 text-xs text-rose-500">{error} <button type="button" onClick={() => setRetry(value => value + 1)}>重试</button></p>
  if (!data) return null
  const selected = { baseline: selection.baseline ?? data.baseline?.deployNumber,
    current: selection.current ?? data.current?.deployNumber ?? data.versions.find(version => version.active)?.deployNumber }
  return <div>
    <div className="mb-3 flex flex-wrap gap-2">
      {(['baseline', 'current'] as const).map(side => <label key={side} className="text-xs text-slate-500">
        {side === 'baseline' ? '基线版本' : '对比版本'}
        <select aria-label={side === 'baseline' ? '基线版本' : '对比版本'} className="ml-2 max-w-64 rounded border border-slate-200 p-1" value={selected[side] ?? ''}
          onChange={event => setSelection(previous => ({ baseline: previous.baseline ?? data.baseline?.deployNumber,
            current: previous.current ?? data.current?.deployNumber ?? data.versions.find(v => v.active)?.deployNumber,
            [side]: event.target.value ? Number(event.target.value) : undefined }))}>
          <option value="">请选择实际部署版本</option>
          {data.versions.filter(version => version.deployNumber !== selected[side === 'baseline' ? 'current' : 'baseline'])
            .map(version => <option key={version.deployNumber} value={version.deployNumber}>{label(version)}</option>)}
        </select>
      </label>)}
    </div>
    {data.status === 'choose_baseline' && <p className="text-xs text-slate-500">发布前真实基线来源未知，请选择两个实际部署版本。</p>}
    {data.status === 'no_active_version' && <p className="text-xs text-slate-500">暂无当前生效版本，请选择两个实际部署版本。</p>}
    <div className="grid grid-cols-2 gap-2">{observation('基线', data.baseline)}{observation('对比', data.current)}</div>
    {data.differencePercentagePoints !== null && <p className="mt-2 text-sm text-slate-700">成功率{data.differencePercentagePoints >= 0 ? '提高' : '降低'} {Math.abs(data.differencePercentagePoints).toFixed(1)} 个百分点</p>}
    {data.status === 'no_samples' && <p className="mt-2 text-xs text-slate-500">尚无足够终态样本，暂不计算提升。</p>}
    {data.baseline && data.current && <details className="mt-2 text-xs text-slate-500"><summary>观察口径</summary>
      <p>两边均观察发布后 {(data.windowMs / 86400000).toFixed(2)} 天，最多 7 天。时间为上海时间，范围含起点、不含终点。</p>
      <p>终态含成功、失败、中止和取消；失败数包含中止与取消。排除运行中/等待：基线 {data.baseline.excludedUnfinished}，对比 {data.current.excludedUnfinished}；未知部署终态 {data.excludedUnknownDeployment}。</p>
      <p>按精确部署编号分组；少量样本需谨慎观察。指标变化不代表全部由优化造成。</p>
    </details>}
  </div>
}
