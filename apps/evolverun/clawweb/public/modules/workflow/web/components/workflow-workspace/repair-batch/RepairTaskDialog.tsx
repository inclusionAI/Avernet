import { workflowSnapshotKey, type WorkflowSnapshotRef } from '@avernet/clawweb-shared/server/contracts/workflow-snapshot'
import { itemSnapshotKey } from '../../../../server/contracts/repair-version-scope'
import { useEffect, useRef, useState } from 'react'
import { repairBatches } from '../../../api/repair-batches'
import { repairActionFailure, type RepairRecoveryAction } from '../../../api/repair-action-error'
import { repairReadError } from '../../../api/repair-read-error'
import type { RepairCandidatesResponse, RepairTaskDetail } from '../../../../server/contracts/repair-workbench'
import RepairTaskPanel from './RepairTaskPanel'
import RepairWorkspace from './RepairWorkspace'
import { button, primary, RepairDialog } from './repair-view'

const frozenItemTitle = (item: RepairTaskDetail['latestAttempt']['input']['items'][number]) =>
  typeof item.proposal?.summary === 'string' ? item.proposal.summary : item.instruction || item.itemId

function feedbackItems(detail: RepairTaskDetail) {
  const rows=detail.latestAttempt.input.automation?.roundId?detail.revisions:[detail.latestAttempt];
  return [...new Map([...rows].sort((a,b)=>a.revision-b.revision).flatMap(row=>row.input.items.map(item=>[item.itemId,item] as const))).values()]
}

