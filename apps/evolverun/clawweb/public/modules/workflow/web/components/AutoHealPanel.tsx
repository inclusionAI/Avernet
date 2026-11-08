import { autoHealSaveIsCurrent, autoHealSavedBaselineChangedMessage, autoHealUnifiedSaveIdentity, bindAutoHealDeployToSave, isAutoHealWriteNotSent, isDeployPrePushConflict, PostSaveLockError, postSaveLockMessage } from '../api/autoHealWrites'
import { readAutoHealUnifiedBaseline, readAutoHealUnifiedCapability, AutoHealScopedCapabilityError, confirmAutoHealUnifiedReview, type AutoHealUnifiedBaseline } from '../api/autoHealUnifiedBaseline'
import { serializeUnifiedSpec } from '../api/workflowUnifiedSave'
import AutoHealSaveReview from './AutoHealSaveReview'
import AutoHealDeployConfirm from './AutoHealDeployConfirm'
import type { AutoHealDeploySnapshot } from '../api/autoHealDeploySnapshot'
import { editorWorkflowWriteSnapshot, editorWorkflowWriteState, subscribeEditorWorkflowWrites } from '../editor/workflowWriteGuard'
import { hasPostSaveVerificationError, packVersionsRevision, subscribePackVersions } from '../editor/packVersionSession'
import { assertDeploymentAvailable, clearAttemptConfirmedDeployment, clearEditorReconciledDeployment, clearRejectedDeployment, createDeploymentId, markWorkflowDeploymentPending, finishWorkflowDeployment, isWorkflowDeploymentPending, readEditorDeploymentMarker, subscribeWorkflowDeploymentPending, workflowDeploymentPendingRevision } from '../editor/deploymentGuard'
import { useState, useCallback, useEffect, useRef, useSyncExternalStore } from 'react'
import { useAutoHealDiagnose, useAutoHealSave, useAutoHealDeploy } from '../api/hooks'
import { useAutoHealSourceRun } from '../api/useAutoHealSourceRun'
import StatusBadge from './StatusBadge'
import type { FlowRun, AutoHealDiagnosisResult, AutoHealDiffItem, AutoHealErrorChainItem, AutoHealFixSuggestion, AutoHealSaveResult } from '@avernet/clawweb-shared/web/types'
import { formatDuration } from '../utils/time'
import DeploymentRecoveryDialog from './DeploymentRecoveryDialog'
import { PanelHeader, PhaseIndicator, type AutoHealPhase } from './AutoHealPanelHeader'

type Phase = AutoHealPhase

interface AutoHealPanelProps {
  run: FlowRun
  onClose: () => void
  onRerunComplete?: () => void
}

export default function AutoHealPanel(props: AutoHealPanelProps) {
  // Diagnosis and rerun must never carry over to a different source run.
  return <AutoHealPanelSession key={JSON.stringify([props.run.workflow_id, props.run.flow_id])} {...props} />
}

