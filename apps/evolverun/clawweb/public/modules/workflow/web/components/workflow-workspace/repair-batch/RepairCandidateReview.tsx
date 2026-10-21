import { useEffect, useRef, useState } from 'react'
import { repairBatches } from '../../../api/repair-batches'
import type { RepairRevision } from '../../../../server/contracts/repair-batch'
import type { RepairDiff, RepairTaskDetail } from '../../../../server/contracts/repair-workbench'
import { button, primary, JsonDetails } from './repair-view'
import PackDiff from './PackDiff'
import { candidateLabel, repairGitLinks } from './repair-release-view'

const message = (value: unknown) => value instanceof Error ? value.message : String(value)
const record = (value: unknown): Record<string, unknown> => value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
const records = (value: unknown) => Array.isArray(value) ? value.map(record) : []
const reportLabel = (value: unknown, fallback: string) => ({ changed: '已生成修改', not_needed: '未采纳', unresolved: '待确认', passed: '通过', failed: '失败', not_covered: '未覆盖', mock: 'Mock', none: '未覆盖', external_resources: '外部资源', pack_script_execution: 'Pack 脚本执行' }[String(value)] ?? (value == null ? fallback : String(value)))
function coverageLabel(covered: Record<string, unknown> | undefined, checks: RepairRevision['checks']) {
  if (!covered || ['none', 'not_covered'].includes(String(covered.mode))) return '未覆盖'
  const mock = record(checks?.mock)
  if (covered.mode !== 'mock' || !['passed', 'failed'].includes(String(mock.status))
    || mock.caseCount === 0 || mock.assertionCount === 0
    || Array.isArray(checks?.cases) && !checks.cases.length
    || !(Number(covered.assertionCount) > 0) || !Array.isArray(covered.caseNames) || !covered.caseNames.length) return '未验证'
  // Historical coverage records do not establish a link to executed case assertions.
  return '未验证（仅有覆盖记录）'
}

