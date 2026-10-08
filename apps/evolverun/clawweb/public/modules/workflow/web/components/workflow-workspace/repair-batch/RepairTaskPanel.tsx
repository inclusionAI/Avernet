import { useEffect, useState } from 'react'
import { repairBatches } from '../../../api/repair-batches'
import type { RepairDiff, RepairTaskDetail } from '../../../../server/contracts/repair-workbench'
import { button, JsonDetails, phases } from './repair-view'

const message = (value: unknown) => value instanceof Error ? value.message : String(value)
const record = (value: unknown): Record<string, unknown> => value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
const records = (value: unknown) => Array.isArray(value) ? value.map(record) : []
const reportLabel = (value: unknown, fallback: string) => ({ changed: '已修改', not_needed: '无需修改', unresolved: '未解决', passed: '通过', failed: '失败', not_covered: '未覆盖', mock: 'Mock', none: '未覆盖', external_resources: '外部资源', pack_script_execution: 'Pack 脚本执行' }[String(value)] ?? (value == null ? fallback : String(value)))
const executionLabels: Record<NonNullable<RepairTaskDetail['execution']>['status'], string> = {
  created: '等待派发', dispatching: '正在派发；状态未确认时不要重复提交', dispatched: '已派发，等待生成结果',
  running: 'AIS 正在生成候选 Pack', dispatch_failed: '派发未确认', succeeded: '执行结果已返回', failed: '执行失败', cancelled: '执行已取消',
}