function AutoHealPanelSession({ run, onClose, onRerunComplete }: AutoHealPanelProps) {
  const [phase, setPhase] = useState<Phase>('idle')
  // A previous implementation persisted sensitive diagnosis/spec data without a
  // verified actor. Never read or render it. Remove it and leave a non-sensitive
  // marker so an unknown in-flight write cannot silently be replaced on reload.
  const [legacyBlocked, setLegacyBlocked] = useState(true)
  const [deploymentRecovery, setDeploymentRecovery] = useState<{ workflowId: string; message: string; storageAvailable: boolean; attemptToken?: string } | null>(null)
  const [deploymentRecoveryError, setDeploymentRecoveryError] = useState<string | null>(null)
  const [deploymentRecoveryOpen, setDeploymentRecoveryOpen] = useState(false)
  const recoveryTarget = useRef<{ workflowId: string; token: string } | null>(null)
  useSyncExternalStore(subscribeWorkflowDeploymentPending, workflowDeploymentPendingRevision, workflowDeploymentPendingRevision)
  const workflowPending = isWorkflowDeploymentPending(run.workflow_id)
  useSyncExternalStore(subscribePackVersions, packVersionsRevision, packVersionsRevision)
  const postSaveLocked = hasPostSaveVerificationError(run.workflow_id)
  useSyncExternalStore(subscribeEditorWorkflowWrites, () => editorWorkflowWriteSnapshot(run.workflow_id), () => editorWorkflowWriteSnapshot(run.workflow_id))
  const databaseWritePhase = editorWorkflowWriteState(run.workflow_id).phase
  const mounted = useRef(false)
  useEffect(() => { mounted.current = true; return () => { mounted.current = false } }, [])
  useEffect(() => {
    const prefix = 'clawweb.autoheal.operation.v1.'
    const marker = 'clawweb.autoheal.review-blocked.v1.'
    try {
      const keys = Array.from({ length: localStorage.length }, (_, index) => localStorage.key(index))
      let failed = false
      for (const key of keys) {
        if (!key?.startsWith(prefix)) continue
        const suffix = key.slice(prefix.length)
        try { localStorage.setItem(marker + suffix, '1') } catch { failed = true }
        try { localStorage.removeItem(key) } catch { failed = true }
      }
      let blockedMarker = localStorage.getItem(marker + encodeURIComponent(run.workflow_id)) !== null
      try { assertDeploymentAvailable(run.workflow_id) } catch { blockedMarker = true }
      setDeploymentRecovery(current => blockedMarker
        ? current?.workflowId === run.workflow_id && current.attemptToken ? current : { workflowId: run.workflow_id, message: '存在未确认的浏览器核对标记。请人工核对 Git 和数据库后，仅清除当前工作流标记。', storageAvailable: !failed }
        : null)
      setLegacyBlocked(failed || blockedMarker)
    } catch {
      setDeploymentRecovery({ workflowId: run.workflow_id, message: '无法访问浏览器核对标记，无法安全恢复。', storageAvailable: false })
      setLegacyBlocked(true)
    }
  }, [run.workflow_id, workflowPending])
  const [customPrompt, setCustomPrompt] = useState('')
  const [diagnosisResult, setDiagnosisResult] = useState<AutoHealDiagnosisResult | null>(null)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const [showCustomPrompt, setShowCustomPrompt] = useState(false)
  const [diffExpanded, setDiffExpanded] = useState(true)
  const [errorChainExpanded, setErrorChainExpanded] = useState(true)
  const [suggestionsExpanded, setSuggestionsExpanded] = useState(true)
  const [autoRun, setAutoRun] = useState(true)
  const [deployConfirmation, setDeployConfirmation] = useState<AutoHealSaveResult | null>(null)
  const [saveMode, setSaveMode] = useState<'checking' | 'unified' | 'compatible' | 'failed' | 'unavailable'>('checking')
  const [capabilityError, setCapabilityError] = useState('')
  const [review, setReview] = useState<{ baseline: AutoHealUnifiedBaseline; candidateJson: string } | null>(null)
  const saveInFlight = useRef(false)
  useEffect(() => {
    if (!diagnosisResult) return
    let current = true
    setSaveMode('checking'); setCapabilityError(''); setReview(null)
    void readAutoHealUnifiedCapability(diagnosisResult.workflowId).then(enabled => {
      if (current) setSaveMode(enabled ? 'unified' : 'compatible')
    }).catch(error => {
      if (current) { setCapabilityError(extractErrorMessage(error, '无法确认宿主保存能力。')); setSaveMode(error instanceof AutoHealScopedCapabilityError ? 'unavailable' : 'failed') }
    })
    return () => { current = false }
  }, [diagnosisResult])
  const [saveResult, setSaveResult] = useState<{ workflowId: string; packId: string; receipt: AutoHealSaveResult } | null>(null)

  const diagnoseMutation = useAutoHealDiagnose()
  const saveMutation = useAutoHealSave()
  const deployMutation = useAutoHealDeploy()
  const runMutation = useAutoHealSourceRun()
  // Block same-tick double clicks before mutation state can rerender the buttons.
  const deployInFlight = useRef(false)
  const runInFlight = useRef(false)

  // Close on Escape
  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', handleKey)
    return () => window.removeEventListener('keydown', handleKey)
  }, [onClose])

  const handleDiagnose = useCallback(() => {
    if (!run.flow_id?.trim()) {
      setPhase('error')
      setErrorMessage('运行记录缺少 flow_id，无法发起诊断。请刷新页面后重试。')
      return
    }

    setSaveMode('checking'); setReview(null)
    setPhase('diagnosing')
    setErrorMessage(null)
    setDiagnosisResult(null)

    diagnoseMutation.mutate(
      {
        flowId: run.flow_id,
        useBaas: true,
        customPrompt: customPrompt.trim() || undefined,
      },
      {
        onSuccess: (data) => {
          setDiagnosisResult(data)
          setPhase('diagnosed')
        },
        onError: (err) => {
          setPhase('error')
          setErrorMessage(extractErrorMessage(err, '诊断失败，请稍后重试'))
        },
      },
    )
  }, [run.flow_id, customPrompt, diagnoseMutation])

  // Track elapsed time while diagnosing for progress display
  const [diagnosingElapsed, setDiagnosingElapsed] = useState(0)
  useEffect(() => {
    if (phase !== 'diagnosing') {
      setDiagnosingElapsed(0)
      return
    }
    const startTime = Date.now()
    const timer = setInterval(() => {
      setDiagnosingElapsed(Math.floor((Date.now() - startTime) / 1000))
    }, 1000)
    return () => clearInterval(timer)
  }, [phase])

  const submitSave = useCallback((spec: NonNullable<AutoHealDiagnosisResult['fixedSpec']>, baseline?: AutoHealUnifiedBaseline) => {
    if (!diagnosisResult) return
    let requestId: string | undefined
    try { if (baseline) requestId = createDeploymentId() }
    catch (err) { saveInFlight.current = false; setPhase('error'); setErrorMessage(extractErrorMessage(err, '无法创建请求标识；未发送保存。')); return }
    setReview(null); setPhase('saving'); setErrorMessage(null); setSaveResult(null)
    saveMutation.mutate({ workflowId: diagnosisResult.workflowId, spec, diagnosisId: diagnosisResult.diagnosisId,
      note: 'auto-heal save', ...(baseline ? { unifiedBaseline: baseline, requestId } : {}) }, {
      onSuccess: (result) => {
        saveInFlight.current = false
        if (!result || result.ok !== true || result.saved !== true || result.workflowId !== diagnosisResult.workflowId || typeof result.packId !== 'string' || !result.packId.trim()) {
          setPhase('error'); setErrorMessage('保存结果无效或工作流不匹配，无法部署。请先核对草稿。'); return
        }
        setSaveResult({ workflowId: result.workflowId, packId: result.packId, receipt: result }); setPhase('saved')
      },
      onError: (err) => {
        saveInFlight.current = false; setPhase('error')
        setErrorMessage(err instanceof PostSaveLockError ? postSaveLockMessage : extractErrorMessage(err, '保存修复失败'))
      },
    })
  }, [diagnosisResult, saveMutation])

  const handleSave = useCallback(async () => {
    if (!diagnosisResult?.fixedSpec || legacyBlocked || saveInFlight.current || review || saveMode === 'checking' || saveMode === 'failed' || saveMode === 'unavailable') return
    if (diagnosisResult.workflowId !== run.workflow_id) { setPhase('error'); setErrorMessage('诊断工作流不匹配，无法保存。'); return }
    if (hasPostSaveVerificationError(diagnosisResult.workflowId)) { setPhase('error'); setErrorMessage(postSaveLockMessage); return }
    try { assertDeploymentAvailable(diagnosisResult.workflowId) } catch (err) {
      setLegacyBlocked(true); setPhase('error'); setErrorMessage(extractErrorMessage(err, '无法访问浏览器核对标记。')); return
    }
    saveInFlight.current = true
    if (saveMode === 'compatible') { submitSave(diagnosisResult.fixedSpec); return }
    setPhase('saving'); setErrorMessage(null)
    try {
      const candidateJson = serializeUnifiedSpec(diagnosisResult.fixedSpec)
      const baseline = await readAutoHealUnifiedBaseline(diagnosisResult.workflowId)
      if (mounted.current) { setReview({ baseline, candidateJson }); setPhase('diagnosed') }
    } catch (err) {
      if (mounted.current) { setPhase('error'); setErrorMessage(extractErrorMessage(err, '读取保存基线失败；请求未发送。')) }
    } finally { saveInFlight.current = false }
  }, [diagnosisResult, legacyBlocked, review, saveMode, run.workflow_id, submitSave])

  const handleReviewConfirm = useCallback(() => {
    if (!review || !diagnosisResult?.fixedSpec || saveInFlight.current || legacyBlocked || saveMode !== 'unified') return
    try {
      if (serializeUnifiedSpec(diagnosisResult.fixedSpec) !== review.candidateJson) throw new Error('修复内容已变化，请重新核对。')
      assertDeploymentAvailable(diagnosisResult.workflowId)
      if (hasPostSaveVerificationError(diagnosisResult.workflowId)) throw new Error(postSaveLockMessage)
      const spec = JSON.parse(review.candidateJson) as NonNullable<AutoHealDiagnosisResult['fixedSpec']>
      confirmAutoHealUnifiedReview(review.baseline, spec)
      saveInFlight.current = true
      submitSave(spec, review.baseline)
    } catch (err) { setReview(null); setPhase('error'); setErrorMessage(extractErrorMessage(err, '无法确认基线；请求未发送。')) }
  }, [review, diagnosisResult, legacyBlocked, saveMode, submitSave])

  const openDeploymentRecovery = useCallback(() => {
    if (!deploymentRecovery?.storageAvailable || isWorkflowDeploymentPending(run.workflow_id)) return
    try {
      const marker = readEditorDeploymentMarker(run.workflow_id)
      if (!marker || deploymentRecovery.attemptToken && marker !== deploymentRecovery.attemptToken) throw new Error('部署请求与核对标记不匹配，请重新核对。')
      recoveryTarget.current = { workflowId: run.workflow_id, token: marker }
      setDeploymentRecoveryError(null); setDeploymentRecoveryOpen(true)
    } catch (error) { setErrorMessage(extractErrorMessage(error, '无法读取核对标记。')); setPhase('error') }
  }, [deploymentRecovery, run.workflow_id])

  const handleDeploymentRecoveryConfirm = useCallback(() => {
    const target = recoveryTarget.current
    if (!deploymentRecovery?.storageAvailable || !target || target.workflowId !== run.workflow_id) return
    setDeploymentRecoveryError(null)
    try {
      if (deploymentRecovery.attemptToken && target.token !== deploymentRecovery.attemptToken) throw new Error('部署请求与核对标记不匹配。')
      clearEditorReconciledDeployment(target.workflowId, target.token)
    } catch (error) {
      setDeploymentRecoveryError(extractErrorMessage(error, '已确认人工核对，但无法清除浏览器核对标记；当前工作流仍保持 blocked。'))
      return
    }
    recoveryTarget.current = null
    setDeploymentRecovery(null); setDeploymentRecoveryOpen(false); setLegacyBlocked(false); setErrorMessage(null)
    setPhase(saveResult && diagnosisResult ? 'saved' : 'idle')
  }, [deploymentRecovery, run.workflow_id, saveResult, diagnosisResult])

  const handleRerun = useCallback(() => {
    if (!diagnosisResult || runInFlight.current) return
    if (!run.flow_id?.trim()) {
      setPhase('run-error')
      setErrorMessage('运行记录缺少 flow_id，无法重新运行。请刷新页面后重试。')
      return
    }

    runInFlight.current = true
    setPhase('running')
    setErrorMessage(null)

    runMutation.mutate(
      { workflowId: diagnosisResult.workflowId, sourceFlowId: run.flow_id },
      {
        onSuccess: () => {
          runInFlight.current = false
          setPhase('idle')
          setDiagnosisResult(null)
          setCustomPrompt('')
          onRerunComplete?.()
        },
        onError: (err) => {
          runInFlight.current = false
          setPhase('run-error')
          setErrorMessage(extractErrorMessage(err, '重新运行失败'))
        },
      },
    )
  }, [diagnosisResult, run.flow_id, runMutation, onRerunComplete])

  const handleDeploy = useCallback(async (snapshot?: AutoHealDeploySnapshot) => {
    if (!diagnosisResult || !saveResult || deployInFlight.current || legacyBlocked) return
    const unified = autoHealUnifiedSaveIdentity(saveResult.receipt)
    if (unified && (!snapshot || snapshot.commit !== unified.commit || snapshot.draftToken !== unified.draftToken)) { setErrorMessage('部署审阅与保存回执不匹配；请求未发送。'); return }
    if (!autoHealSaveIsCurrent(saveResult.receipt)) { setErrorMessage(autoHealSavedBaselineChangedMessage); return }
    if (hasPostSaveVerificationError(saveResult.workflowId)) { setErrorMessage(postSaveLockMessage); return }

    // Persist only a non-sensitive uncertainty marker before sending. Without a
    // server journal, a reload after lost acknowledgement must not silently retry.
    let pendingToken: string, requestId: string
    try { requestId = createDeploymentId(); pendingToken = markWorkflowDeploymentPending(saveResult.workflowId) } catch (err) {
      setLegacyBlocked(true)
      setPhase('error')
      setErrorMessage(extractErrorMessage(err, '无法记录待核对标记，部署未发送；请检查浏览器存储。'))
      return
    }
    deployInFlight.current = true
    setPhase('deploying')
    setErrorMessage(null)

    const input = { workflowId: saveResult.workflowId, packId: saveResult.packId, requestId,
      diagnosisId: diagnosisResult.diagnosisId, note: 'auto-heal deploy', autoRun: false,
      ...(snapshot && unified ? { candidateCommit: snapshot.commit, expectedDraftToken: snapshot.draftToken, expectedActiveCommit: snapshot.expectedActiveCommit, snapshotProtocol: 'saved-tree-v2' as const, confirmedFileDiff: true as const } : {}) }
    try {
      bindAutoHealDeployToSave(input, saveResult.receipt)
      const result = await deployMutation.mutateAsync(input)
          deployInFlight.current = false
          if (!result || result.ok !== true || result.deployed !== true || result.workflowId !== saveResult.workflowId || result.packId !== saveResult.packId
            || !Number.isSafeInteger(result.deployNumber) || result.deployNumber < 1 || !Number.isSafeInteger(result.version)
            || result.version < 1 || typeof result.tagName !== 'string' || !result.tagName.trim()
            || (snapshot && unified && (result.requestId !== requestId || !('snapshotProtocol' in result) || result.snapshotProtocol !== 'saved-tree-v2' || !('candidateCommit' in result) || result.candidateCommit !== snapshot.commit || !('expectedActiveCommit' in result) || result.expectedActiveCommit !== snapshot.expectedActiveCommit))) {
            setLegacyBlocked(true)
            setDeploymentRecovery({ workflowId: saveResult.workflowId, message: '部署结果无法确认，当前工作流需要人工核对。', storageAvailable: true, attemptToken: pendingToken })
            setPhase('error')
            setErrorMessage('部署结果无法确认；请先人工核对 Git 和数据库，勿重试。')
            return
          }
          try { clearAttemptConfirmedDeployment(saveResult.workflowId, pendingToken) } catch (err) {
            setLegacyBlocked(true)
            setDeploymentRecovery({ workflowId: saveResult.workflowId, message: '浏览器核对标记清除失败，当前工作流仍保持 blocked。', storageAvailable: true, attemptToken: pendingToken })
            setPhase('error')
            setErrorMessage(extractErrorMessage(err, '无法清除浏览器核对标记。'))
            return
          }
          setPhase('deployed')
          if (autoRun && mounted.current) handleRerun()
    } catch (err) {
          deployInFlight.current = false
          if (err instanceof PostSaveLockError || isAutoHealWriteNotSent(err, input)) {
            try { clearRejectedDeployment(saveResult.workflowId, pendingToken); setPhase('saved'); setErrorMessage(err instanceof PostSaveLockError ? postSaveLockMessage : extractErrorMessage(err, '部署未发送。')) }
            catch (clearError) {
              setLegacyBlocked(true); setDeploymentRecovery({ workflowId: saveResult.workflowId, message: extractErrorMessage(clearError, '无法清除浏览器核对标记。'), storageAvailable: true, attemptToken: pendingToken })
              setPhase('error'); setErrorMessage(extractErrorMessage(clearError, '无法清除浏览器核对标记。'))
            }
            return
          }
          if (isDeployPrePushConflict(err, { workflowId: saveResult.workflowId, requestId })) {
            try {
              clearRejectedDeployment(saveResult.workflowId, pendingToken)
              setDeploymentRecovery(null)
              setLegacyBlocked(false)
              setDiagnosisResult(null)
              setSaveResult(null)
              setPhase('error')
              setErrorMessage('当前生效 tag 缺失或候选快照存在文件缺失、模式或受保护文件内容冲突，部署已阻止。请联系有权限的维护人员核对并修复，然后重新诊断并保存。')
              return
            } catch (clearError) {
              setLegacyBlocked(true)
              setDeploymentRecovery({ workflowId: saveResult.workflowId, message: extractErrorMessage(clearError, '无法清除浏览器核对标记。'), storageAvailable: true, attemptToken: pendingToken })
              setPhase('error')
              setErrorMessage(extractErrorMessage(clearError, '无法清除浏览器核对标记。'))
              return
            }
          }
          setLegacyBlocked(true)
          setDeploymentRecovery({ workflowId: saveResult.workflowId, message: '部署失败或结果未知，当前工作流需要人工核对。', storageAvailable: true, attemptToken: pendingToken })
          setPhase('error')
          setErrorMessage(`${extractErrorMessage(err, '部署修复失败')}；结果可能未知，请先人工核对 Git 和数据库，勿重试。`)
    } finally {
      finishWorkflowDeployment(saveResult.workflowId, pendingToken)
    }
  }, [diagnosisResult, saveResult, deployMutation, autoRun, handleRerun, legacyBlocked])

  const handleReset = useCallback(() => {
    setPhase('idle')
    setDiagnosisResult(null); setReview(null); setDeployConfirmation(null)
    setErrorMessage(null)
    setCustomPrompt('')
  }, [])

  const savedBaselineStale = phase === 'saved' && !!saveResult && !autoHealSaveIsCurrent(saveResult.receipt)
  const isFailed = ['failed', 'blocked', 'waiting'].includes(run.status)

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/40" onClick={onClose}>
      <div
        className={`flex h-full w-full ${review ? 'max-w-6xl' : 'max-w-2xl'} flex-col bg-white shadow-2xl`}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <PanelHeader run={run} onClose={onClose} />

        <PhaseIndicator phase={phase} />

        {/* Scrollable Content */}
        <div className="flex-1 overflow-y-auto p-6 space-y-5">
          {(legacyBlocked || workflowPending) && (
            <div role="alert" className="rounded border border-amber-300 p-3 text-sm">
              <p>{workflowPending ? '部署操作正在进行，不能清除核对标记或重复部署。' : '存在未确认的操作，或无法访问浏览器核对标记。为防止重复写入，请先人工核对 Git 和数据库；核对完成前，暂不能保存或部署。'}</p>
              {deploymentRecovery?.storageAvailable && !workflowPending && (
                <button
                  type="button"
                  onClick={openDeploymentRecovery}
                  className="mt-2 rounded border border-input bg-card px-3 py-1.5 text-xs text-foreground"
                >
                  我已人工核对，清除当前工作流标记…
                </button>
              )}
            </div>
          )}
          {databaseWritePhase !== 'clear' && <div role="alert" className="rounded border border-destructive p-3 text-sm text-destructive">{databaseWritePhase === 'pending' ? '数据库写入正在进行；请等待结果。' : '数据库写入结果未知；请人工核对，勿重试或将读取结果作为已确认基线。'}</div>}
          {savedBaselineStale && <div role="alert" className="rounded border border-destructive p-3 text-sm text-destructive">{autoHealSavedBaselineChangedMessage}</div>}
          {postSaveLocked && <div role="alert" className="rounded border border-destructive p-3 text-sm text-destructive">{postSaveLockMessage}</div>}
          {(phase === 'error' || phase === 'run-error' || phase === 'saved') && errorMessage && (
            <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3">
              <div className="flex items-center gap-2 mb-1">
                <svg className="h-4 w-4 text-red-500" fill="currentColor" viewBox="0 0 20 20">
                  <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.28 7.22a.75.75 0 00-1.06 1.06L8.94 10l-1.72 1.72a.75.75 0 101.06 1.06L10 11.06l1.72 1.72a.75.75 0 101.06-1.06L11.06 10l1.72-1.72a.75.75 0 00-1.06-1.06L10 8.94 8.28 7.22z" clipRule="evenodd" />
                </svg>
                <span className="font-medium text-red-800 text-sm">操作失败</span>
              </div>
              <p className="text-red-700 text-sm">{errorMessage}</p>
            </div>
          )}

          {/* Idle: Diagnosis Init */}
          {phase === 'idle' && (
            <IdleContent
              run={run}
              isFailed={isFailed}
              customPrompt={customPrompt}
              setCustomPrompt={setCustomPrompt}
              showCustomPrompt={showCustomPrompt}
              setShowCustomPrompt={setShowCustomPrompt}
              onDiagnose={handleDiagnose}
            />
          )}

          {phase === 'diagnosing' && <DiagnosingContent elapsed={diagnosingElapsed} />}

          {/* Diagnosed: Results */}
          {(phase === 'diagnosed' || phase === 'saving' || phase === 'saved' || phase === 'deploying' || phase === 'deployed' || phase === 'run-error' || phase === 'error') && diagnosisResult && (
            <DiagnosisResults
              result={diagnosisResult}
              diffExpanded={diffExpanded}
              setDiffExpanded={setDiffExpanded}
              errorChainExpanded={errorChainExpanded}
              setErrorChainExpanded={setErrorChainExpanded}
              suggestionsExpanded={suggestionsExpanded}
              setSuggestionsExpanded={setSuggestionsExpanded}
            />
          )}

          {(phase === 'diagnosed' || phase === 'error') && diagnosisResult?.fixedSpec && <div className="rounded border border-input bg-card p-3 text-sm">
            {saveMode === 'checking' && <p>正在确认宿主保存能力；完成前不能保存。</p>}
            {saveMode === 'unavailable' && <p role="alert">{capabilityError}</p>}
            {saveMode === 'failed' && <p role="alert">{capabilityError} 无法确认宿主是否支持统一保存；未发送保存。请重试或联系维护人员检查宿主能力接口。</p>}
            {saveMode === 'compatible' && <p>兼容保存的数据库和 Git 写入范围及并发保护由宿主决定，可能覆盖诊断后的更改；保存不等于部署。</p>}
            {saveMode === 'unified' && <p>保存前需要核对当前草稿和修复内容。诊断未提供可验证的源草稿身份。</p>}
          </div>}
          {review && <AutoHealSaveReview baseline={review.baseline} candidateJson={review.candidateJson}
            onConfirm={handleReviewConfirm} onCancel={() => setReview(null)} />}

          {/* Saving state */}
          {phase === 'saving' && (
            <div className="flex flex-col items-center py-12 text-gray-400">
              <div className="mb-3 h-10 w-10 animate-spin rounded-full border-3 border-gray-300 border-t-blue-600" />
              <p className="font-medium text-gray-600 text-sm">正在保存修复…</p>
              <p className="text-xs">{saveMode === 'unified' ? '正在读取基线或保存数据库与 Git，保存不会部署' : '正在通过宿主兼容接口保存；保存范围由宿主决定'}</p>
            </div>
          )}

          {/* Saved: deploy preview + autoRun toggle */}
          {phase === 'saved' && (
            <div className="space-y-4">
              <div className="rounded-lg border border-blue-200 bg-blue-50 px-5 py-4">
                <h3 className="font-semibold text-blue-800">修复已保存</h3>
                <p className="mt-1 text-blue-700 text-sm">
                  {(saveResult?.receipt as (AutoHealSaveResult & { unified?: boolean }) | undefined)?.unified
                    ? '数据库和 Git 已确认保存，尚未部署；部署前将重新核对保存回执和文件差异。'
                    : '宿主已确认保存，尚未部署；数据库和 Git 的实际保存范围由宿主决定。'}
                </p>
              </div>
              <div className="rounded-lg border border-gray-200 bg-white px-5 py-4">
                <label className="flex items-center justify-between">
                  <span className="text-sm font-medium text-gray-700">部署后自动重新运行</span>
                  <input
                    type="checkbox"
                    checked={autoRun}
                    onChange={(e) => setAutoRun(e.target.checked)}
                    className="h-4 w-4 rounded border-gray-300 text-blue-600 focus:ring-blue-500"
                  />
                </label>
                <p className="mt-1 text-xs text-gray-500">
                  {(saveResult?.receipt as (AutoHealSaveResult & { unified?: boolean }) | undefined)?.unified ? '开启后将重新运行已确认部署的快照。' : '开启后将重新运行服务端实际部署的草稿；其内容可能与本次修复不同。'}
                </p>
              </div>
            </div>
          )}

          {/* Deploying state */}
          {phase === 'deploying' && (
            <div className="flex flex-col items-center py-12 text-gray-400">
              <div className="mb-3 h-10 w-10 animate-spin rounded-full border-3 border-gray-300 border-t-blue-600" />
              <p className="font-medium text-gray-600 text-sm">正在部署修复…</p>
              <p className="text-xs">正在发布服务端读取的草稿</p>
            </div>
          )}

          {/* Deployed: Success state */}
          {(phase === 'deployed' || phase === 'run-error') && (
            <div className="rounded-lg border border-green-200 bg-green-50 px-5 py-4 text-center">
              <svg className="mx-auto mb-2 h-10 w-10 text-green-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
              <h3 className="font-semibold text-green-800">部署已完成</h3>
              <p className="mt-1 text-green-700 text-sm">服务端读取的草稿已部署；这不保证内容与本次修复相同。</p>
            </div>
          )}

          {/* Running */}
          {phase === 'running' && (
            <div className="flex flex-col items-center py-12 text-gray-400">
              <div className="mb-3 h-10 w-10 animate-spin rounded-full border-3 border-gray-300 border-t-blue-600" />
              <p className="font-medium text-gray-600 text-sm">正在重新运行工作流…</p>
              <p className="text-xs">请稍候</p>
            </div>
          )}
        </div>

        {deploymentRecovery && deploymentRecoveryOpen && !workflowPending && (
          <DeploymentRecoveryDialog
            workflowId={deploymentRecovery.workflowId}
            errorMessage={deploymentRecoveryError}
            onCancel={() => setDeploymentRecoveryOpen(false)}
            onConfirm={handleDeploymentRecoveryConfirm}
          />
        )}

        {deployConfirmation && phase === 'saved' && <AutoHealDeployConfirm receipt={deployConfirmation} onCancel={() => setDeployConfirmation(null)} onConfirm={snapshot => { const confirmed = deployConfirmation; setDeployConfirmation(null); if (confirmed === saveResult?.receipt) void handleDeploy(snapshot) }} />}
        <PanelFooter
          phase={phase}
          isFailed={isFailed}
          hasFixedSpec={!!diagnosisResult?.fixedSpec}
          onDiagnose={handleDiagnose}
          onSave={handleSave}
          onDeploy={() => { if (saveResult) setDeployConfirmation(saveResult.receipt) }}
          onRerun={handleRerun}
          onReset={handleReset}
          isDiagnosing={diagnoseMutation.isPending}
          isWriteBlocked={legacyBlocked || postSaveLocked || databaseWritePhase !== 'clear' || savedBaselineStale || (phase !== 'saved' && (saveMode === 'checking' || saveMode === 'failed' || saveMode === 'unavailable' || !!review))}
          savedBaselineStale={savedBaselineStale}
          isSaving={saveMutation.isPending}
          isDeploying={deployMutation.isPending}
          isRunning={runMutation.isPending}
        />
      </div>
    </div>
  )
}

