import { useEffect, useRef, useState } from 'react'
import { repairBatches } from '../../../api/repair-batches'
import type { RepairDiff, RepairTaskDetail } from '../../../../server/contracts/repair-workbench'
import { button, primary, JsonDetails } from './repair-view'
import PackDiff from './PackDiff'
import RepairReleaseFlow from './RepairReleaseFlow'
import { candidateLabel, repairGitLinks } from './repair-release-view'

const message = (value: unknown) => value instanceof Error ? value.message : String(value)
const record = (value: unknown): Record<string, unknown> => value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
const records = (value: unknown) => Array.isArray(value) ? value.map(record) : []
const reportLabel = (value: unknown, fallback: string) => ({ changed: '已修改', not_needed: '无需修改', unresolved: '未解决', passed: '通过', failed: '失败', not_covered: '未覆盖', mock: 'Mock', none: '未覆盖', external_resources: '外部资源', pack_script_execution: 'Pack 脚本执行' }[String(value)] ?? (value == null ? fallback : String(value)))
const executionLabels: Record<NonNullable<RepairTaskDetail['execution']>['status'], string> = {
  created: '等待派发', dispatching: '正在派发；状态未确认时不要重复提交', dispatched: '已派发，等待生成结果',
  running: 'AIS 正在生成候选 Pack', dispatch_failed: 'AIS 拒绝派发，可修正配置后重试',
  dispatch_uncertain: '派发结果无法确认，请核对 AIS Job 后处理；不能直接重试以免重复生成',
  succeeded: '执行结果已返回', failed: '执行失败', cancelled: '本地任务已取消；远端停止需核对',
}