export default function RepairTaskPanel({ detail, canEdit, onFeedback, onCancel, onRetryDispatch, dispatchError, busy }: {
  detail: RepairTaskDetail; canEdit: boolean; onFeedback: () => void; onCancel: () => void; busy: boolean;
  onRetryDispatch: () => void; dispatchError: string;
}) {
  const [selectedRevision, setSelectedRevision] = useState(detail.latestSuccessful?.revision ?? detail.latestAttempt.revision)
  const [base, setBase] = useState<'baseline' | 'parent'>('baseline')
  const [diff, setDiff] = useState<RepairDiff | null>(null)
  const [diffError, setDiffError] = useState('')
  const [diffLoading, setDiffLoading] = useState(false)
  const [showDiff, setShowDiff] = useState(false)
  const [retry, setRetry] = useState(0)
  const revision = detail.revisions.find(item => item.revision === selectedRevision) ?? detail.latestAttempt
  const candidate = typeof revision.draft?.candidateCommit === 'string' ? revision.draft.candidateCommit : null
  const checks = revision.checks
  const outcomes = records(revision.draft?.itemResults)
  const coverage = records(checks?.coverage)
  useEffect(() => {
    if (!showDiff || !candidate || !detail.capabilities.diff) return
    let current = true; setDiffLoading(true); setDiffError(''); setDiff(null)
    repairBatches.diff(detail.taskId, revision.revision, base).then(value => { if (current) setDiff(value) })
      .catch(error => { if (current) setDiffError(message(error)) }).finally(() => { if (current) setDiffLoading(false) })
    return () => { current = false }
  }, [detail.taskId, revision.revision, candidate, detail.capabilities.diff, base, showDiff, retry])

  return <section aria-label="修复任务详情" className="mt-4 rounded-xl border border-blue-200 bg-white p-4">
    <h3 className="break-all text-sm font-semibold">修复任务 {detail.taskId}</h3>
    <div className="mt-3 grid gap-2 sm:grid-cols-2">
      <div className={`rounded-lg p-3 text-xs ${detail.latestAttempt.phase === 'failed' ? 'bg-red-50 text-red-700' : 'bg-slate-50 text-slate-700'}`}>
        最近尝试：v{detail.latestAttempt.revision} · {phases[detail.latestAttempt.phase] ?? detail.latestAttempt.phase}
        <JsonDetails title="查看本次失败原因" value={detail.latestAttempt.error} />
      </div>
      <p className="rounded-lg bg-blue-50 p-3 text-xs text-blue-700">{detail.latestSuccessful ? `最近成功候选：v${detail.latestSuccessful.revision}` : '尚无成功候选稿'}</p>
    </div>
    {detail.execution && <div className={`mt-3 rounded-lg border p-3 text-xs ${detail.execution.status === 'dispatch_failed' ? 'border-amber-200 bg-amber-50 text-amber-800' : 'border-slate-200 bg-slate-50 text-slate-600'}`}>
      <p className="mb-1 font-medium">执行与派发状态 · v{detail.latestAttempt.revision}</p>
      <p>{executionLabels[detail.execution.status]}</p>
      {detail.execution.jobId && <p className="mt-1 break-all text-slate-500">AIS Job：{detail.execution.jobId} · 第 {detail.execution.attempt} 次</p>}
      {detail.execution.rawStatus && <p className="mt-1 text-slate-500">平台状态：{detail.execution.rawStatus}</p>}
      {detail.execution.errorCode && <code className="mt-1 block break-all">{detail.execution.errorCode}</code>}
      {detail.latestAttempt.phase === 'drafting' && ['created', 'dispatch_failed'].includes(detail.execution.status) && <>
        <p className="mt-2">重试沿用本版冻结输入，不会创建新版本。</p>
        <button type="button" className={`${button} mt-2`} disabled={!canEdit || !detail.capabilities.generation || busy} onClick={onRetryDispatch}>重试派发</button>
      </>}
      {dispatchError && <p role="alert" className="mt-2 text-red-600">派发重试未确认：{dispatchError}</p>}
    </div>}
    <p className="mt-3 text-xs leading-5 text-slate-500">生成结果仅供审阅，尚未应用到工作流或部署。当前不提供发布能力。</p>
    <div className="mt-3 flex flex-wrap items-center gap-2">
      <label className="text-xs">查看版本 <select aria-label="查看修复版本" className="ml-1 rounded border border-slate-200 p-2" value={revision.revision}
        onChange={event => { setSelectedRevision(Number(event.target.value)); setShowDiff(false); setDiff(null); setDiffError(''); setBase('baseline') }}>
        {detail.revisions.map(item => <option key={item.revision} value={item.revision}>v{item.revision} · {phases[item.phase] ?? item.phase}</option>)}
      </select></label>
      <button type="button" className={button} disabled={!canEdit || !detail.capabilities.generation || busy || !['review', 'blocked', 'failed'].includes(detail.latestAttempt.phase)} onClick={onFeedback}>反馈并生成下一版</button>
      {['drafting', 'review', 'blocked', 'failed'].includes(detail.latestAttempt.phase) && <button type="button" className={button} disabled={!canEdit || busy} onClick={onCancel}>取消修复任务</button>}
    </div>
    {revision.input.instructions && <p className="mt-3 whitespace-pre-wrap text-xs text-slate-600">修复说明：{revision.input.instructions}</p>}
    {revision.input.feedback && <p className="mt-2 whitespace-pre-wrap text-xs text-slate-600">本版反馈：{revision.input.feedback}</p>}
    {candidate ? <><p className="mt-4 text-xs text-slate-500">候选提交</p><code className="block break-all text-xs text-slate-700">{candidate}</code></> : <p className="mt-4 text-xs text-slate-500">此版本暂无候选稿。</p>}
    {typeof revision.draft?.summary === 'string' && <p className="mt-2 whitespace-pre-wrap text-sm">{revision.draft.summary}</p>}
    <div className="mt-4 space-y-2"><h4 className="text-xs font-semibold">逐项修复结果</h4>{revision.input.items.map(item => {
      const outcome = outcomes.find(outcome => outcome.itemId === item.itemId)
      const covered = coverage.find(row => row.itemId === item.itemId)
      return <div className="rounded-lg border border-slate-200 p-3 text-xs" key={item.itemId}>
        <p className="font-medium">{typeof item.proposal?.summary === 'string' ? item.proposal.summary : item.instruction || item.itemId}</p>
        <p className="mt-1 text-slate-600">处理结论：{outcome ? reportLabel(outcome.status, '未提供状态') : '尚未提供逐项结论'}</p>
        {outcome?.reason != null && <p className="mt-1 text-slate-500">{String(outcome.reason)}</p>}
        <p className="mt-1 text-slate-500">验证覆盖：{covered ? `${reportLabel(covered.mode, '未覆盖')} · ${String(covered.assertionCount ?? 0)} 条断言` : '未覆盖'}</p>
      </div>
    })}</div>
    <div className="mt-4 rounded-lg bg-slate-50 p-3 text-xs">
      <h4 className="font-semibold">候选检查</h4>
      <p className="mt-2">静态检查：{reportLabel(record(checks?.static).status, '尚未执行')} · Mock 检查：{reportLabel(record(checks?.mock).status, '尚未执行')}</p>
      {Array.isArray(record(checks?.static).issues) && <ul className="mt-2 list-inside list-disc text-red-700">{(record(checks?.static).issues as unknown[]).map((issue, index) => <li key={index}>{String(issue)}</li>)}</ul>}
      <p className="mt-2 text-amber-700">未覆盖：{Array.isArray(checks?.uncoveredChecks) ? checks.uncoveredChecks.map(value => reportLabel(value, '未知')).join('、') || '报告未声明' : '尚未提供检查报告'}。Mock 通过不代表线上资源或脚本执行已验证。</p>
      <JsonDetails title="完整检查报告" value={checks} /><JsonDetails title="完整候选报告" value={revision.draft} /><JsonDetails title="此版本错误" value={revision.error} />
    </div>
    {candidate && <div className="mt-4">
      <div className="flex flex-wrap items-center gap-2"><button type="button" className={button} disabled={!detail.capabilities.diff} onClick={() => setShowDiff(value => !value)}>{showDiff ? '收起差异' : '查看差异'}</button>
        <label className="text-xs">比较基准 <select aria-label="比较基准" className="rounded border border-slate-200 p-2" value={base} onChange={event => { setDiff(null); setDiffError(''); setBase(event.target.value as typeof base) }}>
          <option value="baseline">任务基线</option><option value="parent" disabled={!revision.input.parentCandidateCommit}>上一成功候选</option>
        </select></label></div>
      {!detail.capabilities.diff && <p className="mt-2 text-xs text-slate-500">当前未提供差异读取能力。</p>}
      {showDiff && diffLoading && <p role="status" className="mt-2 text-xs">加载候选差异…</p>}
      {showDiff && diffError && <p role="alert" className="mt-2 text-xs text-red-600">差异加载失败：{diffError} <button className={button} onClick={() => setRetry(value => value + 1)}>重试差异</button></p>}
      {showDiff && diff && <div className="mt-3 space-y-3">
        {diff.truncated && <p className="text-xs text-amber-700">差异已截断，仅展示部分内容。</p>}
        {!diff.files.length && <p className="text-xs text-slate-500">此基准下没有文件差异。</p>}
        {diff.files.map(file => <details key={file.path} open className="overflow-hidden rounded-lg border border-slate-200">
          <summary className="cursor-pointer break-all bg-slate-50 px-3 py-2 text-xs">{file.status} · {file.path}</summary>
          {file.binary ? <p className="p-3 text-xs">二进制文件，无法展示文本差异。</p> : <div className="grid sm:grid-cols-2">
            <div className="min-w-0 bg-red-50/50 p-3"><p className="mb-2 text-xs text-red-700">修改前</p><pre className="max-h-80 overflow-auto text-[11px]">{file.before ?? '(文件不存在)'}</pre></div>
            <div className="min-w-0 bg-emerald-50/50 p-3"><p className="mb-2 text-xs text-emerald-700">修改后</p><pre className="max-h-80 overflow-auto text-[11px]">{file.after ?? '(文件已删除)'}</pre></div>
          </div>}{file.truncated && <p className="p-2 text-xs text-amber-700">此文件内容已截断。</p>}
        </details>)}
      </div>}
    </div>}
  </section>
}