interface IdleContentProps {
  run: FlowRun
  isFailed: boolean
  customPrompt: string
  setCustomPrompt: (v: string) => void
  showCustomPrompt: boolean
  setShowCustomPrompt: (v: boolean) => void
  onDiagnose: () => void
}

function IdleContent({ run, isFailed, customPrompt, setCustomPrompt, showCustomPrompt, setShowCustomPrompt, onDiagnose }: IdleContentProps) {
  if (!isFailed) {
    return (
      <div className="rounded-lg border border-amber-200 bg-amber-50 px-5 py-6 text-center">
        <svg className="mx-auto mb-2 h-8 w-8 text-amber-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.5L13.732 4c-.77-.833-1.964-.833-2.732 0L4.082 16.5c-.77.833.192 2.5 1.732 2.5z" />
        </svg>
        <p className="font-medium text-amber-800">当前运行状态为 <StatusBadge status={run.status} /></p>
        <p className="mt-1 text-amber-700 text-sm">自动修复仅支持失败（failed）、阻塞（blocked）或等待中（waiting）的工作流。</p>
      </div>
    )
  }

  return (
    <div className="space-y-4">
      <div className="rounded-lg border border-gray-200 bg-white px-5 py-4">
        <h3 className="font-medium text-gray-800 text-sm">运行概览</h3>
        <div className="mt-3 grid grid-cols-3 gap-4">
          <RunStat label="状态" value={<StatusBadge status={run.status} />} />
          <RunStat label="节点" value={`${run.node_count} 个`} />
          <RunStat label="失败" value={`${run.failed_count} 个`} valueClassName="text-red-600" />
          <RunStat label="成功" value={`${run.succeeded_count} 个`} valueClassName="text-green-600" />
          <RunStat label="耗时" value={run.total_duration_ms != null ? formatDuration(run.total_duration_ms) : '—'} />
          <RunStat label="触发" value={run.triggered_by || '—'} />
        </div>
      </div>

      {/* Custom Prompt Toggle */}
      <div>
        <button
          onClick={() => setShowCustomPrompt(!showCustomPrompt)}
          className="flex items-center gap-1.5 text-gray-600 text-xs hover:text-gray-800 transition-colors"
        >
          <svg
            className={`h-3 w-3 transition-transform ${showCustomPrompt ? 'rotate-90' : ''}`}
            fill="currentColor" viewBox="0 0 20 20"
          >
            <path fillRule="evenodd" d="M7.293 14.707a1 1 0 010-1.414L10.586 10 7.293 6.707a1 1 0 011.414-1.414l4 4a1 1 0 010 1.414l-4 4a1 1 0 01-1.414 0z" clipRule="evenodd" />
          </svg>
          <span className="font-medium">自定义诊断提示</span>
          <span className="text-gray-400">（可选）</span>
        </button>
        {showCustomPrompt && (
          <textarea
            value={customPrompt}
            onChange={(e) => setCustomPrompt(e.target.value)}
            placeholder="输入额外的诊断提示，例如：重点关注 API 调用超时问题…"
            rows={3}
            maxLength={2000}
            className="mt-2 w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500 resize-none"
          />
        )}
      </div>

      {/* What happens */}
      <div className="rounded-lg border border-blue-100 bg-blue-50/50 px-5 py-4">
        <h4 className="font-medium text-blue-800 text-sm mb-2">诊断流程说明</h4>
        <ol className="space-y-1.5 text-blue-700 text-xs">
          <li className="flex items-start gap-2">
            <span className="mt-0.5 shrink-0 rounded-full bg-blue-200 px-1.5 py-0.5 text-blue-800 text-[10px] font-bold">1</span>
            AI 读取运行数据和原始工作流 YAML
          </li>
          <li className="flex items-start gap-2">
            <span className="mt-0.5 shrink-0 rounded-full bg-blue-200 px-1.5 py-0.5 text-blue-800 text-[10px] font-bold">2</span>
            分析失败节点的错误链路和根因
          </li>
          <li className="flex items-start gap-2">
            <span className="mt-0.5 shrink-0 rounded-full bg-blue-200 px-1.5 py-0.5 text-blue-800 text-[10px] font-bold">3</span>
            生成修复后的 YAML 并展示变更对比
          </li>
          <li className="flex items-start gap-2">
            <span className="mt-0.5 shrink-0 rounded-full bg-blue-200 px-1.5 py-0.5 text-blue-800 text-[10px] font-bold">4</span>
            确认后一键应用修复，并可重新运行验证
          </li>
        </ol>
      </div>
    </div>
  )
}