export default function RepairCandidateReview({detail,revision,active}:{detail:RepairTaskDetail;revision:RepairRevision;active:boolean}) {
  const [base, setBase] = useState<'baseline' | 'parent'>('baseline')
  const [diff, setDiff] = useState<RepairDiff | null>(null)
  const [diffError, setDiffError] = useState('')
  const [diffLoading, setDiffLoading] = useState(false)
  const [showDiff, setShowDiff] = useState(true)
  const [filePath, setFilePath] = useState('')
  const [tab, setTab] = useState<'diff' | 'checks'>('diff')
  const [retry, setRetry] = useState(0)
  const diffCache = useRef(new Map<string, RepairDiff>())
  const candidate = typeof revision.draft?.candidateCommit === 'string' ? revision.draft.candidateCommit : null
  const gitLinks = repairGitLinks(detail.release?.repositoryWebUrl, revision.input.baseline.packCommit, candidate)
  const checks = revision.checks
  const outcomes = records(revision.draft?.itemResults)
  const coverage = records(checks?.coverage)
  useEffect(() => {
    if (!active || tab !== 'diff' || !showDiff || !candidate || !detail.capabilities.diff) return
    const key = JSON.stringify([detail.taskId, revision.revision, candidate, base, retry])
    const cached = diffCache.current.get(key)
    if (cached) { setDiff(cached); setDiffError(''); setDiffLoading(false); return }
    let current = true; setDiffLoading(true); setDiffError(''); setDiff(null)
    repairBatches.diff(detail.taskId, revision.revision, base).then(value => { if (current) {
      if (diffCache.current.size >= 10) diffCache.current.delete(diffCache.current.keys().next().value!)
      diffCache.current.set(key, value); setDiff(value)
    } })
      .catch(error => { if (current) setDiffError(message(error)) }).finally(() => { if (current) setDiffLoading(false) })
    return () => { current = false }
  }, [detail.taskId, revision.revision, candidate, detail.capabilities.diff, base, showDiff, retry, tab, active])

  const renderOutcome = (item: typeof revision.input.items[number]) => {
      const outcome = outcomes.find(outcome => outcome.itemId === item.itemId)
      const covered = coverage.find(row => row.itemId === item.itemId)
      return <div className="rounded-lg border border-input p-3 text-xs" key={item.itemId}>
        <p className="font-medium">{typeof item.proposal?.summary === 'string' ? item.proposal.summary : item.instruction.startsWith('当前生效 Pack 已变化') ? '重新评估原修改的适用性' : item.instruction || item.itemId}</p>
        <p className="mt-1 text-muted-foreground">处理结论：{outcome ? reportLabel(outcome.status, '未提供状态') : '尚未提供逐项结论'}</p>
        {outcome?.reason != null && <p className="mt-1 text-muted-foreground">{String(outcome.reason)}</p>}
        <p className="mt-1 text-muted-foreground">验证覆盖：{coverageLabel(covered, checks)}</p>
      </div>
  }
  const selectedFailure = revision.phase === 'failed'
  const failureText = (code:unknown) => code === 'AIS_FAILED' ? 'AIS 任务执行或提交失败；请先核对原执行，再决定恢复方式。' : code === 'GIT_RESULT_UNAVAILABLE' ? '候选结果尚未读取成功；保留原请求，先核对原结果。' : '此版本生成或检查未完成，请查看本版本原始错误。'
  return (
      <section id={`repair-review-${detail.taskId}`} className="scroll-mt-6 overflow-hidden rounded-lg border border-input bg-card p-5">
        <h3 className="text-base font-semibold">本版改动</h3>
        {selectedFailure && <div role="status" className="mt-3 rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-sm">
          <p className="font-semibold text-destructive">所选版本 v{revision.revision}：生成失败</p>
          <p className="mt-2">{failureText(revision.error?.code)}</p>
          <JsonDetails title="所选版本原始错误" value={revision.error} />
          {revision.revision !== detail.latestAttempt.revision && <p className="mt-2 text-xs text-muted-foreground">此历史版本只读保留；后续版本成功不会改写本次失败。</p>}
        </div>}
        {revision.phase === 'no_change' && <div role="status" className="mt-3 rounded-lg bg-muted p-3 text-sm">
          <p className="font-semibold">所选版本 v{revision.revision}：无需变更</p>
          <p className="mt-2">{detail.automaticProgress && detail.automaticProgress.completedBatches < detail.automaticProgress.totalBatches ? '本批无需新增修改，整轮仍在核对。' : detail.automaticProgress?.publishable ? "此批无需新增修改；完整候选保留本轮前序修改，请审阅完整差异后决定是否发布。" : "本轮无需重新发布；已有生效版本及其观察记录继续保留。具体依据以本版本逐项结果为准。"}</p>
        </div>}
        <p className="mt-2 text-xs text-muted-foreground">基于部署 #{revision.input.baseline.activeDeployNumber ?? '未知'} · 修改尚不代表业务效果已验证。</p>
    {outcomes.length > 0 && <div className="mt-4 space-y-2"><h4 className="text-xs font-semibold">目标处理结果</h4>
      {revision.input.items.slice(0,3).map(renderOutcome)}
      {revision.input.items.length>3&&<details className="text-xs text-muted-foreground"><summary>更多目标处理结果（{revision.input.items.length-3} 项）</summary><div className="mt-2 space-y-2">{revision.input.items.slice(3).map(renderOutcome)}</div></details>}
    </div>}
        <div aria-label="候选检查摘要" className="mt-4 space-y-2 text-sm">
          <p className="font-medium">{outcomes.length ? `已生成修改 ${outcomes.filter(row=>row.status==='changed').length} 项 · 未采纳 ${outcomes.filter(row=>row.status==='not_needed').length} 项 · 待确认 ${outcomes.filter(row=>row.status==='unresolved').length} 项` : '尚未返回逐项处理结果'}</p>
          {revision.input.items.length>outcomes.length&&<p className="text-warning-foreground">缺少 {revision.input.items.length-outcomes.length} 项处理结果，请先核对候选。</p>}
          <p>静态检查：{reportLabel(record(checks?.static).status,'尚未执行')} · Mock 检查：{reportLabel(record(checks?.mock).status,'尚未执行')}</p>
          <p className="text-xs text-muted-foreground">{Array.isArray(checks?.uncoveredChecks)&&checks.uncoveredChecks.length ? `尚未验证：${checks.uncoveredChecks.map(value=>reportLabel(value,'未知')).join('、')}` : !checks ? '尚未提供检查覆盖情况。' : '业务效果仍需发布后观察。'} · Mock 结果不代表业务效果。</p>
        </div>
        {typeof revision.draft?.summary === 'string' && !/^workflow repair candidate:/i.test(revision.draft.summary) && <p className="mt-3 text-sm">{revision.draft.summary}</p>}
        {typeof revision.draft?.summary === 'string' && /^workflow repair candidate:/i.test(revision.draft.summary) && <details className="mt-3 text-xs text-muted-foreground"><summary>生成记录原文</summary><p className="mt-2">{revision.draft.summary}</p></details>}
        <nav aria-label="候选审核内容" className="mt-4 flex gap-5 border-b border-input">
          {([['diff', '文件差异'], ['checks', '检查详情']] as const).map(([key, label]) => <button key={key} aria-pressed={tab === key} onClick={() => setTab(key)} className={`border-b-2 py-3 text-xs font-medium ${tab === key ? 'border-primary text-primary' : 'border-transparent text-muted-foreground'}`}>{label}</button>)}
        </nav>
    {!candidate && <p className="mt-4 text-xs text-muted-foreground">此版本暂无候选稿。</p>}

    {tab === 'checks' && <div className="mt-4 rounded-lg bg-muted/50 p-3 text-xs">
      <h4 className="font-semibold">候选检查</h4>
      {Array.isArray(record(checks?.static).issues) && <ul className="mt-2 list-inside list-disc text-destructive">{(record(checks?.static).issues as unknown[]).map((issue, index) => <li key={index}>{String(issue)}</li>)}</ul>}
      <JsonDetails title="完整检查报告" value={checks} /><JsonDetails title="完整候选报告" value={revision.draft} /><JsonDetails title="此版本错误" value={revision.error} />
    </div>}
    {tab === 'diff' && candidate && <div className="mt-4">
      <div className="flex flex-wrap items-center gap-2"><button type="button" className={button} disabled={!detail.capabilities.diff} onClick={() => setShowDiff(value => !value)}>{showDiff ? '收起差异' : '查看差异'}</button>
        <label className="text-xs">比较基准 <select aria-label="比较基准" className="rounded border border-input bg-background text-foreground p-2" value={base} onChange={event => { setDiff(null); setDiffError(''); setBase(event.target.value as typeof base) }}>
          <option value="baseline">任务基线</option><option value="parent" disabled={!revision.input.parentCandidateCommit}>上一成功候选</option>
        </select></label></div>
      {!detail.capabilities.diff && <p className="mt-2 text-xs text-muted-foreground">当前未提供差异读取能力。</p>}
      {showDiff && diffLoading && <p role="status" className="mt-2 text-xs">加载候选差异…</p>}
      {showDiff && diffError && <p role="alert" className="mt-2 text-xs text-destructive">差异加载失败：{diffError} <button className={button} onClick={() => setRetry(value => value + 1)}>重试差异</button></p>}
      {showDiff && diff && <div className="mt-3 space-y-3">
        {diff.truncated && <p className="text-xs text-warning-foreground">差异已截断，仅展示部分内容。</p>}
        {!diff.files.length && <p className="text-xs text-muted-foreground">此基准下没有文件差异。</p>}
        <div className="grid overflow-hidden rounded border border-input bg-background text-foreground md:grid-cols-[11rem_minmax(0,1fr)]"><nav aria-label="候选变更文件" className="border-r border-input bg-muted/50 p-2">{diff.files.map(file => <button key={file.path} onClick={() => setFilePath(file.path)} className={`block w-full break-all rounded p-2 text-left font-mono text-[10px] ${file.path === filePath || !diff.files.some(f => f.path === filePath) && file === diff.files[0] ? 'bg-primary/10 text-primary' : 'text-muted-foreground'}`}>{file.status} · {file.path}</button>)}</nav><div className="min-w-0">{diff.files.filter(file => file.path === (diff.files.some(f => f.path === filePath) ? filePath : diff.files[0]?.path)).map(file => <details key={file.path} open className="overflow-hidden rounded-lg border border-input">
          <summary className="cursor-pointer break-all bg-muted/50 px-3 py-2 text-xs">{file.status} · {file.path}</summary>
          <PackDiff file={file} />
        </details>)}</div></div>
      </div>}
    </div>}
        <details className="mt-4 border-t border-input pt-3 text-xs text-muted-foreground"><summary>版本与 Git 记录</summary><div className="mt-2 flex flex-wrap items-center gap-x-5 gap-y-2">
          <span className="font-medium">v{revision.revision} · {candidateLabel(detail, revision)}</span>
          <span className="text-muted-foreground">基于部署 #{revision.input.baseline.activeDeployNumber ?? '未知'}</span>
          {gitLinks?.candidate && <a className="text-primary hover:underline" href={gitLinks.candidate} target="_blank" rel="noopener noreferrer">查看 Git 提交 ↗</a>}
          {gitLinks?.compare && <a className="text-primary hover:underline" href={gitLinks.compare} target="_blank" rel="noopener noreferrer">在 Git 查看完整变更 ↗</a>}
        </div>

          <dl className="mt-2 space-y-2 rounded-lg bg-muted/40 p-3"><dt>基线 commit</dt><dd className="break-all font-mono">{revision.input.baseline.packCommit}</dd>
            <dt>候选 commit</dt><dd className="break-all font-mono">{candidate ?? '尚未生成'}</dd><dt>任务编号</dt><dd className="break-all font-mono">{detail.taskId}</dd></dl></details>
      </section>
  )
}