export default function RepairTaskPanel({ detail, canEdit, onFeedback, onCancel, onRetryDispatch, onRetryGeneration, onRefresh, onHistoryPageChange, dispatchError, busy }: {
  onHistoryPageChange?: (page: number) => void;
  detail: RepairTaskDetail; canEdit: boolean; onFeedback: () => void; onCancel: () => void; busy: boolean;
  onRetryGeneration: () => void; onRefresh: () => void; onRetryDispatch: () => void; dispatchError: string;
}) {
  const [selectedRevision, setSelectedRevision] = useState(detail.latestSuccessful?.revision ?? detail.latestAttempt.revision)
  const [base, setBase] = useState<'baseline' | 'parent'>('baseline')
  const [diff, setDiff] = useState<RepairDiff | null>(null)
  const [diffError, setDiffError] = useState('')
  const [diffLoading, setDiffLoading] = useState(false)
  const [showDiff, setShowDiff] = useState(true)
  const [filePath, setFilePath] = useState('')
  const [tab, setTab] = useState<'diff' | 'checks' | 'sources'>('diff')
  const [retry, setRetry] = useState(0)
  const diffCache = useRef(new Map<string, RepairDiff>())
  const revision = detail.revisions.find(item => item.revision === selectedRevision) ?? (detail.latestSuccessful?.revision === selectedRevision ? detail.latestSuccessful : detail.latestAttempt)
  const candidate = typeof revision.draft?.candidateCommit === 'string' ? revision.draft.candidateCommit : null
  const gitLinks = repairGitLinks(detail.release?.repositoryWebUrl, revision.input.baseline.packCommit, candidate)
  const checks = revision.checks
  const outcomes = records(revision.draft?.itemResults)
  const coverage = records(checks?.coverage)
  useEffect(() => {
    if (tab !== 'diff' || !showDiff || !candidate || !detail.capabilities.diff) return
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
  }, [detail.taskId, revision.revision, candidate, detail.capabilities.diff, base, showDiff, retry, tab])

  const failed = detail.latestAttempt.phase === 'failed'
  const selectedFailure = revision.phase === 'failed'
  const failureText = (code: unknown) => code === 'AIS_FAILED' ? 'AIS 任务执行或提交失败；请先核对原执行，再决定恢复方式。'
    : code === 'GIT_RESULT_UNAVAILABLE' ? '候选结果尚未读取成功；保留原请求，先核对原结果。' : '此版本生成或检查未完成，请查看本版本原始错误。'
  const awaitingResult = failed && detail.latestAttempt.error?.code === 'GIT_RESULT_UNAVAILABLE'
  const alreadyPublished = detail.revisions.some(row => row.phase === 'published')
    || !!detail.release?.deployments.some(row => row.status === 'succeeded')
  const selectedDeployment = detail.release?.deployments.find(row => row.revision === revision.revision && row.status === 'succeeded')
  const automatic = !!detail.latestAttempt.input.automation?.roundId
  const canGenerate = canEdit && detail.capabilities.generation && !busy && !alreadyPublished
  return <div className="grid items-start gap-6 xl:grid-cols-[minmax(0,1fr)_19rem] pb-20">
    <section aria-label="修复任务详情" className="min-w-0 space-y-4">
      <section className={`rounded-lg border bg-card p-5 ${failed ? 'border-destructive/30' : 'border-input'}`}>
        <p className="mb-2 text-xs text-muted-foreground">{detail.latestAttempt.input.automation ? '自动生成草稿 · 等待人工审阅' : '手动优化任务'}</p>
        <p className="text-sm font-semibold text-foreground">最近尝试：v{detail.latestAttempt.revision} · {candidateLabel(detail, detail.latestAttempt)}</p>
        {failed && <>
          <p className="mt-2 text-xs leading-5 text-destructive">{awaitingResult ? 'AIS 已完成，但候选结果尚未读取成功。先重新读取原结果，不重复派发。' : detail.latestSuccessful ? `本轮生成失败，已保留 v${detail.latestSuccessful.revision} 候选。旧审核不自动恢复。` : '首次生成失败，尚无候选。可以沿用本轮范围与要求重新生成，无需取消任务。'}</p>
          <p className="mt-2 text-xs text-muted-foreground">{failureText(detail.latestAttempt.error?.code)}</p>
          <div className="mt-3 flex flex-wrap gap-3">
            <button className={primary} disabled={!canGenerate} onClick={onRetryGeneration}>{detail.latestAttempt.error?.code === 'GIT_RESULT_UNAVAILABLE' ? '重新读取候选' : '重新生成 Pack'}</button>
            <button className={button} disabled={!canGenerate || awaitingResult || automatic} onClick={onFeedback}>调整范围与要求</button>
            {detail.latestSuccessful && <button className={button} onClick={() => { setSelectedRevision(detail.latestSuccessful!.revision); setTab('diff'); setShowDiff(true) }}>返回上一成功候选审核</button>}
          </div>
          <p className="mt-2 text-xs text-muted-foreground">{awaitingResult ? '重新读取沿用原 Job 和当前轮次。' : '先核对原执行与 Git 结果；有有效候选则恢复原轮次，没有原结果才沿用范围和要求生成新一轮。'}</p>
        </>}
        {detail.execution && <div className="mt-3 border-t border-border/50 pt-3 text-xs leading-5 text-muted-foreground">
          <p className="font-medium">最近 v{detail.latestAttempt.revision} 的执行记录{revision.revision !== detail.latestAttempt.revision ? `（不代表所选 v${revision.revision} 的执行结果）` : ''}</p>
          <p>{executionLabels[detail.execution.status]}</p>
          <details className="mt-1"><summary className="cursor-pointer">执行记录</summary><p>AIS Job：{detail.execution.jobId ?? '尚未确认'} · 第 {detail.execution.attempt} 次</p><p>平台状态：{detail.execution.rawStatus ?? '尚未确认'}</p><p>{detail.execution.errorCode}</p></details>
          {detail.latestAttempt.phase === 'drafting' && ['created', 'dispatch_failed'].includes(detail.execution.status) && <button className={`${button} mt-2`} disabled={!canGenerate} onClick={onRetryDispatch}>重试派发</button>}
          {detail.execution.status === 'dispatch_uncertain' && <button className={`${button} mt-2`} disabled={busy} onClick={onRefresh}>核对任务状态</button>}
        </div>}
        {dispatchError && <p role="alert" className="mt-3 text-xs text-destructive">{dispatchError}</p>}
        {!canEdit && <p className="mt-2 text-xs text-warning-foreground">当前只有查看权限。</p>}
        {!detail.capabilities.generation && <p className="mt-2 text-xs text-warning-foreground">生成服务尚未接入，仍可查看候选。</p>}
        <JsonDetails title={failed ? '查看本次失败原因' : '之前执行或读取错误'} value={detail.latestAttempt.error} />
      </section>
      <section id={`repair-scope-${detail.taskId}`} className="scroll-mt-6 rounded-lg border border-input bg-card p-5">
        <h3 className="text-base font-semibold">本轮冻结范围</h3>
        {detail.automaticProgress && <p className="mt-2 text-sm">本轮已纳入 {detail.scopeItemCount ?? revision.input.items.length} 项 · 核对进度 {detail.automaticProgress.completedBatches}/{detail.automaticProgress.totalBatches}</p>}
        <p className="mt-2 text-xs text-muted-foreground">候选 v{revision.revision} · {revision.input.items.length} 项{detail.automaticProgress ? "（此批）" : ""} · 任务基线部署 #{revision.input.baseline.activeDeployNumber ?? '未知'}。查看不会修改本轮范围。</p>
        <ul className="mt-3 space-y-2 text-sm">{revision.input.items.map(item => <li key={item.itemId}>{typeof item.proposal?.summary === 'string' ? item.proposal.summary : item.instruction || '本轮优化项'}</li>)}</ul>
        {canGenerate && !failed && !automatic && <button type="button" className={`${button} mt-3`} onClick={onFeedback}>调整范围与要求</button>}
      </section>
      <section id={`repair-review-${detail.taskId}`} className="scroll-mt-6 overflow-hidden rounded-lg border border-input bg-card p-5">
        <div className="flex flex-wrap items-center justify-between gap-3"><h3 className="text-base font-semibold">候选 Pack · v{revision.revision}</h3>
          <label className="text-xs text-muted-foreground">查看版本 <select aria-label="查看修复版本" className="ml-2 rounded border border-input bg-background text-foreground p-2" value={revision.revision}
            onChange={event => { setSelectedRevision(Number(event.target.value)); setShowDiff(true); setDiff(null); setDiffError(''); setBase('baseline') }}>
            {[...new Map([detail.latestAttempt, ...(detail.latestSuccessful ? [detail.latestSuccessful] : []), ...detail.revisions].map(item => [item.revision, item])).values()].map(item => <option key={item.revision} value={item.revision}>v{item.revision} · {candidateLabel(detail, item)}</option>)}
          </select></label>
        </div>
        {selectedFailure && <div role="status" className="mt-3 rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-sm">
          <p className="font-semibold text-destructive">所选版本 v{revision.revision}：生成失败</p>
          <p className="mt-2">{failureText(revision.error?.code)}</p>
          <JsonDetails title="所选版本原始错误" value={revision.error} />
          {revision.revision !== detail.latestAttempt.revision && <p className="mt-2 text-xs text-muted-foreground">此历史版本只读保留；后续版本成功不会改写本次失败。</p>}
        </div>}
        {revision.phase === 'no_change' && <div role="status" className="mt-3 rounded-lg bg-muted p-3 text-sm">
          <p className="font-semibold">所选版本 v{revision.revision}：无需变更</p>
          <p className="mt-2">{detail.automaticProgress?.publishable ? "此批无需新增修改；完整候选保留本轮前序修改，请审阅完整差异后决定是否发布。" : "本轮无需重新发布；已有生效版本及其观察记录继续保留。具体依据以本版本逐项结果为准。"}</p>
        </div>}
        <div className="mt-4 flex flex-wrap items-center gap-x-5 gap-y-2 rounded-lg bg-muted/40 px-4 py-3 text-sm">
          <span className="font-medium">v{revision.revision} · {candidateLabel(detail, revision)}</span>
          <span className="text-muted-foreground">基于部署 #{revision.input.baseline.activeDeployNumber ?? '未知'}</span>
          {gitLinks?.candidate && <a className="text-primary hover:underline" href={gitLinks.candidate} target="_blank" rel="noopener noreferrer">查看 Git 提交 ↗</a>}
          {gitLinks?.compare && <a className="text-primary hover:underline" href={gitLinks.compare} target="_blank" rel="noopener noreferrer">在 Git 查看完整变更 ↗</a>}
        </div>
        <details className="mt-3 text-xs text-muted-foreground"><summary className="cursor-pointer">版本技术信息</summary>
          <dl className="mt-2 space-y-2 rounded-lg bg-muted/40 p-3"><dt>基线 commit</dt><dd className="break-all font-mono">{revision.input.baseline.packCommit}</dd>
            <dt>候选 commit</dt><dd className="break-all font-mono">{candidate ?? '尚未生成'}</dd><dt>任务编号</dt><dd className="break-all font-mono">{detail.taskId}</dd></dl></details>
        {typeof revision.draft?.summary === 'string' && <p className="mt-3 text-sm leading-6">{revision.draft.summary}</p>}
        <nav aria-label="候选审核内容" className="mt-4 flex gap-5 border-b border-input">
          {([['diff', 'Pack 文件 Diff'], ['checks', '检查报告'], ['sources', '逐项结果']] as const).map(([key, label]) => <button key={key} aria-pressed={tab === key} onClick={() => setTab(key)} className={`border-b-2 py-3 text-xs font-medium ${tab === key ? 'border-primary text-primary' : 'border-transparent text-muted-foreground'}`}>{label}</button>)}
        </nav>
    {revision.input.instructions && <p className="mt-3 whitespace-pre-wrap text-xs text-muted-foreground">修复说明：{revision.input.instructions}</p>}
    {revision.input.feedback && <p className="mt-2 whitespace-pre-wrap text-xs text-muted-foreground">本版反馈：{revision.input.feedback}</p>}
    {!candidate && <p className="mt-4 text-xs text-muted-foreground">此版本暂无候选稿。</p>}

    {tab === 'sources' && <div className="mt-4 space-y-2"><h4 className="text-xs font-semibold">逐项修复结果</h4>{revision.input.items.map(item => {
      const outcome = outcomes.find(outcome => outcome.itemId === item.itemId)
      const covered = coverage.find(row => row.itemId === item.itemId)
      return <div className="rounded-lg border border-input p-3 text-xs" key={item.itemId}>
        <p className="font-medium">{typeof item.proposal?.summary === 'string' ? item.proposal.summary : item.instruction || item.itemId}</p>
        <p className="mt-1 text-muted-foreground">处理结论：{outcome ? reportLabel(outcome.status, '未提供状态') : '尚未提供逐项结论'}</p>
        {outcome?.reason != null && <p className="mt-1 text-muted-foreground">{String(outcome.reason)}</p>}
        <p className="mt-1 text-muted-foreground">验证覆盖：{covered ? `${reportLabel(covered.mode, '未覆盖')} · ${String(covered.assertionCount ?? 0)} 条断言` : '未覆盖'}</p>
      </div>
    })}</div>}
    {tab === 'checks' && <div className="mt-4 rounded-lg bg-muted/50 p-3 text-xs">
      <h4 className="font-semibold">候选检查</h4>
      <p className="mt-2">静态检查：{reportLabel(record(checks?.static).status, '尚未执行')} · Mock 检查：{reportLabel(record(checks?.mock).status, '尚未执行')}</p>
      {Array.isArray(record(checks?.static).issues) && <ul className="mt-2 list-inside list-disc text-destructive">{(record(checks?.static).issues as unknown[]).map((issue, index) => <li key={index}>{String(issue)}</li>)}</ul>}
      <p className="mt-2 text-warning-foreground">未覆盖：{Array.isArray(checks?.uncoveredChecks) ? checks.uncoveredChecks.map(value => reportLabel(value, '未知')).join('、') || '报告未声明' : '尚未提供检查报告'}。Mock 通过不代表线上资源或脚本执行已验证。</p>
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
      </section>
      <div id={`repair-release-${detail.taskId}`} className="scroll-mt-4"><RepairReleaseFlow detail={detail} selectedRevision={revision.revision} canEdit={canEdit} onRefresh={onRefresh} /></div>
      {!automatic && !alreadyPublished && ['review', 'blocked'].includes(detail.latestAttempt.phase) && <section className="rounded-lg border border-input bg-card p-5">
        <h3 className="text-sm font-semibold">不满意？把修改要求交给下一轮 AIS</h3><p className="mt-2 text-xs leading-5 text-muted-foreground">基于上一成功 Pack，保留原始问题与证据；调整范围和反馈后生成新候选。</p>
        <button className={`${primary} mt-3`} disabled={!canGenerate || revision.revision !== detail.latestSuccessful?.revision} onClick={onFeedback}>反馈并生成下一版</button>
      </section>}
    </section>
    <aside className="space-y-4 xl:sticky xl:top-4">
      <section className="rounded-lg border border-input bg-card p-5"><h3 className="text-sm font-semibold">候选版本</h3>
        <p className="mt-2 text-xs leading-5 text-muted-foreground">每轮生成与部署记录独立保留。选择版本查看变更，只有最新可用候选可以部署。</p>
        <div className="mt-4 divide-y divide-border/50">{detail.revisions.slice().sort((a, b) => b.revision - a.revision).map(item => <button key={item.revision} onClick={() => { setSelectedRevision(item.revision); setDiff(null); setShowDiff(true) }} className="block w-full py-3 text-left">
          <p className={`text-sm font-medium ${item.revision === revision.revision ? 'text-primary' : 'text-foreground'}`}>v{item.revision} · {candidateLabel(detail, item)}</p>
          <p className="mt-1 text-xs text-muted-foreground">{item.revision === revision.revision ? '当前查看' : '查看此版本'}</p>
        </button>)}</div>
        {detail.revisionPage && detail.revisionPage.totalPages > 1 && <div className="mt-3 space-y-2 text-xs text-muted-foreground">
          <p>历史记录第 {detail.revisionPage.page}/{detail.revisionPage.totalPages} 页 · 共 {detail.revisionPage.total} 次</p>
          <div className="flex justify-between"><button className={button} disabled={busy || detail.revisionPage.page <= 1} onClick={() => onHistoryPageChange?.(detail.revisionPage!.page - 1)}>较新记录</button>
            <button className={button} disabled={busy || detail.revisionPage.page >= detail.revisionPage.totalPages} onClick={() => onHistoryPageChange?.(detail.revisionPage!.page + 1)}>较早记录</button></div>
        </div>}
      </section>
      <section className="rounded-lg border border-input bg-card p-5"><h3 className="text-xs font-semibold">所选版本与发布状态</h3><p className="mt-2 text-xs leading-5 text-muted-foreground">{selectedDeployment ? `候选 v${revision.revision} 已发布为部署 #${selectedDeployment.receipt?.deployNumber ?? '未知'}；当前生效与观察版本以服务端证据为准。` : '所选候选尚无成功发布回执；仅查看范围、Diff 和检查不会应用修改。'}系统依据生效版本、相关节点和明确规则自动观察；证据不足时不判成功，也不自动回滚。</p></section>
      {!alreadyPublished && ['drafting', 'review', 'blocked', 'failed'].includes(detail.latestAttempt.phase) && <button className="text-xs text-muted-foreground hover:text-destructive" disabled={!canEdit || busy} onClick={onCancel}>取消修复任务</button>}
    </aside>
  </div>
}