function RunStat({ label, value, valueClassName = '' }: { label: string; value: React.ReactNode; valueClassName?: string }) {
  return (
    <div>
      <div className="text-gray-500 text-xs">{label}</div>
      <div className={`font-medium text-sm mt-0.5 ${valueClassName}`}>{value}</div>
    </div>
  )
}

function DiagnosingContent({ elapsed }: { elapsed: number }) {
  // Progress stages based on elapsed time
  const minutes = Math.floor(elapsed / 60)
  const seconds = elapsed % 60
  const timeStr = minutes > 0 ? `${minutes}分${seconds}秒` : `${seconds}秒`

  // Animate through stages
  const stage = elapsed < 10 ? 1 : elapsed < 30 ? 2 : elapsed < 60 ? 3 : 4

  return (
    <div className="flex flex-col items-center py-16 text-gray-400">
      <div className="mb-4 h-12 w-12 animate-spin rounded-full border-3 border-gray-300 border-t-blue-600" />
      <p className="font-medium text-gray-600 text-sm">AI 正在分析运行数据…</p>
      <p className="mt-1 text-xs">已等待 {timeStr}，BaaS Bot 异步处理中</p>
      <div className="mt-6 space-y-2 text-left w-full max-w-xs">
        <DiagnoseStep label="提交诊断请求" done />
        <DiagnoseStep label="读取工作流 YAML" done={stage >= 2} active={stage === 1} />
        <DiagnoseStep label="分析错误链路" done={stage >= 3} active={stage === 2} />
        <DiagnoseStep label="生成修复建议" done={stage >= 4} active={stage === 3} />
      </div>
      {elapsed > 30 && (
        <p className="mt-4 text-xs text-amber-500">诊断可能需要 1-5 分钟，请耐心等待…</p>
      )}
    </div>
  )
}

