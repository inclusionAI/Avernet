import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import type { RepairRevision } from '../../../../server/contracts/repair-batch'
import type { RepairTrial, RepairTrialOptions, RepairTrialRequest, RepairTrialTarget } from '../../../../server/contracts/repair-trial'
import { repairBatches } from '../../../api/repair-batches'
import { repairReleaseError } from '../../../api/repair-release-error'
import { button, primary } from './repair-view'

type TrialView = RepairTrial
const labels = { dispatching: '已下发，等待接收', awaiting_run: '等待运行登记', running: '执行中', waiting: '等待审批或人工输入', succeeded: '运行成功', failed: '运行失败', unknown: '结果未知，核对原请求' }
export default function RepairTrialPanel({ taskId, revision, enabled, open, onClose, onTrials }: {
  taskId: string; revision: RepairRevision; enabled: boolean; open: boolean; onClose: () => void; onTrials: (trials: TrialView[], configured: boolean) => void;
}) {
  const [nextOffset, setNextOffset] = useState<number | null>(null)
  const [records, setRecords] = useState<TrialView[]>([])
  const [options, setOptions] = useState<(RepairTrialOptions & { inputSchemaDigest: string; targets: RepairTrialTarget[] }) | null>(null)
  const [targetId, setTargetId] = useState('')
  const [savedMode, setSavedMode] = useState(false)
  const [params, setParams] = useState<Record<string, unknown>>({})
  const [json, setJson] = useState('{}')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [submitted, setSubmitted] = useState(false)
  const [uncertain, setUncertain] = useState(false)
  const loadedOffset = useRef(0)
  const inFlight = useRef(false)
  const request = useRef<RepairTrialRequest | null>(null)
  const scope = `${taskId}:${revision.revision}:${revision.draft?.candidateCommit}`
  const current = useRef(scope); current.current = scope
  async function refresh(offset = 0) {
    const mine = scope
    try {
      const response = await repairBatches.trials(taskId, offset)
      if (current.current !== mine) return
      setRecords(previous => { const map = new Map([...previous, ...response.trials].map(row => [row.id, row])); return [...map.values()].sort((a, b) => b.createdAtMs - a.createdAtMs) });
      if (offset > 0 || loadedOffset.current === 0) setNextOffset(response.nextOffset ?? null)
      loadedOffset.current = Math.max(loadedOffset.current, offset)
      onTrials(response.trials, response.configured)
      if (request.current && response.trials.some(row => row.requestId === request.current!.requestId)) { request.current = null; setUncertain(false) }
    } catch (reason) { if (current.current === mine) setError(repairReleaseError(reason)) }
  }
  useEffect(() => {
    loadedOffset.current = 0; setBusy(false); setSubmitted(false); setNextOffset(null); setRecords([]); setOptions(null); setTargetId(''); setSavedMode(false); setParams({}); setJson('{}'); setError(''); request.current = null; setUncertain(false)
    void refresh()
  }, [scope])
  useEffect(() => {
    const pending = records.some(row => ['dispatching', 'awaiting_run', 'running', 'waiting', 'unknown'].includes(row.status))
    if (!pending && !uncertain) return
    const timer = setInterval(() => { void refresh() }, 5000)
    return () => clearInterval(timer)
  }, [scope, records, uncertain])
  useEffect(() => {
    if (!open || !enabled) return
    const mine = scope
    void repairBatches.trialOptions(taskId, revision.revision).then(value => {
      if (current.current === mine) setOptions(value)
    }).catch(reason => { if (current.current === mine) setError(repairReleaseError(reason)) })
  }, [scope, open, enabled])
  async function start() {
    if (!options || !targetId || !enabled || inFlight.current || submitted && !uncertain) return
    const mine = scope
    inFlight.current = true; setSubmitted(true); setBusy(true); setError('')
    try {
      request.current ??= { mode: savedMode ? 'saved' : 'pinned', revision: revision.revision, expectedStateVersion: revision.stateVersion,
        requestId: crypto.randomUUID(), targetId, input: options.inputSchema?.properties ? Object.fromEntries(Object.entries(params).map(([key, value]) => [key, ['object', 'array'].includes((options.inputSchema!.properties as Record<string, { type?: string }>)[key]?.type ?? '') && typeof value === 'string' ? JSON.parse(value) : value])) : JSON.parse(json), inputSchemaDigest: options.inputSchemaDigest }
      await repairBatches.startTrial(taskId, request.current)
      if (current.current !== mine) return
      request.current = null; setUncertain(false); await refresh()
    } catch (reason) {
      if (current.current !== mine) return
      const status = (reason as { status?: number }).status
      if (!request.current || status && status >= 400 && status < 500) { request.current = null; setSubmitted(false) }
      setError(reason instanceof SyntaxError && !request.current ? '参数 JSON 格式无效，请检查对象、逗号和引号。' : repairReleaseError(reason)); setUncertain(!!request.current); await refresh()
    } finally { inFlight.current = false; if (current.current === mine) setBusy(false) }
  }
  const fields = options?.inputSchema?.properties as Record<string, { type?: string; title?: string; description?: string; enum?: unknown[] }> | undefined
  return <section aria-label="候选试跑" className="mt-4 space-y-3 border-t border-input pt-4">
    {open && <div className="space-y-3">
      <h4 className="font-semibold">先试跑候选 v{revision.revision}</h4>
      <p className="text-sm text-muted-foreground">这是真实执行，可能调用 MCP 或写入业务系统。不会自动发布，也不计入正式版本效果。</p>
      {!options ? <p role="status">正在读取候选参数和授权生产 Bot…</p> : <>
        <label className="block text-sm"><input type="checkbox" checked={savedMode} disabled={busy || uncertain || submitted} onChange={event => setSavedMode(event.target.checked)} /> 使用旧插件兼容模式</label>
        {!savedMode && <p className="text-sm text-muted-foreground">精确执行需要新版 ClawMind；未更新的插件请在执行前选择兼容模式。</p>}
        {savedMode && <p className="text-sm text-muted-foreground">运行远端已保存版，未固定此候选，结果仅供参考。仅在保存版与候选参数定义一致时可用。旧版可能重复执行或触发原有通知；参数按旧命令方式传递，不支持敏感输入。</p>}
        <label className="block text-sm">执行 Bot<select aria-label="执行 Bot" className="ml-3 rounded border border-input bg-background p-2" value={targetId} disabled={busy || uncertain} onChange={event => setTargetId(event.target.value)}>
          <option value="">请选择生产 Bot</option>{options.targets.map(bot => <option key={bot.id} value={bot.id}>{bot.label}</option>)}
        </select></label>
        {!options.targets.length && <p>没有已授权且支持工作流运行的生产 Bot。</p>}
        {fields ? Object.entries(fields).map(([key, spec]) => <label key={key} className="block text-sm">{spec.title ?? key}{Array.isArray(options.inputSchema?.required) && options.inputSchema.required.includes(key) ? ' *' : ''}
          {spec.enum ? <select aria-label={key} className="ml-3 rounded border border-input bg-background p-2" disabled={busy || uncertain} value={params[key] === undefined ? '' : JSON.stringify(params[key])} onChange={event => setParams({ ...params, [key]: JSON.parse(event.target.value) })}>
            <option value="" disabled>请选择</option>{spec.enum.map(value => <option key={JSON.stringify(value)} value={JSON.stringify(value)}>{String(value)}</option>)}
          </select> : spec.type === 'boolean' ? <input aria-label={key} type="checkbox" checked={params[key] === true} disabled={busy || uncertain} onChange={event => setParams({ ...params, [key]: event.target.checked })} />
          : <input aria-label={key} type={/password|token|secret|key/i.test(key) ? 'password' : ['number', 'integer'].includes(spec.type ?? '') ? 'number' : 'text'} className="ml-3 rounded border border-input bg-background p-2" disabled={busy || uncertain}
            value={params[key] === undefined ? '' : typeof params[key] === 'object' ? JSON.stringify(params[key]) : String(params[key])}
            onChange={event => { const value = event.target.value; setParams({ ...params, [key]: ['number', 'integer'].includes(spec.type ?? '') && value !== '' ? Number(value) : value }) }} />}
          {spec.description && <span className="ml-2 text-muted-foreground">{spec.description}</span>}
        </label>) : <label className="block text-sm">输入参数 JSON<textarea aria-label="输入参数 JSON" className="mt-2 block min-h-28 w-full rounded border border-input bg-background p-3 font-mono" disabled={busy || uncertain} value={json} onChange={event => setJson(event.target.value)} /></label>}
        <div className="flex gap-2"><button className={primary} disabled={!targetId || busy || submitted && !uncertain} onClick={() => void start()}>{busy ? '提交中…' : uncertain ? '核对并重试原请求' : submitted ? '试跑已提交' : savedMode ? '运行远端保存版' : '执行此候选'}</button>{submitted && !uncertain && records.some(row => row.revision === revision.revision && ['succeeded', 'failed'].includes(row.status)) && <button className={button} onClick={() => { setSubmitted(false); request.current = null }}>新建试跑</button>}<button className={button} onClick={onClose}>收起试跑参数</button></div>
      </>}
    </div>}
    {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
    {records.length > 0 && <details open={open}><summary className="cursor-pointer text-sm">试跑记录（{records.length}）</summary><div className="max-h-96 space-y-3 overflow-auto py-3">{records.map(row => <article key={row.id} className="rounded border border-input bg-background p-3 text-sm">
      <p className="font-medium">{row.mode === 'saved' ? '兼容执行：远端保存版' : `候选 v${row.revision}`} · {labels[row.status]}{row.mode !== 'saved' && row.candidateCommit !== revision.draft?.candidateCommit && <span className="ml-2 text-muted-foreground">该结果属于旧候选</span>}</p>
      {row.mode === 'saved' && <p className="text-muted-foreground">未固定候选、仅供参考；此结果不代表已验证候选 v{row.revision}。</p>}
      <p className="mt-1 text-muted-foreground">{row.target.label} · {new Date(row.createdAtMs).toLocaleString()} {row.durationMs != null && `· ${row.durationMs} ms`}</p>
      {row.error && <p className="mt-2 text-destructive">{row.error}</p>}
      {row.output && <pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap break-words">{row.output}</pre>}
      {row.flowId && <Link className="mt-2 inline-block text-primary underline" to={`/runs/${encodeURIComponent(row.flowId)}`}>运行详情、节点与日志</Link>}
      <details className="mt-2"><summary>参数（已脱敏）</summary><pre className="max-h-36 overflow-auto whitespace-pre-wrap">{JSON.stringify(row.input, null, 2)}</pre></details>
    </article>)}</div>{nextOffset !== null && <button className={button} onClick={() => void refresh(nextOffset)}>加载更早试跑</button>}</details>}
  </section>
}
