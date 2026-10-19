import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import type { RepairRevision } from '../../../../server/contracts/repair-batch'
import { normalizeTrialInputDefinition, TrialInputDefinitionError } from '../../../../server/contracts/repair-trial-input-definition'
import RepairTrialInputs, { parseTrialJson, readTrialFormInput, trialInputDefaults, trialInputEntries, type TrialParameterEntry } from './RepairTrialInputs'
import type { RepairTrial, RepairTrialOptions, RepairTrialRequest, RepairTrialTarget } from '../../../../server/contracts/repair-trial'
import { repairBatches } from '../../../api/repair-batches'
import { repairReleaseError } from '../../../api/repair-release-error'
import { button, primary, RepairDialog } from './repair-view'

type TrialView = RepairTrial
const labels = { dispatching: '已下发，等待接收', awaiting_run: '等待运行登记', running: '执行中', waiting: '等待审批或人工输入', succeeded: '运行成功', failed: '运行失败', unknown: '结果未知，核对原请求' }
function capabilityRejectedBeforeCreation(reason: unknown): boolean {
  const failure = reason as { status?: number; body?: string }
  if (failure?.status !== 503 || typeof failure.body !== 'string') return false
  try { return JSON.parse(failure.body)?.code === 'CAPABILITY_UNAVAILABLE' }
  catch { return false }
}
export default function RepairTrialPanel({ taskId, revision, enabled, open, onClose, onTrials }: {
  taskId: string; revision: RepairRevision; enabled: boolean; open: boolean; onClose: () => void; onTrials: (trials: TrialView[], configured: boolean) => void;
}) {
  const [nextOffset, setNextOffset] = useState<number | null>(null)
  const [records, setRecords] = useState<TrialView[]>([])
  const [options, setOptions] = useState<(RepairTrialOptions & { inputSchemaDigest: string; targets: RepairTrialTarget[] }) | null>(null)
  const [targetId, setTargetId] = useState('')
  const [loadError, setLoadError] = useState('')
  const [loadAttempt, setLoadAttempt] = useState(0)
  const initialized = useRef('')
  const [advanced, setAdvanced] = useState(false)
  const [entries, setEntries] = useState<TrialParameterEntry[]>([{ key: '', value: '' }])
  const [params, setParams] = useState<Record<string, unknown>>({})
  const [json, setJson] = useState('{}')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [submitted, setSubmitted] = useState(false)
  const [uncertain, setUncertain] = useState(false)
  const loadedOffset = useRef(0)
  const inFlight = useRef<string | null>(null)
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
    loadedOffset.current = 0; setBusy(false); setSubmitted(false); setNextOffset(null); setRecords([]); setOptions(null); setTargetId(''); setLoadError(''); setAdvanced(false); setEntries([{ key: '', value: '' }]); initialized.current = ''; setParams({}); setJson('{}'); setError(''); request.current = null; setUncertain(false)
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
    let active = true
    const mine = scope
    setLoadError('')
    void repairBatches.trialOptions(taskId, revision.revision).then(value => {
      if (!active || current.current !== mine) return
      const normalized = normalizeTrialInputDefinition(value.inputSchema == null ? undefined : { schema: value.inputSchema })
      const next = { ...value, inputSchema: normalized.inputSchema, inputDefinition: value.inputDefinition ?? normalized.inputDefinition }
      if (initialized.current !== mine) {
        const defaults = trialInputDefaults(next.inputSchema)
        setParams(defaults); setJson(JSON.stringify(defaults, null, 2)); initialized.current = mine
        setEntries(trialInputEntries(next.inputSchema, defaults))
      }
      setOptions(next)
    }).catch(reason => { if (active && current.current === mine) setLoadError(reason instanceof TrialInputDefinitionError ? reason.message : '无法读取候选参数和授权 Bot，请重试。') })
    return () => { active = false }
  }, [scope, open, enabled, loadAttempt])
  const definition = { inputSchema: options?.inputSchema ?? null, inputDefinition: options?.inputDefinition ?? 'missing' as const }
  const selectedTarget = options?.targets.find(bot => bot.id === targetId)
  // An unknown request only reconciles its frozen identity; it never selects a new Bot.
  const targetUnavailable = !uncertain && (!selectedTarget || selectedTarget.executionMode === 'unavailable' || selectedTarget.executionMode === 'saved')
  const locked = busy || uncertain || submitted
  function toggleAdvanced() {
    try {
      if (advanced) {
        const input = parseTrialJson(json)
        setParams(input); setEntries(trialInputEntries(definition.inputSchema, input))
      } else setJson(JSON.stringify(readTrialFormInput(definition, params, entries, false), null, 2))
      setAdvanced(!advanced); setError('')
    } catch (reason) { setError(reason instanceof SyntaxError ? '参数 JSON 格式无效，请检查对象、逗号和引号。' : (reason as Error).message) }
  }
  async function start() {
    if (!options || loadError || !targetId || !enabled || targetUnavailable || inFlight.current === scope || submitted && !uncertain) return
    const mine = scope
    inFlight.current = mine; setSubmitted(true); setBusy(true); setError('')
    try {
      request.current ??= { revision: revision.revision, expectedStateVersion: revision.stateVersion,
        requestId: crypto.randomUUID(), targetId, input: advanced ? parseTrialJson(json) : readTrialFormInput(definition, params, entries), inputSchemaDigest: options.inputSchemaDigest }
      await repairBatches.startTrial(taskId, request.current)
      if (current.current !== mine) return
      request.current = null; setUncertain(false); await refresh()
    } catch (reason) {
      if (current.current !== mine) return
      const failedBeforeDispatch = !request.current
      const status = (reason as { status?: number }).status
      if (!request.current || capabilityRejectedBeforeCreation(reason) || status && status >= 400 && status < 500) { request.current = null; setSubmitted(false) }
      setError(failedBeforeDispatch ? reason instanceof SyntaxError ? '参数 JSON 格式无效，请检查对象、逗号和引号。' : (reason as Error).message : repairReleaseError(reason)); setUncertain(!!request.current); await refresh()
    } finally { if (inFlight.current === mine) inFlight.current = null; if (current.current === mine) setBusy(false) }
  }
  const actions = <div className="flex flex-wrap gap-2">
    <button className={primary} disabled={!options || !!loadError || !targetId || !enabled || targetUnavailable || busy || submitted && !uncertain} onClick={() => void start()}>{busy ? '提交中…' : uncertain ? '核对并重试原请求' : submitted ? '试跑已提交' : '开始试跑'}</button>
    {submitted && !uncertain && records.some(row => row.revision === revision.revision && ['succeeded', 'failed'].includes(row.status)) && <button className={button} onClick={() => { setSubmitted(false); request.current = null }}>新建试跑</button>}
    <button className={button} onClick={onClose}>返回审阅</button>
  </div>
  const history = records.length > 0 && <details open={open}><summary className="cursor-pointer text-sm">试跑记录（{records.length}）</summary><div className="max-h-96 space-y-3 overflow-auto py-3">{records.map(row => <article key={row.id} className="rounded border border-input bg-background p-3 text-sm">
    <p className="font-medium">{row.mode === 'saved' ? '兼容执行：远端保存版' : `候选 v${row.revision}`} · {labels[row.status]}{row.mode !== 'saved' && row.candidateCommit !== revision.draft?.candidateCommit && <span className="ml-2 text-muted-foreground">该结果属于旧候选</span>}</p>
    {row.mode === 'saved' && <p className="text-muted-foreground">未固定候选、仅供参考；此结果不代表已验证候选 v{row.revision}。</p>}
    <p className="mt-1 text-muted-foreground">{row.target.label} · {new Date(row.createdAtMs).toLocaleString()} {row.durationMs != null && `· ${row.durationMs} ms`}</p>
    {row.error && <p className="mt-2 text-destructive">{row.error}</p>}
    {row.output && <pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap break-words">{row.output}</pre>}
    {row.flowId && <Link className="mt-2 inline-block text-primary underline" to={`/runs/${encodeURIComponent(row.flowId)}`}>运行详情、节点与日志</Link>}
    <details className="mt-2"><summary>参数（已脱敏）</summary><pre className="max-h-36 overflow-auto whitespace-pre-wrap">{JSON.stringify(row.input, null, 2)}</pre></details>
  </article>)}</div>{nextOffset !== null && <button className={button} onClick={() => void refresh(nextOffset)}>加载更早试跑</button>}</details>
  const readFailure = loadError && <div className="space-y-2"><p role="alert" className="text-sm text-destructive">{loadError}</p><button className={button} onClick={() => setLoadAttempt(value => value + 1)}>重新读取参数</button></div>
  if (!open && !error && !records.length) return null
  return <section aria-label="候选试跑" className="mt-4 space-y-3 border-t border-input pt-4">
    {open && <RepairDialog title={`先试跑候选 v${revision.revision}`} onClose={onClose} footer={actions}><div className="space-y-3">
      <p className="text-sm text-muted-foreground">这是真实执行，可能调用 MCP 或写入业务系统。不会自动发布，也不计入正式版本效果。</p>
      {readFailure}
      {!options ? loadError ? null
        : <p role="status">正在读取候选参数和授权生产 Bot…</p> : <>
        <label className="block text-sm">执行 Bot<select aria-label="执行 Bot" className="mt-1 block w-full min-w-0 rounded border border-input bg-background p-2" value={targetId} disabled={locked} onChange={event => { setTargetId(event.target.value); setError('') }}>
          <option value="">请选择生产 Bot</option>{options.targets.map(bot => <option key={bot.id} value={bot.id}>{bot.label}</option>)}
        </select></label>
        {!options.targets.length && <p>没有已授权且支持工作流运行的生产 Bot。</p>}
        {selectedTarget?.executionMode === 'saved' && <p className="text-sm text-destructive">此 Bot 无法安全固定当前候选，请升级 ClawMind 并重启 Bot 后重试。</p>}
        {selectedTarget?.executionMode === 'unavailable' && <p className="text-sm text-destructive">{selectedTarget.unavailableReason ?? '此 Bot 当前无法试跑，请选择其他 Bot。'}</p>}
        <RepairTrialInputs definition={definition} params={params} entries={entries} advanced={advanced} json={json} locked={locked} onParams={setParams} onEntries={setEntries} onJson={setJson} onToggle={toggleAdvanced} />
      </>}
      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
      {history}
    </div></RepairDialog>}
    {!open && <>{error && <p role="alert" className="text-sm text-destructive">{error}</p>}{history}</>}
  </section>
}