function DiagnoseStep({ label, active, done }: { label: string; active?: boolean; done?: boolean }) {
  return (
    <div className={`flex items-center gap-2 text-xs ${active ? 'text-blue-600 font-medium' : done ? 'text-green-600' : 'text-gray-400'}`}>
      {done && <span>✓</span>}
      {active && (
        <svg className="h-3 w-3 animate-spin" fill="none" viewBox="0 0 24 24">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
        </svg>
      )}
      {!done && !active && <span className="h-1 w-1 rounded-full bg-gray-300" />}
      <span>{label}</span>
    </div>
  )
}

interface DiagnosisResultsProps {
  result: AutoHealDiagnosisResult
  diffExpanded: boolean
  setDiffExpanded: (v: boolean) => void
  errorChainExpanded: boolean
  setErrorChainExpanded: (v: boolean) => void
  suggestionsExpanded: boolean
  setSuggestionsExpanded: (v: boolean) => void
}

function DiagnosisResults({ result, diffExpanded, setDiffExpanded, errorChainExpanded, setErrorChainExpanded, suggestionsExpanded, setSuggestionsExpanded }: DiagnosisResultsProps) {
  return (
    <div className="space-y-4">
      {/* Summary */}
      <div className="rounded-lg border border-blue-200 bg-blue-50 px-5 py-4">
        <h3 className="mb-1 font-semibold text-blue-900 text-sm">🔍 诊断摘要</h3>
        <p className="text-blue-800 text-sm leading-relaxed">{result.summary}</p>
      </div>

      {/* Error Chain */}
      {result.errorChain.length > 0 && (
        <CollapsibleCard
          title={`🐛 错误链路 (${result.errorChain.length})`}
          expanded={errorChainExpanded}
          onToggle={() => setErrorChainExpanded(!errorChainExpanded)}
        >
          <div className="space-y-2">
            {result.errorChain.map((item, i) => (
              <ErrorChainItem key={item.nodeId} item={item} index={i} />
            ))}
          </div>
        </CollapsibleCard>
      )}

      {/* Fix Suggestions */}
      {result.fixSuggestions.length > 0 && (
        <CollapsibleCard
          title={`💡 修复建议 (${result.fixSuggestions.length})`}
          expanded={suggestionsExpanded}
          onToggle={() => setSuggestionsExpanded(!suggestionsExpanded)}
        >
          <div className="space-y-2">
            {result.fixSuggestions.map((s, i) => (
              <FixSuggestionItem key={`${s.nodeId}-${s.field}`} suggestion={s} index={i} />
            ))}
          </div>
        </CollapsibleCard>
      )}

      {/* Yaml Diff */}
      {result.diff.length > 0 && result.fixedYaml && (
        <CollapsibleCard
          title={`📝 YAML 变更对比 (${result.diff.length} 处)`}
          expanded={diffExpanded}
          onToggle={() => setDiffExpanded(!diffExpanded)}
          defaultColor="indigo"
        >
          <DiffView diff={result.diff} />
          {/* Full YAML preview */}
          <details className="mt-3">
            <summary className="cursor-pointer text-gray-600 text-xs hover:text-gray-800">查看完整修复后 YAML</summary>
            <pre className="mt-2 max-h-64 overflow-auto rounded-md bg-gray-900 p-3 font-mono text-xs text-green-400 whitespace-pre-wrap">
              {result.fixedYaml}
            </pre>
          </details>
        </CollapsibleCard>
      )}

      {/* No Fix Available */}
      {!result.fixedYaml && (
        <div className="rounded-lg border border-amber-200 bg-amber-50 px-5 py-4 text-center">
          <p className="font-medium text-amber-800 text-sm">AI 未能生成可自动应用的修复</p>
          <p className="mt-1 text-amber-700 text-xs">请参考上方的错误链路和修复建议手动调整工作流。</p>
        </div>
      )}

      {/* Raw Response */}
      {result.rawResponse && (
        <details>
          <summary className="cursor-pointer text-gray-400 text-xs hover:text-gray-600">查看 AI 原始回复</summary>
          <pre className="mt-2 max-h-48 overflow-auto rounded-md bg-gray-50 p-3 font-mono text-xs text-gray-600 whitespace-pre-wrap border border-gray-200">
            {result.rawResponse}
          </pre>
        </details>
      )}
    </div>
  )
}