export default function RepairTaskDialog({ workflowId, taskId, includeHistorical, canEdit, onClose, onChanged, onNextRound, onPreviousRound, onWorkflowMismatch }: {
  workflowId: string; taskId: string; includeHistorical: boolean; canEdit: boolean;
  onClose: () => void; onChanged: () => void; onNextRound?: (publishedRevision: number) => void; onPreviousRound?: () => void;
  onWorkflowMismatch?: () => void;
}) {
  const [detail, setDetail] = useState<RepairTaskDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [taskError, setTaskError] = useState('')
  const [recoveryAction, setRecoveryAction] = useState<RepairRecoveryAction | null>(null)
  const [notice, setNotice] = useState('')
  const [sourceRefresh, setSourceRefresh] = useState(0)
  const editorRevision = useRef('')
  const [busy, setBusy] = useState(false)
  const [refresh, setRefresh] = useState(0)
  const [historyPage, setHistoryPage] = useState(1)
  const [mode, setMode] = useState<'detail' | 'feedback' | 'cancel'>('detail')
  const [instructions, setInstructions] = useState('')
  const [feedback, setFeedback] = useState('')
  const [feedbackItemIds, setFeedbackItemIds] = useState<string[]>([])
  const [frozenFeedbackItems, setFrozenFeedbackItems] = useState<RepairTaskDetail['latestAttempt']['input']['items']>([])
  const loadedFeedbackScope=useRef('')
  const [feedbackCandidates, setFeedbackCandidates] = useState<RepairCandidatesResponse | null>(null)
  const [feedbackCandidatesLoading, setFeedbackCandidatesLoading] = useState(false)
  const [feedbackCandidatesError, setFeedbackCandidatesError] = useState('')
  const [feedbackPage, setFeedbackPage] = useState(1)
  const [dispatchError, setDispatchError] = useState('')
  const requestId = useRef('')
  const requestPayloadKey = useRef('')
  const requestSequence = useRef(0)
  const activeTask = useRef<{ taskId: string; active: boolean } | null>(null)
  const workflowMismatch = useRef(onWorkflowMismatch)
  workflowMismatch.current = onWorkflowMismatch
  const feedbackHasTarget = !!detail?.latestAttempt.input.items[0]?.context?.targetSnapshot
  const feedbackHistory = feedbackHasTarget || includeHistorical

  useEffect(() => {
    let current = true
    let pollTimer: ReturnType<typeof setTimeout> | undefined
    const schedulePoll = () => {
      pollTimer = setTimeout(() => { if (current) setRefresh(value => value + 1) }, 2_000)
    }
    setLoading(detail == null); setTaskError('')
    const read = historyPage === 1 ? repairBatches.task(taskId) : repairBatches.task(taskId, historyPage)
    read.then(result => {
      if (!current) return
      if (result.workflowId !== workflowId || result.taskId !== taskId) {
        activeTask.current = { taskId, active: false }; setTaskError('此任务不属于当前工作流')
        if (result.workflowId !== workflowId) workflowMismatch.current?.()
        return
      }
      setDetail(result)
      const active = ['drafting', 'publishing'].includes(result.latestAttempt.phase) || !!result.automaticProgress && result.automaticProgress.completedBatches < result.automaticProgress.totalBatches && ['review', 'no_change'].includes(result.latestAttempt.phase)
      activeTask.current = { taskId, active }
      if (active) schedulePoll()
    }).catch(reason => {
      if (!current) return
      setTaskError(repairReadError(reason))
      if (activeTask.current?.taskId !== taskId || activeTask.current.active) schedulePoll()
    })
      .finally(() => { if (current) setLoading(false) })
    return () => { current = false; if (pollTimer) clearTimeout(pollTimer) }
  }, [workflowId, taskId, refresh, historyPage])

  useEffect(() => {
    if (mode !== 'feedback') return
    let current = true
    setFeedbackCandidatesLoading(true)
    setFeedbackCandidatesError('')
    repairBatches.candidates(workflowId, { metadataPage: 1, includeTasks: false, ...(feedbackHasTarget ? { versionScope: 'all' as const } : {}), state: 'pending', page: feedbackPage, pageSize: 20, includeHistorical: feedbackHistory })
      .then(async result => {
        if (!current || !detail) return
        const key=`${taskId}:${detail.latestAttempt.revision}`
        if(loadedFeedbackScope.current!==key){
          const originals=feedbackItems(detail)
          const items=new Map(originals.map(item=>[item.itemId,item]))
          if(detail.latestAttempt.input.automation?.roundId&&items.size<(detail.scopeItemCount??0)){
            for(let page=1;page<=(detail.revisionPage?.totalPages??1);page++){
              if(page===detail.revisionPage?.page)continue
              const historical=await repairBatches.task(taskId,page)
              if(historical.taskId!==taskId||historical.latestAttempt.revision!==detail.latestAttempt.revision)throw new Error('任务版本已变化，请返回后重新核对。')
              for(const item of feedbackItems(historical))if(!items.has(item.itemId))items.set(item.itemId,item)
            }
            if(items.size<(detail.scopeItemCount??0))throw new Error('完整原建议尚未读取，请重试；不会只取最后一批。')
          }
          if(!current)return
          setFrozenFeedbackItems([...items.values()]);setFeedbackItemIds([...items.keys()]);loadedFeedbackScope.current=key
        }
        setFeedbackCandidates(result)
      })
      .catch(reason => { if (current) setFeedbackCandidatesError(repairReadError(reason)) })
      .finally(() => { if (current) setFeedbackCandidatesLoading(false) })
    return () => { current = false }
  }, [mode, workflowId, feedbackPage, feedbackHistory, feedbackHasTarget, sourceRefresh])

  const inspectCandidate = (revision?: number) => {
    try {
      const key = `repair-workspace:${workflowId}:${taskId}`
      const saved = JSON.parse(sessionStorage.getItem(key) || '{}')
      sessionStorage.setItem(key, JSON.stringify({ ...saved, view: 'candidate', ...(revision ? { revision, followLatest: true } : {}) }))
    } catch { /* Browser storage is optional. */ }
    setMode('detail')
  }
  const resultNeedsRead = detail?.latestAttempt.phase === 'failed' && detail.latestAttempt.error?.code === 'GIT_RESULT_UNAVAILABLE'
  const effectiveRecoveryAction = recoveryAction ?? (resultNeedsRead ? 'retry-result' : null)
  const reload = () => { setRefresh(value => value + 1); onChanged() }
  const openSelection = () => {
    if (!detail || busy) return
    const key = `${taskId}:${detail.latestAttempt.revision}`
    if (editorRevision.current !== key) {
      editorRevision.current = key
      setInstructions(detail.latestAttempt.input.instructions)
      setFeedback(detail.latestAttempt.phase === 'failed' ? detail.latestAttempt.input.feedback : '')
      const originals=feedbackItems(detail);setFrozenFeedbackItems(originals);setFeedbackItemIds(originals.map(item=>item.itemId))
      loadedFeedbackScope.current=!detail.latestAttempt.input.automation?.roundId||originals.length>=(detail.scopeItemCount??0)?key:''
      setFeedbackPage(1); setFeedbackCandidates(null); setFeedbackCandidatesError('')
      requestId.current = ''; requestPayloadKey.current = ''
    }
    setMode('feedback')
  }
  const acceptTask = (result: RepairTaskDetail) => {
    if (result.workflowId !== workflowId || result.taskId !== taskId) throw new Error('Task identity mismatch')
    setDetail(result); setTaskError('')
    return result
  }
  const recover = async () => {
    if (!detail || busy) return
    if (effectiveRecoveryAction === 'refresh-sources') {
      setFeedbackItemIds([]); requestId.current = ''; requestPayloadKey.current = ''
      setSourceRefresh(value => value + 1); setRecoveryAction(null); setError('')
      setNotice('建议来源已更新，原选择已清空，请重新确认本轮范围。'); return
    }
    setBusy(true); setError(''); setNotice(''); setDispatchError('')
    try {
      if (effectiveRecoveryAction === 'retry-result' && canEdit) await repairBatches.retryResult(taskId, detail.latestAttempt.revision)
      const result = acceptTask(await repairBatches.task(taskId))
      if (['drafting', 'publishing'].includes(result.latestAttempt.phase)) setRefresh(value => value + 1)
      setRecoveryAction(null)
      if (result.latestAttempt.draft || result.latestAttempt.requestId === requestId.current || result.latestAttempt.revision !== detail.latestAttempt.revision) {
        inspectCandidate(result.latestSuccessful?.revision ?? result.latestAttempt.revision); setNotice('任务状态已核对，当前结果已保留；未提交的范围调整也仍保留。')
      } else setNotice('任务状态已核对，可以再次提交当前调整；相同内容沿用原请求编号。')
      onChanged()
    } catch (reason) {
      const failure = repairActionFailure(reason); setError(failure.message); setRecoveryAction(failure.action)
    } finally { setBusy(false) }
  }
  const recoveryButton = (effectiveRecoveryAction || taskError) && <button type="button" className={button} disabled={busy} onClick={() => void recover()}>
    {effectiveRecoveryAction === 'retry-result' && canEdit ? '核对原结果' : effectiveRecoveryAction === 'refresh-sources' ? '重新读取建议' : '核对任务状态'}
  </button>
  const retryDispatch = async () => {
    if (!detail || busy) return
    setBusy(true); setDispatchError('')
    try { await repairBatches.retryDispatch(taskId, detail.latestAttempt.revision); reload() }
    catch (reason) { setDispatchError(repairActionFailure(reason).message) }
    finally { setBusy(false) }
  }
  const retryGeneration = async () => {
    if (!detail || busy) return
    const key = `retry:${detail.latestAttempt.revision}`
    if (!requestId.current || requestPayloadKey.current !== key) {
      requestId.current = globalThis.crypto.randomUUID(); requestPayloadKey.current = key
    }
    setBusy(true); setDispatchError('')
    try {
      if (detail.latestAttempt.error?.code === 'GIT_RESULT_UNAVAILABLE') await repairBatches.retryResult(taskId, detail.latestAttempt.revision)
      else await repairBatches.retryGeneration(taskId, { expectedRevision: detail.latestAttempt.revision, requestId: requestId.current })
      requestId.current = ''; reload()
    } catch (reason) {
      const failure = repairActionFailure(reason); setDispatchError(failure.message); setRecoveryAction(failure.action); reload()
    } finally { setBusy(false) }
  }
  const cancel = async () => {
    if (!detail || busy) return
    setBusy(true); setError('')
    try { await repairBatches.cancel(taskId, detail.latestAttempt.revision); setMode('detail'); reload() }
    catch (reason) { setError(repairActionFailure(reason).message) }
    finally { setBusy(false) }
  }
  const feedbackAllowed=!!detail&&['failed','review','blocked','no_change'].includes(detail.latestAttempt.phase)
  const revise = async () => {
    if (!detail || !feedbackCandidates || !feedbackItemIds.length || feedbackCandidatesLoading || feedbackCandidatesError || taskError || busy || effectiveRecoveryAction || !canEdit || !feedbackCandidates.canEdit
      || !detail.capabilities.generation || !feedbackAllowed || feedbackItemIds.length>feedbackCandidates.limits.maxItems
      || editorRevision.current !== `${taskId}:${detail.latestAttempt.revision}`) return
    const target = detail.latestAttempt.input.items[0]?.context?.targetSnapshot as WorkflowSnapshotRef | undefined
    const targetKey = target ? workflowSnapshotKey(target) : undefined
    const items = [...frozenFeedbackItems, ...feedbackCandidates.items]
    const revisionInput = {
      workflowId, inputDigest: feedbackCandidates.inputDigest, includeHistorical: feedbackCandidates.includeHistorical,
      itemIds: feedbackItemIds, instructions, ...(targetKey ? { targetSnapshotKey: targetKey, historicalItemIds: feedbackItemIds.filter(id => { const item = items.find(value => value.itemId === id); return item && itemSnapshotKey(item) !== targetKey }) } : {}),
      expectedAttemptRevision: detail.latestAttempt.revision,
      parentCandidateCommit: typeof detail.latestSuccessful?.draft?.candidateCommit === 'string' ? detail.latestSuccessful.draft.candidateCommit : null,
      feedback: feedback.trim(),
    }
    const payloadKey = JSON.stringify(revisionInput)
    if (!requestId.current || requestPayloadKey.current !== payloadKey) {
      requestSequence.current += 1
      requestId.current = globalThis.crypto?.randomUUID?.() ?? `repair-feedback-${Date.now()}-${requestSequence.current}`
      requestPayloadKey.current = payloadKey
    }
    const payload = { ...revisionInput, requestId: requestId.current }
    if (new TextEncoder().encode(JSON.stringify(payload)).length > feedbackCandidates.limits.maxRequestBytes) {
      setError('请求内容过大，请缩短说明或减少选择。')
      return
    }
    setBusy(true); setError(''); setNotice('')
    try {
      await repairBatches.revise(taskId, payload)
      requestId.current = ''; requestPayloadKey.current = ''; try { sessionStorage.removeItem(`repair-workspace:${workflowId}:${taskId}`) } catch {} setMode('detail'); setFeedback(''); reload()
    } catch (reason) {
      const failure = repairActionFailure(reason); setError(failure.message); setRecoveryAction(failure.action)
    }
    finally { setBusy(false) }
  }

  if (mode === 'feedback' && detail) {
    const frozenItems = frozenFeedbackItems
    const frozenIds = new Set(frozenItems.map(item => item.itemId))
    const selectableItems = [...frozenItems, ...(feedbackCandidates?.items ?? []).filter(item => !frozenIds.has(item.itemId))]
    const maxItems = feedbackCandidates?.limits.maxItems ?? 100
    const readOnly = !canEdit || feedbackCandidates?.canEdit === false || !detail.capabilities.generation || !feedbackAllowed
    const blocker = busy ? '正在提交或核对任务，请稍候…' : taskError ? '任务读取失败，请先核对任务状态。'
      : !canEdit || feedbackCandidates?.canEdit === false ? '当前只有查看权限，不能生成候选。'
      : !detail.capabilities.generation ? '生成服务尚未接入，仍可查看范围与候选。'
      : !feedbackAllowed ? '当前任务状态不允许生成新一轮；可返回审核查看进度。'
      : editorRevision.current !== `${taskId}:${detail.latestAttempt.revision}` ? '任务已进入新轮次，请返回审核后重新确认范围。'
      : effectiveRecoveryAction ? error || '原候选结果尚未确认，请先核对原结果。'
      : feedbackCandidatesLoading ? '正在读取本轮可选建议…'
      : feedbackCandidatesError || !feedbackCandidates ? '建议读取失败，请重试后生成。'
      : !feedbackItemIds.length ? '请至少保留一条建议。' : feedbackItemIds.length>maxItems ? `单次最多 ${maxItems} 条建议，请明确调整本版范围；历史版本保留。` : ''

    return <RepairWorkspace navigation={false} stage={1} title="调整建议与要求" subtitle={`${workflowId} · 沿用冻结基线，保留原始问题与证据`} onBack={() => { if (!busy) inspectCandidate() }} backLabel="返回审核 Pack" onSelect={openSelection} onReview={() => setMode('detail')} busy={busy}>
    <p className="text-xs leading-5 text-muted-foreground">选择建议即可生成；修复要求与修改反馈均为选填。新候选保留原任务基线，不会应用或部署。</p>
    <p className="text-xs text-muted-foreground">{detail.latestSuccessful ? `下一轮从最新成功候选 v${detail.latestSuccessful.revision} 继续；查看历史版本不会改变生成基准。` : '当前尚无成功候选，下一轮从原任务基线开始。'}</p>
    <div className="grid items-start gap-6 xl:grid-cols-[minmax(0,1fr)_19rem]"><fieldset className="rounded-lg border border-input bg-card p-5">
      <legend className="px-1 text-xs font-medium text-foreground">确认本轮处理项</legend>
      {feedbackCandidatesLoading && <p role="status" className="mt-1 text-xs text-muted-foreground">加载最新待处理项…</p>}
      {feedbackCandidatesError && <p role="alert" className="mt-1 text-xs text-destructive">{feedbackCandidatesError} <button type="button" className="underline" disabled={busy} onClick={() => setSourceRefresh(value => value + 1)}>重试读取建议</button></p>}
      <div className="mt-1 space-y-2">{selectableItems.map(item => {
        const title = frozenItemTitle(item)
        return <label key={item.itemId} className="flex items-start gap-3 rounded-lg border border-input/50 p-3 text-xs text-muted-foreground">
          <input type="checkbox" aria-label={`选择修订项 ${title}`} checked={feedbackItemIds.includes(item.itemId)}
            disabled={busy || readOnly || feedbackCandidatesLoading && (!frozenIds.has(item.itemId) || loadedFeedbackScope.current !== editorRevision.current) || (!feedbackItemIds.includes(item.itemId) && feedbackItemIds.length >= maxItems)}
            onChange={() => setFeedbackItemIds(current => current.includes(item.itemId)
              ? current.filter(id => id !== item.itemId) : [...current, item.itemId])}
            className="mt-0.5 h-4 w-4 rounded border-input accent-primary" />
          <span>{title}{!frozenIds.has(item.itemId) && <span className="ml-1 text-primary">新待处理</span>}</span>
        </label>
      })}</div>
      {feedbackCandidates && feedbackCandidates.page.totalPages > 1 && <div className="mt-3 flex items-center justify-between text-xs text-muted-foreground">
        <button type="button" disabled={feedbackPage <= 1 || feedbackCandidatesLoading} onClick={() => setFeedbackPage(page => page - 1)} className="underline disabled:no-underline disabled:opacity-50">上一页</button>
        <span>待处理项第 {feedbackCandidates.page.page}/{feedbackCandidates.page.totalPages} 页</span>
        <button type="button" disabled={feedbackPage >= feedbackCandidates.page.totalPages || feedbackCandidatesLoading} onClick={() => setFeedbackPage(page => page + 1)} className="underline disabled:no-underline disabled:opacity-50">下一页</button>
      </div>}
    </fieldset>
    <aside className="space-y-4 xl:sticky xl:top-4"><p className="rounded-lg border border-input bg-card p-5 text-sm font-semibold">本轮已选 {feedbackItemIds.length} 条建议</p><label className="block rounded-lg border border-input bg-card p-5 text-xs font-medium text-foreground">修复要求 <span className="font-normal text-muted-foreground">（选填）</span>
      <textarea aria-label="修复要求" rows={4} maxLength={20_000} value={instructions} disabled={busy || readOnly}
        onChange={event => setInstructions(event.target.value)} className="mt-2 w-full rounded-lg border border-input p-3 font-normal leading-5" />
    </label>
    <label className="block rounded-lg border border-input bg-card p-5 text-xs font-medium text-foreground">修改反馈 <span className="font-normal text-muted-foreground">（选填）</span>
      <textarea aria-label="修改反馈" rows={5} maxLength={20_000} value={feedback} disabled={busy || readOnly}
        onChange={event => setFeedback(event.target.value)} className="mt-2 w-full rounded-lg border border-input p-3 font-normal leading-5" />
    </label>
    </aside></div>
    <footer className="sticky bottom-4 z-20 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-input bg-card px-5 py-4 shadow-sm">
      <div><p className="text-sm font-semibold text-foreground">本轮已选 {feedbackItemIds.length} 条 · 生成后进入审核</p>
        <p id="repair-generation-help" role={error ? 'alert' : 'status'} className={`mt-1 text-xs ${error ? 'text-destructive' : 'text-muted-foreground'}`}>{error || blocker || notice || '修复要求与反馈选填，留空将沿用所选建议。'}</p>
      </div><div className="flex flex-wrap gap-2">{recoveryButton}<button type="button" className={primary} aria-describedby="repair-generation-help" disabled={!!blocker} onClick={() => void revise()}>{busy ? '处理中…' : '生成新版本'}</button></div>
    </footer>
  </RepairWorkspace>
  }

  if (mode === 'cancel' && detail) return <RepairDialog title="取消修复任务" busy={busy} onClose={() => setMode('detail')}>
    <p className="text-xs leading-5 text-muted-foreground">取消任务会保留候选稿与历史记录，并释放处理项供后续重新选择。远端停止需另行核对；迟到结果不会重新激活本任务。</p>
    {error && <p role="alert" className="mt-3 text-xs text-destructive">{error}</p>}
    <button type="button" className={`${primary} mt-4`} disabled={busy} onClick={() => void cancel()}>{busy ? '取消中…' : '确认取消任务'}</button>
  </RepairDialog>

  return <section aria-label="Workflow 修复工作区" className="mx-auto max-w-[96rem] space-y-5 pb-8">
    <button type="button" className="text-xs text-muted-foreground hover:text-primary" disabled={busy} onClick={onClose}>← 返回问题与优化</button>
    {onPreviousRound && <button type="button" className={button} disabled={busy} onClick={onPreviousRound}>返回上一轮观察</button>}
    {loading && <p role="status" className="text-xs text-muted-foreground">加载修复任务…</p>}
    {taskError && <p role="alert" className="text-xs text-destructive">{taskError} <button type="button" className={button} disabled={busy} onClick={reload}>重试读取任务</button></p>}
    {(error || notice || effectiveRecoveryAction) && <div className="flex flex-wrap items-center gap-3 rounded-lg border border-input bg-card p-4"><p role={error ? 'alert' : 'status'} className="text-xs text-muted-foreground">{error || notice || dispatchError}</p>{recoveryButton}</div>}
    {detail && <RepairTaskPanel key={detail.taskId}
      detail={detail} onHistoryPageChange={setHistoryPage} canEdit={canEdit && !taskError && !loading && !recoveryAction} busy={busy} dispatchError={recoveryAction ? '' : dispatchError}
      onFeedback={openSelection}
      onNextRound={onNextRound}
      onCancel={() => { setError(''); setMode('cancel') }} onRetryDispatch={() => void retryDispatch()} onRetryGeneration={() => void retryGeneration()} onRefresh={reload} />}
  </section>
}