function ErrorChainItem({ item, index }: { item: AutoHealErrorChainItem; index: number }) {
  return (
    <div className="rounded-md border border-red-100 bg-red-50/50 px-4 py-3">
      <div className="flex items-center gap-2 mb-1">
        <span className="rounded-full bg-red-200 px-1.5 py-0.5 text-red-800 text-[10px] font-bold">{index + 1}</span>
        <span className="font-medium text-gray-900 text-sm">{item.nodeTitle || item.nodeId}</span>
        <span className="rounded bg-gray-100 px-1.5 py-0.5 text-gray-600 text-[10px] font-mono">{item.executorType}</span>
      </div>
      {item.errorText && (
        <pre className="mt-1 max-h-24 overflow-auto rounded bg-red-100 px-2.5 py-1.5 font-mono text-xs text-red-800 whitespace-pre-wrap">
          {item.errorText.length > 500 ? item.errorText.slice(0, 500) + '…' : item.errorText}
        </pre>
      )}
      {item.analysis && (
        <p className="mt-1.5 text-gray-700 text-xs leading-relaxed">{item.analysis}</p>
      )}
    </div>
  )
}

function FixSuggestionItem({ suggestion, index }: { suggestion: AutoHealFixSuggestion; index: number }) {
  return (
    <div className="rounded-md border border-amber-100 bg-amber-50/30 px-4 py-3">
      <div className="flex items-center gap-2 mb-1">
        <span className="rounded-full bg-amber-200 px-1.5 py-0.5 text-amber-800 text-[10px] font-bold">{index + 1}</span>
        <span className="font-mono text-gray-700 text-xs">{suggestion.nodeId}.{suggestion.field}</span>
      </div>
      {suggestion.oldValue != null && (
        <div className="flex items-center gap-2 text-xs">
          <span className="text-gray-500">原值：</span>
          <code className="rounded bg-red-100 px-1.5 py-0.5 text-red-700 font-mono text-[11px]">
            {truncate(String(suggestion.oldValue), 80)}
          </code>
        </div>
      )}
      {suggestion.newValue != null && (
        <div className="flex items-center gap-2 text-xs mt-0.5">
          <span className="text-gray-500">新值：</span>
          <code className="rounded bg-green-100 px-1.5 py-0.5 text-green-700 font-mono text-[11px]">
            {truncate(String(suggestion.newValue), 80)}
          </code>
        </div>
      )}
      <p className="mt-1 text-gray-600 text-xs">{suggestion.reason}</p>
    </div>
  )
}

function DiffView({ diff }: { diff: AutoHealDiffItem[] }) {
  return (
    <div className="space-y-1.5">
      {diff.map((item, i) => (
        <div key={i} className={`rounded-md border px-3 py-2 text-xs ${
          item.type === 'add' ? 'border-green-200 bg-green-50' :
          item.type === 'remove' ? 'border-red-200 bg-red-50' :
          'border-blue-200 bg-blue-50'
        }`}>
          <div className="flex items-center gap-2 mb-0.5">
            <span className={`rounded px-1 py-0.5 font-bold text-[10px] uppercase ${
              item.type === 'add' ? 'bg-green-200 text-green-800' :
              item.type === 'remove' ? 'bg-red-200 text-red-800' :
              'bg-blue-200 text-blue-800'
            }`}>
              {item.type === 'add' ? '新增' : item.type === 'remove' ? '删除' : '修改'}
            </span>
            <span className="font-mono text-gray-700">{item.path}</span>
          </div>
          {item.type === 'change' && (
            <div className="flex items-center gap-2 font-mono text-[11px]">
              <span className="text-red-700 line-through">{truncate(String(item.oldValue ?? ''), 60)}</span>
              <span className="text-gray-400">→</span>
              <span className="text-green-700">{truncate(String(item.newValue ?? ''), 60)}</span>
            </div>
          )}
          {item.type === 'add' && item.value && (
            <code className="text-green-700 font-mono text-[11px]">{truncate(String(item.value), 80)}</code>
          )}
          {item.type === 'remove' && item.oldValue && (
            <code className="text-red-700 font-mono text-[11px] line-through">{truncate(String(item.oldValue), 80)}</code>
          )}
        </div>
      ))}
    </div>
  )
}

function CollapsibleCard({ title, expanded, onToggle, defaultColor = 'gray', children }: {
  title: string
  expanded: boolean
  onToggle: () => void
  defaultColor?: 'gray' | 'indigo'
  children: React.ReactNode
}) {
  const borderColor = defaultColor === 'indigo' ? 'border-indigo-200' : 'border-gray-200'
  return (
    <div className={`rounded-lg border ${borderColor} bg-white shadow-sm`}>
      <button
        onClick={onToggle}
        className="flex w-full items-center justify-between px-5 py-3 text-left transition-colors hover:bg-gray-50"
      >
        <span className="font-semibold text-gray-900 text-sm">{title}</span>
        <svg
          className={`h-4 w-4 text-gray-400 transition-transform ${expanded ? 'rotate-180' : ''}`}
          fill="none" stroke="currentColor" viewBox="0 0 24 24"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
        </svg>
      </button>
      {expanded && <div className="border-t border-gray-100 px-5 py-4">{children}</div>}
    </div>
  )
}

interface PanelFooterProps {
  phase: Phase
  isFailed: boolean
  hasFixedSpec: boolean
  onDiagnose: () => void
  onSave: () => void
  onDeploy: () => void
  onRerun: () => void
  onReset: () => void
  isDiagnosing: boolean
  isWriteBlocked: boolean
  savedBaselineStale: boolean
  isSaving: boolean
  isDeploying: boolean
  isRunning: boolean
}

function PanelFooter({ phase, isFailed, hasFixedSpec, onDiagnose, onSave, onDeploy, onRerun, onReset, isDiagnosing, isWriteBlocked, savedBaselineStale, isSaving, isDeploying, isRunning }: PanelFooterProps) {
  return (
    <div className="border-t border-gray-200 bg-gray-50 px-6 py-4">
      <div className="flex items-center justify-between gap-3">
        <div>
          {(phase === 'diagnosed' || phase === 'deployed' || phase === 'run-error' || phase === 'error' || phase === 'saved' && savedBaselineStale) && (
            <button
              onClick={onReset}
              className="rounded-md border border-gray-300 bg-white px-4 py-2 text-sm font-medium text-gray-700 shadow-sm transition-colors hover:bg-gray-50"
            >
              重新诊断
            </button>
          )}
        </div>
        <div className="flex items-center gap-3">
          {/* Diagnose button */}
          {phase === 'idle' && isFailed && (
            <button
              onClick={onDiagnose}
              disabled={isDiagnosing}
              className="inline-flex items-center gap-2 rounded-md bg-blue-600 px-5 py-2 text-sm font-medium text-white shadow-sm transition-colors hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z" />
              </svg>
              开始诊断
            </button>
          )}

          {/* Save button */}
          {(phase === 'diagnosed' || (phase === 'error' && hasFixedSpec)) && (
            <button
              onClick={onSave}
              disabled={isSaving || isWriteBlocked || !hasFixedSpec}
              className="inline-flex items-center gap-2 rounded-md bg-orange-600 px-5 py-2 text-sm font-medium text-white shadow-sm transition-colors hover:bg-orange-700 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
              </svg>
              {isSaving ? '保存中…' : '保存修复'}
            </button>
          )}

          {/* Deploy button */}
          {phase === 'saved' && (
            <button
              onClick={onDeploy}
              disabled={isDeploying || isWriteBlocked}
              className="inline-flex items-center gap-2 rounded-md bg-indigo-600 px-5 py-2 text-sm font-medium text-white shadow-sm transition-colors hover:bg-indigo-700 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15.59 14.37a6 6 0 01-5.84 7.38v-4.8m5.84 2.58a14.98 14.98 0 006.16-12.7A14.98 14.98 0 009.631 8.41m5.96 5.04a14.92 14.92 0 00-6.16-12.7A14.98 14.98 0 003.151 8.41m5.96 5.04v4.8m-5.96-5.04a6 6 0 0010.03 4.18" />
              </svg>
              {isDeploying ? '部署中…' : '部署修复'}
            </button>
          )}

          {/* Rerun button */}
          {(phase === 'deployed' || phase === 'run-error') && (
            <button
              onClick={onRerun}
              disabled={isRunning}
              className="inline-flex items-center gap-2 rounded-md bg-green-600 px-5 py-2 text-sm font-medium text-white shadow-sm transition-colors hover:bg-green-700 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
              </svg>
              {isRunning ? '运行中…' : '重新运行'}
            </button>
          )}
        </div>
      </div>
    </div>
  )
}

/** Extract a human-readable error message from API errors.
 *  Handles `ApiError` format: "API 400: {"error":"Bad Request","message":"flowId is required"}"
 */
function extractErrorMessage(err: unknown, fallback: string): string {
  if (!(err instanceof Error)) return fallback
  const msg = err.message
  // Try to parse "API NNN: {...}" pattern from fetchJson's ApiError
  const jsonMatch = msg.match(/^API \d+:\s*(\{.*\})$/s)
  if (jsonMatch) {
    try {
      const parsed = JSON.parse(jsonMatch[1])
      if (parsed.message) return parsed.message
    } catch { /* not valid JSON, fall through */ }
  }
  return msg || fallback
}

function truncate(str: string, maxLen: number): string {
  return str.length > maxLen ? str.slice(0, maxLen) + '…' : str
}