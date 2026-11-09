import { revalidateGitComparison } from './revalidateGitComparison'
import { extractApiErrorMessage } from './editorErrorMessage'
import WorkflowSnapshotReconciliation from '../components/WorkflowSnapshotReconciliation'
import WorkflowBaselineComparison, { WorkflowBaselineNotice, parseGitWorkflowForImport } from '../components/WorkflowBaselineComparison'
import EditorDeployConfirmDialog, { type EditorDeployCandidate } from '../components/EditorDeployConfirmDialog'
import { useActiveHistoryMismatchReview } from '../components/useActiveHistoryMismatchReview'
import { assertWorkflowReconciliation, workflowReconciliationIsCurrent } from '../api/workflowSnapshotReconciliation'
import type { ReconciliationApproval } from '../api/workflowSnapshotReconciliation'
import { useEditorMergeReview } from './useEditorMergeReview'
import { bindEditorDeploymentWriteEpoch, isDeployPrePushConflict, isEditorDeploymentNotSent } from '../api/autoHealWrites'
import { assertDeploymentAvailable, clearEditorConfirmedDeployment, clearEditorReconciledDeployment, clearRejectedDeployment, editorDeploymentMarkerMatches, finishEditorDeployment, isEditorDeploymentPending, markEditorDeploymentPending, readEditorDeploymentMarker, subscribeWorkflowDeploymentPending, workflowDeploymentPendingRevision } from '../editor/deploymentGuard'
import { editorWorkflowSourceIsCurrent, readEditorWorkflowSource } from '../api/editorWorkflowSource'
import { editorDraftDocument, readEditorDraftCas } from '../api/editorDraftCas'
import { useState, useCallback, useContext, useEffect, useLayoutEffect, useRef, useSyncExternalStore } from 'react'
import { stringify as stringifyYaml } from 'yaml'
import { UNSAFE_LocationContext } from 'react-router-dom'
import { useEditorWorkflowDocument, useAutoHealDeploy } from '../api/hooks'
import { QueryClientContext } from '@tanstack/react-query'
import { createWorkflowPack } from '../editor/newWorkflowSave'
import { useEditorStore, type EditorState } from '../editor/store'
import WorkflowPackSidebar from '../components/WorkflowPackSidebar'
import DeploymentRecoveryDialog from '../components/DeploymentRecoveryDialog'
import EditorCanvas from '../components/EditorCanvas'
import NodePalette from '../components/NodePalette'
import NodePropertyPanel from '../components/NodePropertyPanel'
import WorkflowSettingsPanel from '../components/editor/WorkflowSettingsPanel'
import { normalizationChanges, parseWorkflowYaml } from '../editor/yamlDraft'
import { confirmDiscardEditor } from '../editor/navigation'
import { beginWorkflowTransition, commitWorkflowTransition } from '../editor/workflowTransition'
import YamlEditor from '../components/YamlEditor'
import ScriptFilesTab from '../components/ScriptFilesTab'
import { compatiblePackScope, hasPackVersionRisk, hasPackVersionEditRisk, hasPackVersionUpload, sessionFor, validateSnapshot, packVersionsRevision, subscribePackVersions, diff, publish } from '../editor/packVersionSession'
import { WorkspacePopPendingContext, isWorkflowPopOwned, subscribeWorkflowPopOwnership, usePackNavigationGuard, workflowPopOwnershipSnapshot } from '../editor/usePackNavigationGuard'
import { packWorkflow, sameExecutionSpec } from '../editor/packCanvas'
import { PackCanvasPartialSaveError, usePackCanvasSession } from '../editor/usePackCanvasSession'
import { compareGitDatabase, type BaselineComparison } from '../editor/workflowBaselineComparison'
import { WorkflowDocumentAcknowledgedError } from '../editor/workflowDocumentSave'
import { assertEditorWorkflowReadable, editorWorkflowReadIsCurrent } from '../editor/workflowWriteGuard'
import WorkflowHistoryPanel from '../components/WorkflowHistoryPanel'
import { formatCurrentSpecForDiff, formatSpecForDiff, historyComparisonUncertain, specsEqual } from '../components/WorkflowVersionDiff'
import { api, fetchJson } from '@avernet/clawweb-shared/web/api/client'
import type { WorkflowSpec, DeployHistoryItem, VersionSnapshot } from '@avernet/clawweb-shared/web/types'

type ViewMode = 'visual' | 'yaml' | 'split'
type SaveMessage = { type: 'success' | 'error'; text: string } | null
// Bind a workflow operation to the marker it created, across Editor remounts.
const deploymentTokens = new WeakMap<object, string>()


function hasUnsafePackEdits(id: string): boolean {
  const versions = sessionFor(id).state.versions
  return hasPackVersionEditRisk(id) || Object.values(versions).some(version => !version.snapshot && version.files.length > 0)
}

interface EditorProps {
  /** Embedded inside another page (e.g. 任务护航 workspace): hides the workflow sidebar and fills parent height. */
  embedded?: boolean
  /** Workflow to open — controlled by the parent in embedded mode. */
  initialWorkflowId?: string | null
  /** Workspace owns browser POP transitions and commits the editor session. */
  workspaceManagedPop?: boolean
}

export default function Editor({ embedded = false, initialWorkflowId, workspaceManagedPop = false }: EditorProps) {
  const [selectedWorkflowId, setSelectedWorkflowId] = useState<string | null>(() => {
    const live = useEditorStore.getState()
    return live.spec && live._savedSpec === null ? null : live.spec?.id ?? initialWorkflowId ?? null
  })
  const [showYamlImport, setShowYamlImport] = useState(false)
  const [yamlInput, setYamlInput] = useState('')
  const [showNewDialog, setShowNewDialog] = useState(false)
  const [newId, setNewId] = useState('')
  const [newTitle, setNewTitle] = useState('')
  const [showMore, setShowMore] = useState(false)
  const [viewMode, setViewMode] = useState<ViewMode>('visual')
  const [workspaceTab, setWorkspaceTab] = useState<'workflow' | 'scripts'>('workflow')
  const [saveMessage, setSaveMessage] = useState<SaveMessage>(null)
  const [workflowSaving, setWorkflowSaving] = useState(false)
  const workflowSaveInFlight = useRef(false)
  const [showHistory, setShowHistory] = useState(false)
  const [latestDeploy, setLatestDeploy] = useState<DeployHistoryItem | null>(null)
  const [deploymentBaselineStatus, setDeploymentBaselineStatus] = useState<'loading' | 'ready' | 'error'>('loading')
  const [activeSnapshot, setActiveSnapshot] = useState<VersionSnapshot | null>(null)
  const activeHistoryReview = useActiveHistoryMismatchReview(selectedWorkflowId)
  const [deployCandidate, setDeployCandidate] = useState<EditorDeployCandidate | null>(null)
  const candidateRequest = useRef(0)
  const switchRequest = useRef(0)
  // Invalidate before passive effects or pending promises can apply an obsolete
  // controlled target, including when the new target is current, null or undefined.
  useLayoutEffect(() => { switchRequest.current++ }, [initialWorkflowId])
  useEffect(() => () => { switchRequest.current++ }, [])
  const selectedWorkflowRef = useRef(selectedWorkflowId)
  selectedWorkflowRef.current = selectedWorkflowId
  const navigationType = useContext(UNSAFE_LocationContext)?.navigationType
  const [deploymentBaselineRevision, setDeploymentBaselineRevision] = useState(0)
  const [showDeployConfirm, setShowDeployConfirm] = useState(false)
  const [deployReviewed, setDeployReviewed] = useState(false)
  const [deploymentRecovery, setDeploymentRecovery] = useState<{ workflowId: string; sessionId: number; message: string } | null>(null)
  const [deploymentRecoveryError, setDeploymentRecoveryError] = useState<string | null>(null)
  const [deploymentRecoveryOpen, setDeploymentRecoveryOpen] = useState(false)
  const recoveryTarget = useRef<{ workflowId: string; token: string; requestId: string | undefined } | null>(null)
  const [showWorkflowSettings, setShowWorkflowSettings] = useState(() =>
    Object.keys(useEditorStore.getState().pendingEdits ?? {}).some(key => key.startsWith('workflow:')))
  const [recoveredDrafts] = useState(() => Object.keys(useEditorStore.getState().pendingEdits ?? {}).length > 0)


  const { spec, isDirty, isLayoutDirty, selectedNodeId, validationErrors, loadSpec, importYaml, selectNode,
    sessionId: editorSessionId, _databaseSpec: databaseBaseline, pendingEdits = {}, actionError, setActionError, canUndo, canRedo, undo, redo } = useEditorStore()
  const creation = spec ? sessionFor(spec.id).state.creation : undefined
  const hasPendingEdits = Object.keys(pendingEdits).length > 0
  const packRevision = useSyncExternalStore(subscribePackVersions, packVersionsRevision, packVersionsRevision)
  const pendingRevision = useSyncExternalStore(subscribeWorkflowDeploymentPending, workflowDeploymentPendingRevision, workflowDeploymentPendingRevision)
  const workspacePopPending = useContext(WorkspacePopPendingContext)
  const workflowPopOwned = useSyncExternalStore(subscribeWorkflowPopOwnership, workflowPopOwnershipSnapshot, () => false)
  const packRisk = hasPackVersionRisk(selectedWorkflowId)
  const packUploading = hasPackVersionUpload(selectedWorkflowId)
  usePackNavigationGuard(!workspaceManagedPop)

  const { data: workflowSpec, databaseSpec: workflowDatabaseSpec, activeDeployNumber: workflowActiveDeployNumber, isLoading: workflowLoading, error: workflowReadError } = useEditorWorkflowDocument(selectedWorkflowId ?? '', editorSessionId)

  const packCanvas = usePackCanvasSession(selectedWorkflowId, workflowDatabaseSpec)
  const { mergeAdapter, visibleMerge, mergeCurrent, wantsReview, preparing, openMergeReview, cancelMergeReview, clearMergeReview } = useEditorMergeReview(selectedWorkflowId, editorSessionId, packCanvas, workflowSaveInFlight, setWorkflowSaving, setSaveMessage, extractApiErrorMessage)
  // The combined-save message may add DB partial-success context to the same
  // Pack error. Show that richer message once, without hiding unrelated errors.
  const packErrors = [...new Set([packCanvas.error, packCanvas.versionError])].filter((error): error is string =>
    !!error && !(saveMessage?.type === 'error' && saveMessage.text.includes(error)))
  const deployment = selectedWorkflowId ? sessionFor(selectedWorkflowId).state.deployment : null
  const visibleDeployment = deployment
  const deploymentPending = !!selectedWorkflowId && isEditorDeploymentPending(selectedWorkflowId)
  const visibleRecovery = deploymentRecovery?.sessionId === editorSessionId ? deploymentRecovery : null
  useEffect(() => {
    if (!selectedWorkflowId || isEditorDeploymentPending(selectedWorkflowId)) { setDeploymentRecovery(null); return }
    // A workflow-level deployment has its own status and recovery control.
    if (sessionFor(selectedWorkflowId).state.deployment) return
    try { assertDeploymentAvailable(selectedWorkflowId); setDeploymentRecovery(null) }
    catch (error) { setDeploymentRecovery({ workflowId: selectedWorkflowId, sessionId: editorSessionId, message: extractApiErrorMessage(error) }) }
  }, [selectedWorkflowId, editorSessionId, packRevision, pendingRevision])
  const queryClient = useContext(QueryClientContext)
  const deployMutation = useAutoHealDeploy({ editorGuard: true })
  const deployInFlight = useRef(false)

  const handleSelectWorkflow = useCallback(async (workflowId: string) => {
    if (workflowId === selectedWorkflowId) return
    const request = ++switchRequest.current
    const approval = beginWorkflowTransition()
    if (!approval) return
    try {
      const { spec: target, databaseSpec, activeDeployNumber } = await readEditorWorkflowSource(workflowId)
      if (request !== switchRequest.current) return
      if (!commitWorkflowTransition(approval, target, workflowId, false, databaseSpec, activeDeployNumber)) {
        setSaveMessage({ type: 'error', text: '工作流切换未完成：目标无效或确认后编辑内容已变化；原草稿仍保留。' })
        return
      }
      setShowWorkflowSettings(false)
      setSelectedWorkflowId(workflowId)
      setSaveMessage(null)
    } catch (error) {
      if (request === switchRequest.current) setSaveMessage({ type: 'error', text: `工作流读取失败，原草稿仍保留：${extractApiErrorMessage(error)}` })
    }
  }, [selectedWorkflowId, setSaveMessage])

  // A controlled target is an intention until its source is read and committed.
  // Workspace may already have committed the same target as part of its transaction.
  useEffect(() => {
    if (initialWorkflowId === undefined || initialWorkflowId === selectedWorkflowId) return
    if (!initialWorkflowId) { setSaveMessage({ type: 'error', text: '目标工作流不可用；原草稿仍保留。' }); return }
    // A browser POP is handled by WorkflowWorkspace, which reads and commits
    // the target Pack transactionally. Do not start a second controlled-editor
    // transition after React Router has already rendered the POP target. Once
    // the workspace commits, the store identity matches and the branch above
    // synchronizes the local selection.
    if (useEditorStore.getState().spec?.id === initialWorkflowId) {
      setSelectedWorkflowId(initialWorkflowId)
      setSaveMessage(null)
      setShowWorkflowSettings(false)
      return
    }
    if (workspacePopPending || workflowPopOwned || isWorkflowPopOwned()) return
    void handleSelectWorkflow(initialWorkflowId)
  }, [initialWorkflowId, navigationType, workflowPopOwned, workspaceManagedPop, workspacePopPending]) // eslint-disable-line react-hooks/exhaustive-deps

  // Only the committed selection may supply the initial loaded document.
  const currentSpecId = spec?.id
  useEffect(() => {
    if (workflowSpec && workflowSpec.id === selectedWorkflowId && editorWorkflowSourceIsCurrent(workflowSpec, selectedWorkflowId) && !currentSpecId && !useEditorStore.getState().spec) loadSpec(workflowSpec, workflowDatabaseSpec, workflowActiveDeployNumber)
  }, [workflowSpec, workflowDatabaseSpec, workflowActiveDeployNumber, currentSpecId, selectedWorkflowId, loadSpec])

  // Fetch the latest deploy history row to show the current effective version.
  useEffect(() => {
    if (!selectedWorkflowId) {
      setLatestDeploy(null)
      setDeploymentBaselineStatus('ready')
      return
    }
    setLatestDeploy(null)
    setDeploymentBaselineStatus('loading')
    let cancelled = false
    api.workflows
      .getHistory(selectedWorkflowId, 50, true)
      .then((r) => {
        if (cancelled) return
        const active = r.active ?? null
        setLatestDeploy(active)
        setDeploymentBaselineStatus('ready')
      })
      .catch(() => {
        if (!cancelled) {
          setLatestDeploy(null)
          setDeploymentBaselineStatus('error')
        }
      })
    return () => {
      cancelled = true
    }
  }, [selectedWorkflowId, deploymentBaselineRevision])

  // Deployment runs in the Bot conversation. When the editor regains focus,
  // refresh only the comparison baseline so a completed external deployment is
  // reflected without reloading the user's in-progress canvas edits.
  useEffect(() => {
    if (!selectedWorkflowId) return
    const refreshBaselineOnFocus = () => setDeploymentBaselineRevision((revision) => revision + 1)
    window.addEventListener('focus', refreshBaselineOnFocus)
    return () => window.removeEventListener('focus', refreshBaselineOnFocus)
  }, [selectedWorkflowId])

  useEffect(() => {
    if (!selectedWorkflowId || !latestDeploy) {
      setActiveSnapshot(null)
      return
    }
    let cancelled = false
    api.workflows
      .getDeploySnapshot(selectedWorkflowId, latestDeploy.deployNumber)
      .then((snapshot) => {
        if (!cancelled) setActiveSnapshot(snapshot)
      })
      .catch(() => {
        if (!cancelled) setActiveSnapshot(null)
      })
    return () => {
      cancelled = true
    }
  }, [selectedWorkflowId, latestDeploy])

  const effectiveActiveHistoryText = activeHistoryReview.displayText(activeSnapshot?.specJson)
  const deployedSpecText = effectiveActiveHistoryText === undefined ? '' : formatSpecForDiff(effectiveActiveHistoryText)
  const savedSpecText = databaseBaseline ? formatCurrentSpecForDiff(databaseBaseline) : ''
  const comparisonUncertain = Boolean(effectiveActiveHistoryText !== undefined && databaseBaseline && historyComparisonUncertain(effectiveActiveHistoryText))
  const hasUndeployedChanges = Boolean(effectiveActiveHistoryText !== undefined && databaseBaseline && !comparisonUncertain && !specsEqual(deployedSpecText, savedSpecText))
  const deploymentBaselineLabel = '生效'

  const handleOpenSaveDialog = useCallback(async () => {
    if (!useEditorStore.getState().spec) return
    const sessionId = useEditorStore.getState().sessionId
    const workflowId = selectedWorkflowId
    if (workflowSaveInFlight.current) return
    workflowSaveInFlight.current = true; setWorkflowSaving(true)
    setSaveMessage(null)
    try {
      if (!workflowId) {
        const live = useEditorStore.getState()
        if (!live.spec) return
        if (Object.keys(live.pendingEdits ?? {}).length) throw new Error('请先应用或放弃未完成的配置草稿。')
        if (live.validationErrors.length) throw new Error(`保存前请修复验证错误：${live.validationErrors[0].message}`)
        const submitted = structuredClone(live.spec)
        assertEditorWorkflowReadable(submitted.id)
        const created = await createWorkflowPack(submitted)
        for (const key of [['workflow-types'], ['editor-workflow-document', submitted.id], ['db-workflows'], ['db-workflow', submitted.id], ['facade-bindings']]) {
          void queryClient?.invalidateQueries({ queryKey: key })
        }
        const current = useEditorStore.getState()
        if (selectedWorkflowRef.current !== null || current.sessionId !== sessionId || current.spec?.id !== submitted.id) {
          setSaveMessage({ type: 'success', text: `工作流 ${submitted.id} 已创建；当前编辑会话未被替换。` })
          return
        }
        if (!created.databaseSpec || !current.completeSave(created.requestedSpec, created.requestedSpec, sessionId, created.databaseSpec)) throw new Error('创建成功，但编辑器会话无法更新；草稿仍保留。')
        setSelectedWorkflowId(submitted.id)
        setSaveMessage({ type: 'success', text: `已创建并保存为保存版本 commit ${created.result.commit}，尚未部署。保存期间的后续修改仍保留。` })
        return
      }
      assertEditorWorkflowReadable(workflowId)
      const result = await packCanvas.save()
      // Direct document save bypasses useAutoHealSave, so keep its cache
      // contract: a persisted draft must invalidate editor/list sources.
      if (result?.dbSaved) {
        for (const key of [['editor-workflow-document', workflowId], ['db-workflows'], ['workflow-types']]) {
          void queryClient?.invalidateQueries({ queryKey: key })
        }
      }
      if (selectedWorkflowRef.current !== workflowId || useEditorStore.getState().sessionId !== sessionId) return
      clearMergeReview()
      if (result) setSaveMessage({ type: 'success', text: result.mode === 'unified'
        ? `数据库和草稿(wf)分支已确认保存工作流及文件，commit ${result.commit}。尚未部署。保存期间的新改动仍需再次保存；节点位置仅在当前会话保留。`
        : !result.dbSaved && !result.gitSaved && !result.remainingFileChanges
          ? '没有需要保存的更改。'
        : `${result.dbSaved ? '工作流已保存到数据库。' : '本次未更新数据库工作流。'}${result.gitSaved ? `部分文件已保存为保存版本 commit ${result.commit}。` : '本次未更新 Git 文件。'}本次未将数据库中的工作流编排写入 Git 工作流 YAML；旧路径未更新 Git 工作流 YAML。${result.remainingFileChanges ? '仍有文件修改未保存，关闭或刷新页面会丢失；请先导出或处理。' : ''}尚未部署。保存期间的后续修改仍需再次保存。` })
      else if (workflowId) {
        const state = sessionFor(workflowId).state
        const error = state.versions[state.version].error
        if (error && useEditorStore.getState().sessionId === sessionId) setSaveMessage({ type: 'error', text: error })
      }
    } catch (cause) {
      // A verified or possibly committed write makes even an in-flight old read
      // unsafe. Isolate it before invalidating, including after navigation.
      if (workflowId && (cause instanceof PackCanvasPartialSaveError || cause instanceof WorkflowDocumentAcknowledgedError) && cause.workflowId === workflowId) {
        for (const key of [['editor-workflow-document', workflowId], ['db-workflows'], ['workflow-types']]) {
          void queryClient?.invalidateQueries({ queryKey: key })
        }
      }
      if (!workflowId || (selectedWorkflowRef.current === workflowId && useEditorStore.getState().sessionId === sessionId)) {
        setSaveMessage({ type: 'error', text: cause instanceof Error ? cause.message : '保存未完成，本地草稿仍保留。' })
      }
    } finally {
      workflowSaveInFlight.current = false; setWorkflowSaving(false)
    }
  }, [selectedWorkflowId, packCanvas, queryClient, setSaveMessage, clearMergeReview])

  const confirmMergeReview = async (approval: ReconciliationApproval) => {
    if (!visibleMerge || !mergeAdapter || workflowSaveInFlight.current) return
    const { workflowId, sessionId, review } = visibleMerge
    workflowSaveInFlight.current = true; setWorkflowSaving(true)
    try {
      if (selectedWorkflowRef.current !== workflowId || useEditorStore.getState().sessionId !== sessionId) throw new Error('编辑会话已变化；未发送保存。')
      assertWorkflowReconciliation(approval, mergeAdapter.currentReconciliation(review), approval.specJson)
      const result = await mergeAdapter.saveReconciled(approval)
      if (result.mode !== 'unified' || !result.dbSaved || !result.gitSaved) throw new Error('审阅结果未通过统一保存；请核对数据库与 Git。')
      if (result.dbSaved) for (const key of [['editor-workflow-document', workflowId], ['db-workflows'], ['workflow-types']]) void queryClient?.invalidateQueries({ queryKey: key })
      if (selectedWorkflowRef.current === workflowId && useEditorStore.getState().sessionId === sessionId) {
        setSaveMessage({ type: 'success', text: `数据库和草稿(wf)分支已确认保存工作流及文件，commit ${result.commit}。尚未部署。` })
      }
      clearMergeReview()
    } catch (cause) {
      if (cause instanceof PackCanvasPartialSaveError && cause.workflowId === workflowId) for (const key of [['editor-workflow-document', workflowId], ['db-workflows'], ['workflow-types']]) void queryClient?.invalidateQueries({ queryKey: key })
      if (selectedWorkflowRef.current === workflowId && useEditorStore.getState().sessionId === sessionId) setSaveMessage({ type: 'error', text: extractApiErrorMessage(cause) })
      clearMergeReview()
    } finally { workflowSaveInFlight.current = false; setWorkflowSaving(false) }
  }

  const applyYamlToEditor = useCallback((incoming: WorkflowSpec) => {
    const previousId = useEditorStore.getState().spec?.id
    useEditorStore.getState().setActionError(null)
    importYaml(incoming)
    const failure = useEditorStore.getState().actionError
    if (failure) throw new Error(failure)
    if (incoming.id !== previousId) setSelectedWorkflowId(null)
  }, [importYaml])

  const useGitFromComparison = async () => {
    if (!visibleMerge || !mergeAdapter || workflowSaveInFlight.current) return
    const { workflowId, sessionId, review } = visibleMerge
    const current = () => selectedWorkflowRef.current === workflowId && useEditorStore.getState().sessionId === sessionId
      && workflowReconciliationIsCurrent(review, mergeAdapter.currentReconciliation(review))
    workflowSaveInFlight.current = true; setWorkflowSaving(true)
    try {
      const epoch = assertEditorWorkflowReadable(workflowId)
      if (!current()) throw new Error('对比期间工作流或文件已变化，请关闭后重新查看差异。')
      const gitSpec = parseGitWorkflowForImport(review.gitYaml, workflowId)
      await revalidateGitComparison(review)
      if (!editorWorkflowReadIsCurrent(workflowId, epoch) || !current()) throw new Error('对比期间工作流或文件已变化，请关闭后重新查看差异。')
      cancelMergeReview()
      applyYamlToEditor(gitSpec)
      setSaveMessage({ type: 'success', text: 'Git 工作流内容已载入编辑器；请按需保存，尚未部署。' })
    } catch (cause) {
      cancelMergeReview()
      if (selectedWorkflowRef.current === workflowId && useEditorStore.getState().sessionId === sessionId) setSaveMessage({ type: 'error', text: `无法载入 Git 工作流：${extractApiErrorMessage(cause)}` })
    } finally { workflowSaveInFlight.current = false; setWorkflowSaving(false) }
  }

  const handleYamlImport = useCallback(() => {
    try {
      const parsed = parseWorkflowYaml(yamlInput)
      if (!confirmDiscardEditor()) return
      importYaml(parsed)
      setShowYamlImport(false)
      setYamlInput('')
      setSelectedWorkflowId(null)
      setSaveMessage(null)
    } catch (err) {
      setSaveMessage({ type: 'error', text: `YAML parse error: ${err instanceof Error ? err.message : 'Invalid YAML'}` })
    }
  }, [yamlInput, importYaml, setSaveMessage])

  const handleImportFile = useCallback(() => {
    const input = document.createElement('input')
    input.type = 'file'
    input.accept = '.yaml,.yml'
    input.onchange = (e) => {
      const file = (e.target as HTMLInputElement).files?.[0]
      if (!file) return
      const reader = new FileReader()
      reader.onload = (ev) => {
        const text = ev.target?.result as string
        setYamlInput(text)
        setShowYamlImport(true)
      }
      reader.readAsText(file)
    }
    input.click()
  }, [])

  const handleYamlExport = useCallback(() => {
    if (!spec) return
    if (hasPendingEdits) { setSaveMessage({ type: 'error', text: '请先应用或放弃配置草稿，再导出。' }); return }
    const yaml = stringifyYaml(spec, { lineWidth: 0 })
    const filename = `${spec.id || 'workflow'}.yaml`
    const blob = new Blob([yaml], { type: 'application/x-yaml' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = filename
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
    setSaveMessage({ type: 'success', text: `已请求下载 ${filename}；请检查浏览器下载结果。该文件只包含已应用的画布工作流 spec，不包含 Pack 版本文件（包括 YAML、脚本和资源）、未应用草稿或画布节点位置。` })
    setTimeout(() => setSaveMessage(null), 2000)
  }, [spec, hasPendingEdits, setSaveMessage])

  const handleCreateNew = useCallback(() => {
    const id = newId.trim(), title = newTitle.trim()
    if (!id || !title) return
    switchRequest.current++
    const approval = beginWorkflowTransition()
    if (!approval) return
    const target: WorkflowSpec = { id, title, version: '1.0.0', nodes: [{ id: `${id}_start`, title: '开始', phase: 'execute', executor: { type: 'done' } }] }
    if (!commitWorkflowTransition(approval, target, id, true)) {
      setSaveMessage({ type: 'error', text: '新建未完成：目标无效或确认后编辑内容已变化；原草稿仍保留。' })
      return
    }
    setShowNewDialog(false)
    setNewId('')
    setNewTitle('')
    setSelectedWorkflowId(null)
    setSaveMessage(null)
  }, [newId, newTitle, setSaveMessage])

  // Deploy the explicitly confirmed saved tree without rewriting its workflow YAML.
  const runDeploy = useCallback(async () => {
    if (!selectedWorkflowId || !deployCandidate || !deployReviewed || deployInFlight.current) return
    const id = selectedWorkflowId, candidate = deployCandidate, session = sessionFor(id)
    const live = useEditorStore.getState()
    const isOriginalSession = () => selectedWorkflowRef.current === id && useEditorStore.getState().sessionId === candidate.sessionId
    if (session.state.deployment || hasUnsafePackEdits(id) || candidate.packRevision !== packVersionsRevision()
      || candidate.workflowId !== id || live.sessionId !== candidate.sessionId || normalizationChanges(candidate.positions, live.positions).length > 0
      || normalizationChanges(candidate.editorSpec, live.spec).length || normalizationChanges(candidate.editorPending, live.pendingEdits).length || !live._savedSpec) {
      setSaveMessage({ type: 'error', text: '确认期间编辑内容已变化，请先保存并重新确认部署。' }); return
    }
    deployInFlight.current = true
    try {
      if (!editorWorkflowReadIsCurrent(id, candidate.writeEpoch)) throw new Error('数据库写入状态已变化，请先核对并重新确认部署。')
      const receipt = await readEditorDraftCas(id)
      if (!editorWorkflowReadIsCurrent(id, candidate.writeEpoch)) throw new Error('数据库写入状态已变化，请先核对并重新确认部署。')
      const db = editorDraftDocument(receipt.specJson, id)
      if (receipt.draftToken !== candidate.draftToken || (receipt.packId ?? id) !== candidate.packId) throw new Error('数据库草稿或本地编辑已变化，请重新读取并确认。')
      const now = useEditorStore.getState()
      if (selectedWorkflowRef.current !== id || now.sessionId !== candidate.sessionId || normalizationChanges(candidate.positions, now.positions).length > 0
        || normalizationChanges(candidate.editorSpec, now.spec).length || normalizationChanges(candidate.editorPending, now.pendingEdits).length || hasUnsafePackEdits(id)
        || candidate.packRevision !== packVersionsRevision() || !now._savedSpec || !sameExecutionSpec(db, now._savedSpec)) {
        throw new Error('数据库草稿或本地编辑已变化，请重新读取并确认。')
      }
      const input = { workflowId: id, packId: candidate.packId, candidateCommit: candidate.commit, expectedActiveCommit: candidate.expectedActiveCommit, expectedDraftToken: candidate.draftToken,
        requestId: crypto.randomUUID(), note: 'editor deploy', autoRun: false, snapshotProtocol: 'saved-tree-v2' as const, confirmedFileDiff: true as const }
      bindEditorDeploymentWriteEpoch(input, candidate.writeEpoch)
      const pendingToken = markEditorDeploymentPending(id)
      deploymentTokens.set(input, pendingToken)
      publish(session, { ...session.state, deployment: { input, pending: true, error: null } })
      const ownsAttempt = () => session.state.deployment?.input.requestId === input.requestId && editorDeploymentMarkerMatches(id, pendingToken)
      try {
        const result = await deployMutation.mutateAsync(input)
        if (!result || result.ok !== true || result.deployed !== true || result.workflowId !== id || result.packId !== candidate.packId
          || !Number.isSafeInteger(result.deployNumber) || result.deployNumber < 1 || !Number.isSafeInteger(result.version)
          || result.version < 1 || typeof result.tagName !== 'string' || !result.tagName.trim()
          || !('snapshotProtocol' in result) || result.snapshotProtocol !== 'saved-tree-v2' || !('candidateCommit' in result) || result.candidateCommit !== candidate.commit || !('expectedActiveCommit' in result) || result.expectedActiveCommit !== candidate.expectedActiveCommit) throw new Error('部署响应无法验证')
        if (!ownsAttempt()) return
        try { clearEditorConfirmedDeployment(id, pendingToken) } catch (err) {
          const markerClearFailure = extractApiErrorMessage(err)
          if (ownsAttempt()) publish(session, { ...session.state, deployment: { input, pending: false, error: markerClearFailure } })
          if (isOriginalSession()) setDeploymentRecovery({ workflowId: id, sessionId: candidate.sessionId, message: markerClearFailure })
          return
        }
        publish(session, { ...session.state, deployment: null })
        if (isOriginalSession()) {
          setSaveMessage({ type: 'success', text: `部署成功：deploy #${result.deployNumber} · v${result.version} · ${result.tagName}` })
          setDeploymentBaselineRevision(revision => revision + 1)
        }
      } catch (err) {
        if (!ownsAttempt()) return
        if (isDeployPrePushConflict(err, input) || isEditorDeploymentNotSent(err, input)) {
          try {
            if (session.state.deployment?.input.requestId !== input.requestId) throw new Error('部署标记已变化')
            clearRejectedDeployment(id, pendingToken)
            publish(session, { ...session.state, deployment: null })
            if (isOriginalSession()) {
              setDeploymentRecovery(current => current?.workflowId === id && current.sessionId === candidate.sessionId ? null : current)
              setSaveMessage({ type: 'error', text: extractApiErrorMessage(err) })
            }
            return
          } catch (clearError) {
            const message = extractApiErrorMessage(clearError)
            if (ownsAttempt()) publish(session, { ...session.state, deployment: { input, pending: false, error: message } })
            if (isOriginalSession()) setDeploymentRecovery({ workflowId: id, sessionId: candidate.sessionId, message })
            return
          }
        }
        const failureMessage = `部署失败或结果未知：${extractApiErrorMessage(err)}。请人工核对 Git 和数据库；没有日志恢复或安全自动重试。`
        publish(session, { ...session.state, deployment: { input, pending: false, error: failureMessage } })
        if (isOriginalSession()) setDeploymentRecovery({ workflowId: id, sessionId: candidate.sessionId, message: failureMessage })
      } finally {
        finishEditorDeployment(id, pendingToken)
        // If ownership was lost, surface the still-blocked state as unknown,
        // without overwriting the request or marker now owned by another attempt.
        publish(session, { ...session.state })
      }
    } catch (err) {
      if (isOriginalSession()) {
        setSaveMessage({ type: 'error', text: `部署已阻止：${extractApiErrorMessage(err)}` })
      }
    } finally { deployInFlight.current = false }
  }, [selectedWorkflowId, deployCandidate, deployReviewed, deployMutation, setSaveMessage])

  const openDeploymentRecovery = useCallback(() => {
    if (!selectedWorkflowId || isEditorDeploymentPending(selectedWorkflowId)) return
    try {
      const marker = readEditorDeploymentMarker(selectedWorkflowId)
      if (!marker) throw new Error('核对标记不存在或无法读取，请重新检查。')
      const current = sessionFor(selectedWorkflowId).state.deployment
      if (current && deploymentTokens.get(current.input) !== marker) throw new Error('部署请求与核对标记不匹配，请人工核对最新请求；不能清除此标记。')
      const requestId = current?.input.requestId
      recoveryTarget.current = { workflowId: selectedWorkflowId, token: marker, requestId }
      setDeploymentRecovery({ workflowId: selectedWorkflowId, sessionId: useEditorStore.getState().sessionId, message: '请先人工核对 Git 和数据库。' })
      setDeploymentRecoveryError(null); setSaveMessage(null); setDeploymentRecoveryOpen(true)
    } catch (error) { setSaveMessage({ type: 'error', text: extractApiErrorMessage(error) }) }
  }, [selectedWorkflowId])

  const handleDeploymentRecoveryConfirm = useCallback(() => {
    const target = recoveryTarget.current
    if (!visibleRecovery || !target || target.workflowId !== visibleRecovery.workflowId) return
    setDeploymentRecoveryError(null)
    try {
      const session = sessionFor(target.workflowId)
      if (isEditorDeploymentPending(target.workflowId)) throw new Error('部署操作正在进行，不能清除核对标记。')
      if (session.state.deployment?.input.requestId !== target.requestId) throw new Error('部署状态已变化，请重新核对。')
      clearEditorReconciledDeployment(target.workflowId, target.token)
      if (session.state.deployment) publish(session, { ...session.state, deployment: null })
    } catch (error) {
      setDeploymentRecoveryError(extractApiErrorMessage(error)); return
    }
    recoveryTarget.current = null
    setDeploymentRecovery(null); setDeploymentRecoveryOpen(false)
    setSaveMessage({ type: 'success', text: `已清除工作流 ${target.workflowId} 的浏览器核对标记。请基于已核对的 Git 和数据库状态重新确认部署。` })
  }, [visibleRecovery, setSaveMessage])

  const handleDeployClick = useCallback(async () => {
    if (!selectedWorkflowId || deployInFlight.current) return
    if (isEditorDeploymentPending(selectedWorkflowId)) { setSaveMessage({ type: 'error', text: '部署操作正在进行，不能重复部署。' }); return }
    try { assertDeploymentAvailable(selectedWorkflowId) } catch (err) {
      setDeploymentRecovery({ workflowId: selectedWorkflowId, sessionId: useEditorStore.getState().sessionId, message: extractApiErrorMessage(err) })
      setSaveMessage(null)
      return
    }
    if (sessionFor(selectedWorkflowId).state.deployment) { setSaveMessage({ type: 'error', text: '上次部署结果未知，请人工核对 Git 与数据库。' }); return }
    if (hasUnsafePackEdits(selectedWorkflowId)) { setSaveMessage({ type: 'error', text: '版本文件存在未保存更改或保存状态不确定，请先处理后再部署。' }); return }
    if (useEditorStore.getState()._databaseSpec === null) { setSaveMessage({ type: 'error', text: '尚无数据库暂存稿，请先保存当前编排，再确认部署。' }); return }
    const requestSequence = ++candidateRequest.current
    const { sessionId, positions: currentPositions, spec: currentSpec, pendingEdits: currentPending } = useEditorStore.getState()
    const positions = structuredClone(currentPositions)
    const editorSpec = structuredClone(currentSpec), editorPending = structuredClone(currentPending)
    const candidatePackRevision = packVersionsRevision()
    setDeployCandidate(null); setDeployReviewed(false); setSaveMessage(null)
    try {
      const writeEpoch = assertEditorWorkflowReadable(selectedWorkflowId)
      const endpoint = `/api/workflows/${encodeURIComponent(selectedWorkflowId)}/pack-version`
      const [raw, currentResult, receipt, proof] = await Promise.all([fetchJson<unknown>(`${endpoint}?version=draft`),
        fetchJson<unknown>(`${endpoint}?version=current`).then(value => ({ ok: true as const, value }), () => ({ ok: false as const })),
        readEditorDraftCas(selectedWorkflowId), fetchJson<unknown>(`/api/auto-heal/editor-deploy/snapshot-v2/state?workflowId=${encodeURIComponent(selectedWorkflowId)}`)])
      const snapshot = validateSnapshot(raw, selectedWorkflowId, 'draft')
      const packPath = snapshot.packPath
      if (!proof || typeof proof !== 'object' || Array.isArray(proof)) throw new Error('部署能力不可验证')
      const state = proof as Record<string, unknown>
      if (state.protocol !== 'editor-deployment-state-v1' || state.workflowId !== selectedWorkflowId || state.packId !== snapshot.packId
        || snapshot.baseRef !== `origin/wf/${selectedWorkflowId}` || snapshot.draftHead !== snapshot.baseCommit
        || state.canDeploy !== true || state.savedCommit !== snapshot.baseCommit || typeof packPath !== 'string') {
        throw new Error('部署能力未获授权或草稿(wf) Pack 基线已变化')
      }
      let active: ReturnType<typeof validateSnapshot> | null = null
      if (currentResult.ok) {
        active = validateSnapshot(currentResult.value, selectedWorkflowId, 'current')
        if (!compatiblePackScope(snapshot, active) || state.activeCommit !== active.baseCommit) {
          throw new Error('生效版和草稿(wf) Pack 范围或基线不一致，无法完整核对。')
        }
      } else if (state.activeCommit !== null) {
        throw new Error('无法证明当前生效版本不存在')
      }
      const priorFiles = active?.versionFiles ?? []
      const prior = new Map(priorFiles.map(file => [file.path, file]))
      const changes: EditorDeployCandidate['changes'] = diff(priorFiles, snapshot.versionFiles).map(change => ({
        path: change.path, operation: change.operation === 'delete' ? '删除' : change.operation === 'create' ? '新增' : '更新',
        before: prior.get(change.path) ?? null, after: change.operation === 'delete' ? null : change,
      }))
      const reviewSize = changes.reduce((n, change) => n + (change.before?.contentBase64.length ?? 0) + (change.after?.contentBase64.length ?? 0), 0)
      if (reviewSize > 512 * 1024) throw new Error('差异内容过大，无法完整展示并核对；请人工处理后再部署。')
      const source = 'draft' as const
      if (!editorWorkflowReadIsCurrent(selectedWorkflowId, writeEpoch)) throw new Error('数据库写入状态已变化，请先核对并重新确认部署。')
      const db = editorDraftDocument(receipt.specJson, selectedWorkflowId)
      if (requestSequence !== candidateRequest.current || selectedWorkflowRef.current !== selectedWorkflowId) return
      const live = useEditorStore.getState()
      if ((receipt.packId ?? selectedWorkflowId) !== snapshot.packId) throw new Error('数据库草稿的 Pack 与 Git 文件快照不一致，请先核对后再部署。')
      if (hasUnsafePackEdits(selectedWorkflowId) || packVersionsRevision() !== candidatePackRevision || live.sessionId !== sessionId
        || normalizationChanges(editorSpec, live.spec).length || normalizationChanges(editorPending, live.pendingEdits).length
        || !live._savedSpec || !sameExecutionSpec(db, live._savedSpec)) {
        throw new Error('读取期间编辑内容或数据库草稿已变化，请保存并重新读取')
      }
      const versions = sessionFor(selectedWorkflowId).state.versions
      const candidates = [versions.current.snapshot, versions.draft.snapshot]
      const baseline = candidates.find(item => item
        && item.baseCommit === snapshot.baseCommit && item.packId === snapshot.packId && diff(item.versionFiles, snapshot.versionFiles).length === 0)
      // Layout is session-only: existing moves do not invalidate a fresh Git
      // baseline, but new moves during this deployment decision do.
      if (normalizationChanges(positions, live.positions).length > 0) {
        throw new Error('读取期间画布布局已变化，请重新确认部署；当前布局仍保留。')
      }
      // First explicit deployment may be the first Git read (saved or current).
      // A fresh read is safe only with no local baseline, orphaned files or load;
      // revision/session checks still reject concurrent changes at confirmation.
      const freshBaseline = !versions.current.loading && !versions.draft.loading && !versions.current.snapshot && !versions.draft.snapshot
        && versions.current.files.length === 0 && versions.draft.files.length === 0
      if (!baseline && !freshBaseline) throw new Error(`草稿文件快照与已读取基线不一致；请先核对版本`)
      packWorkflow(snapshot.versionFiles, selectedWorkflowId, snapshot.packId)
      const activeHistoryMismatch = active?.activeHistoryMismatch === true
      if (activeHistoryMismatch) activeHistoryReview.prepare(active, latestDeploy, activeSnapshot?.specJson)
      setDeployCandidate({ workflowId: selectedWorkflowId, packId: snapshot.packId, commit: snapshot.baseCommit, expectedActiveCommit: active?.baseCommit ?? '', activeRef: active?.baseRef ?? null, changes, draftToken: receipt.draftToken, source, sessionId, fileCount: snapshot.versionFiles.length, packRevision: candidatePackRevision, writeEpoch, freshBaseline: !baseline && freshBaseline, positions, editorSpec, editorPending, baselineComparison: compareGitDatabase(snapshot, selectedWorkflowId, db), activeHistoryMismatch })
      setShowDeployConfirm(true)
    } catch (err) {
      if (requestSequence === candidateRequest.current && selectedWorkflowRef.current === selectedWorkflowId && useEditorStore.getState().sessionId === sessionId) setSaveMessage({ type: 'error', text: `无法确认数据库草稿和草稿文件，已阻止部署：${extractApiErrorMessage(err)}。` })
    }
  }, [selectedWorkflowId, isDirty, hasPendingEdits, validationErrors, setSaveMessage])

  const saving = workflowSaving || packCanvas.saving

  // Keyboard shortcut: Ctrl/Cmd+S saves the current workflow.
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 's') {
        e.preventDefault()
        if (spec && !saving) handleOpenSaveDialog()
      }
      if (showDeployConfirm || showHistory || showNewDialog || showYamlImport) return
      const target = e.target as HTMLElement | null
      if (target instanceof Element && target.closest('input, textarea, select, [contenteditable=true]')) return
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'z') {
        e.preventDefault()
        if (e.shiftKey) redo?.(); else undo?.()
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [spec, saving, handleOpenSaveDialog, undo, redo, showDeployConfirm, showHistory, showNewDialog, showYamlImport])


  const cycleError = validationErrors.find((e) => e.type === 'cycle')

  return (
    <div className={embedded ? 'flex h-full min-h-0 flex-col' : 'flex h-[calc(100vh-49px)] flex-col'}>
      <div role="tablist" aria-label="编辑器工作区" className="flex shrink-0 gap-4 border-b border-border bg-card px-4">
        {([['workflow', '工作流编排'], ['scripts', '脚本文件']] as const).map(([tab, label]) => <button
          key={tab} id={`editor-tab-${tab}`} type="button" role="tab" aria-selected={workspaceTab === tab}
          aria-controls={`editor-panel-${tab}`} tabIndex={workspaceTab === tab ? 0 : -1}
          onClick={() => { if (!hasPackVersionUpload(selectedWorkflowId) || tab === workspaceTab) setWorkspaceTab(tab) }}
          aria-disabled={packUploading && tab !== workspaceTab}
          onKeyDown={event => {
            if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return
            event.preventDefault()
            const next = tab === 'workflow' ? 'scripts' : 'workflow'
            if (hasPackVersionUpload(selectedWorkflowId)) return
            setWorkspaceTab(next)
            document.getElementById(`editor-tab-${next}`)?.focus()
          }}
          className={`border-b-2 px-1 py-2.5 text-xs font-medium ${workspaceTab === tab ? 'border-primary text-primary' : 'border-transparent text-muted-foreground'}`}
        >{label}</button>)}
      </div>
      {workspaceTab === 'scripts' && packRisk && <p role="status" className="px-4 py-2 text-sm">版本文件有未保存更改或保存状态不确定；切换标签后仍保留在本页面会话中。请先处理，不能部署旧草稿。</p>}
      {/* Toolbar */}
      <div className="flex min-h-12 items-center justify-between gap-4 overflow-x-auto border-b border-border bg-card px-4 py-2">
        <div className="flex min-w-0 items-center gap-2">
          <span className="truncate font-mono text-xs font-medium text-slate-700">
            {spec ? `${spec.id}${spec.version !== undefined ? ` v${spec.version}` : ''}` : '未加载工作流'}
          </span>
          {spec?.title && <span className="hidden truncate text-xs text-slate-400 xl:inline">· {spec.title}</span>}
          {isDirty && <span className="shrink-0 rounded-full bg-amber-50 px-2 py-0.5 text-[10px] font-medium text-amber-700">未保存</span>}
          {selectedWorkflowId &&
            (latestDeploy ? (
              <span className="hidden shrink-0 rounded-full bg-slate-100 px-2 py-0.5 text-[10px] text-slate-500 2xl:inline">
                {deploymentBaselineLabel} v{latestDeploy.version} · deploy #{latestDeploy.deployNumber}
              </span>
            ) : (
              <span className="hidden text-[10px] text-slate-400 2xl:inline">{deploymentBaselineStatus === 'ready' ? '未发布 · 数据库暂存稿' : '生效状态未确认'}</span>
            ))}
          {comparisonUncertain && latestDeploy && (
            <span role="status" className="shrink-0 rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-medium text-amber-800">
              无法判断数据库内容与{deploymentBaselineLabel} v{latestDeploy.version} 是否有差异：历史内容来源不确定或无法无损解析
            </span>
          )}
          {hasUndeployedChanges && latestDeploy && (
            <span className="shrink-0 rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-medium text-amber-800">
              数据库内容与{deploymentBaselineLabel} v{latestDeploy.version} 有差异
            </span>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <div hidden={workspaceTab !== 'workflow'} className={workspaceTab === 'workflow' ? 'flex rounded-lg bg-slate-100 p-0.5' : 'hidden'} aria-label="编辑器视图">
            <button
              onClick={() => setViewMode('visual')}
              className={`rounded-md px-3 py-1.5 text-xs font-medium transition-colors ${viewMode === 'visual' ? 'bg-card text-slate-900 shadow-sm' : 'text-slate-500 hover:text-slate-800'}`}
            >
              画布
            </button>
            <button
              onClick={() => setViewMode('split')}
              className={`rounded-md px-3 py-1.5 text-xs font-medium transition-colors ${viewMode === 'split' ? 'bg-card text-slate-900 shadow-sm' : 'text-slate-500 hover:text-slate-800'}`}
            >
              对照
            </button>
            <button
              onClick={() => setViewMode('yaml')}
              className={`rounded-md px-3 py-1.5 text-xs font-medium transition-colors ${viewMode === 'yaml' ? 'bg-card text-slate-900 shadow-sm' : 'text-slate-500 hover:text-slate-800'}`}
            >
              YAML
            </button>
          </div>
          {workspaceTab === 'workflow' && <><button type="button" onClick={() => {
            if (hasPendingEdits) { setActionError?.('请先应用或放弃未完成的配置草稿'); return }
            setShowWorkflowSettings(open => !open)
          }} disabled={!spec} className="rounded-lg border border-input bg-card px-3 py-1.5 text-xs text-muted-foreground disabled:opacity-40">流程设置</button>
          <button type="button" onClick={() => undo?.()} disabled={!canUndo || hasPendingEdits} aria-label="撤销" className="rounded px-2 py-1 text-muted-foreground disabled:opacity-30">↶</button>
          <button type="button" onClick={() => redo?.()} disabled={!canRedo || hasPendingEdits} aria-label="重做" className="rounded px-2 py-1 text-muted-foreground disabled:opacity-30">↷</button></>}
          <button
            type="button"
            onClick={() => setShowHistory(true)}
            disabled={!selectedWorkflowId}
            className="rounded-lg border border-border bg-card px-3 py-1.5 text-xs font-medium text-slate-600 hover:bg-slate-50 disabled:text-slate-300"
          >
            版本历史
          </button>
          {!embedded && <div className="relative">
            <button type="button" aria-label="更多操作" aria-expanded={showMore} onClick={() => setShowMore((open) => !open)} className="rounded-lg border border-border bg-card px-3 py-1.5 text-xs font-medium text-slate-600 hover:bg-slate-50">更多 ···</button>
            {showMore && <><button type="button" aria-label="关闭更多操作" className="fixed inset-0 z-40 cursor-default" onClick={() => setShowMore(false)} /><div role="menu" className="absolute right-0 top-full z-50 mt-2 w-44 overflow-hidden rounded-xl border border-border bg-card p-1.5 shadow-[0_16px_40px_rgba(15,23,42,0.16)]">
              <button role="menuitem" onClick={() => { setShowNewDialog(true); setShowMore(false) }} className="block w-full rounded-lg px-3 py-2 text-left text-xs text-slate-700 hover:bg-slate-50">新建工作流</button>
              <button role="menuitem" onClick={() => { setShowYamlImport(true); setYamlInput(''); setShowMore(false) }} className="block w-full rounded-lg px-3 py-2 text-left text-xs text-slate-700 hover:bg-slate-50">粘贴 YAML</button>
              <button role="menuitem" onClick={() => { handleImportFile(); setShowMore(false) }} className="block w-full rounded-lg px-3 py-2 text-left text-xs text-slate-700 hover:bg-slate-50">导入 YAML 文件</button>
              <button role="menuitem" onClick={() => { handleYamlExport(); setShowMore(false) }} disabled={!spec} className="block w-full rounded-lg px-3 py-2 text-left text-xs text-slate-700 hover:bg-slate-50 disabled:text-slate-300">导出 YAML</button>
            </div></>}
          </div>}
          <button
            onClick={handleOpenSaveDialog}
            disabled={!spec || saving || creation?.pending}
            className="rounded-lg bg-primary px-4 py-1.5 text-xs font-medium text-primary-foreground transition-colors hover:bg-primary/90 disabled:opacity-40"
          >
            {saving || creation?.pending ? '正在保存…' : '保存'}
          </button>
          <button
            type="button"
            onClick={handleDeployClick}
            disabled={!selectedWorkflowId || deployMutation.isPending}
            className="rounded-lg border border-emerald-300 bg-emerald-50 px-3 py-1.5 text-xs font-medium text-emerald-700 hover:bg-emerald-100 disabled:text-slate-300"
          >
            {deployMutation.isPending ? '部署中…' : '部署'}
          </button>
        </div>
      </div>

      {/* Validation banner */}
      {cycleError && (
        <div className="bg-red-50 px-4 py-2 text-red-700 text-sm">
          {cycleError.message}
        </div>
      )}

      {spec && workspaceTab === 'workflow' && <div className="border-b border-border bg-muted px-4 py-2 text-sm text-muted-foreground">
        <p>编排优先读取数据库暂存稿，无暂存稿时读取生效版本。应用仅更新编辑器；保存分别确认数据库编排和 Git 文件，不承诺跨存储原子提交；部署需另行确认。</p>
        <p>未应用内容、未保存的编排及节点位置仅在当前页面会话中保留，刷新、关闭或放弃更改可能丢失。导出仅包含已应用的画布工作流 spec，不包含 Pack 版本文件（包括 YAML、脚本和资源）、未应用草稿或画布节点位置。未应用内容请在原编辑区域复制备份。</p>
        <button type="button" onClick={handleYamlExport} className="mt-1 rounded border border-input px-2 py-1 text-xs">导出工作流 YAML 备份</button>
      </div>}
      {workspaceTab === 'workflow' && workflowReadError && <p role="alert" className="px-4 py-2 text-sm text-destructive">工作流读取失败，草稿仍保留：{extractApiErrorMessage(workflowReadError)}</p>}
      {workspaceTab === 'workflow' && deploymentBaselineStatus === 'error' && <p role="status" className="px-4 py-2 text-sm text-muted-foreground">生效版本状态读取失败，暂无法比较；当前编排草稿仍保留。</p>}
      {workspaceTab === 'scripts' && packErrors.map(error => <div key={error} role="alert" className="border-b border-border bg-destructive/10 px-4 py-2 text-sm text-destructive">{error}</div>)}
      {visibleRecovery?.workflowId === selectedWorkflowId && !visibleDeployment && !deploymentPending && (
        <section role="alert" aria-label="部署人工核对标记" className="border-b border-border bg-destructive/10 px-4 py-2 text-sm text-destructive">
          <p>{visibleRecovery.message}</p>
          <button
            type="button"
            onClick={openDeploymentRecovery}
            className="mt-2 rounded-lg border border-input bg-card px-3 py-1.5 text-xs text-foreground"
          >
            我已人工核对，清除当前工作流标记…
          </button>
        </section>
      )}
      {deploymentPending && !visibleDeployment && <section role="status" aria-label="部署操作" className="border-b border-border bg-muted px-4 py-2 text-sm">部署操作正在进行，不能清除核对标记或重复部署。</section>}
      {visibleDeployment && (
        <section
          role={deploymentPending ? 'status' : 'alert'}
          aria-label="部署操作"
          className={`border-b border-border px-4 py-2 text-sm ${deploymentPending ? 'bg-muted text-muted-foreground' : 'bg-destructive/10 text-destructive'}`}
        >
          <p>{deploymentPending ? '部署操作正在进行，不能清除核对标记或重复部署。' : visibleDeployment.pending ? '部署结果未知，请人工核对 Git 和数据库。' : visibleDeployment.error}</p>
          <p className="break-all">requestId：{visibleDeployment.input.requestId} · 保存版本 commit：{visibleDeployment.input.candidateCommit}</p>
          {!deploymentPending && (
            <button
              type="button"
              onClick={openDeploymentRecovery}
              className="mt-2 rounded-lg border border-input bg-card px-3 py-1.5 text-xs text-foreground"
            >
              我已人工核对，清除当前工作流标记…
            </button>
          )}
        </section>
      )}

      {(packCanvas.baselineComparison === 'different' || packCanvas.baselineComparison === 'uncomparable') && !visibleMerge &&
        <WorkflowBaselineNotice comparison={packCanvas.baselineComparison} canCompare={!!mergeAdapter} busy={workflowSaving} onCompare={openMergeReview} />}

      {saveMessage && (
        <div role={saveMessage.type === 'error' ? 'alert' : 'status'} className={`px-4 py-2 text-sm ${saveMessage.type === 'success' ? 'bg-success/10 text-success' : 'bg-destructive/10 text-destructive'}`}>
          {saveMessage.text}
        </div>
      )}

      {(packCanvas.baselineComparison !== 'different' && packCanvas.baselineComparison !== 'uncomparable') && (wantsReview || (saveMessage?.type === 'error' && /Git 工作流 YAML 与数据库基线不一致或不可比较/.test(saveMessage.text))) && !visibleMerge && (
        mergeAdapter ? <button type="button" disabled={workflowSaving} onClick={openMergeReview} className="mx-4 my-2 rounded border border-input px-3 py-2 text-sm">核对数据库和 Git 差异…</button>
          : <p role="status" className="px-4 py-2 text-sm">差异审阅保存能力尚不可用；未发送写入，请保留草稿。</p>
      )}
      {preparing && <div role="status" aria-label="差异审阅准备状态">正在核对数据库和 Git 差异</div>}
      {visibleMerge && mergeAdapter && (mergeCurrent
        ? (packCanvas.baselineComparison === 'different' || packCanvas.baselineComparison === 'uncomparable'
          ? <WorkflowBaselineComparison review={visibleMerge.review} current={mergeCurrent} onUseDatabase={cancelMergeReview}
              onUseGit={() => void useGitFromComparison()} onCancel={cancelMergeReview} hasPendingEdits={hasPendingEdits} busy={workflowSaving} />
          : <WorkflowSnapshotReconciliation review={visibleMerge.review} current={mergeCurrent} busy={workflowSaving}
              onConfirm={confirmMergeReview} onCancel={cancelMergeReview} />)
        : <section role="alert">审阅基线不可用或已变化；请取消后重新核对。<button type="button" onClick={cancelMergeReview}>取消</button></section>)}
      {actionError && <div role="alert" className="flex items-center justify-between gap-3 bg-destructive/10 px-4 py-2 text-xs text-destructive"><span>{actionError}</span><button type="button" onClick={() => setActionError?.(null)} aria-label="关闭编辑提示">×</button></div>}
      {visibleRecovery && deploymentRecoveryOpen && !deploymentPending && (
        <DeploymentRecoveryDialog
          workflowId={visibleRecovery.workflowId}
          errorMessage={deploymentRecoveryError}
          onCancel={() => setDeploymentRecoveryOpen(false)}
          onConfirm={handleDeploymentRecoveryConfirm}
        />
      )}
      {recoveredDrafts && hasPendingEdits && <p role="status" className="border-b border-border bg-muted px-4 py-2 text-xs text-muted-foreground">已保留当前会话中未应用的草稿。请在 YAML 视图或右侧配置区域继续编辑并复制备份，应用后可保存完整版本。</p>}
      {hasPendingEdits && <div role="status" className="bg-warning/10 px-4 py-2 text-xs text-warning-foreground">有 {Object.keys(pendingEdits).length} 项未应用的配置。请在原编辑区域应用或放弃；画布修改、保存和部署已保护。</div>}

      {/* Main layout */}
      <div id="editor-panel-workflow" role="tabpanel" aria-labelledby="editor-tab-workflow" hidden={workspaceTab !== 'workflow'} className={workspaceTab === 'workflow' ? 'flex min-h-0 flex-1 overflow-hidden' : 'hidden'}>
        {!embedded && (
          <WorkflowPackSidebar
            onSelectWorkflow={handleSelectWorkflow}
            selectedWorkflowId={selectedWorkflowId}
          />
        )}

        {viewMode === 'visual' && <NodePalette />}
        <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
          {workflowLoading && !spec ? <div className="flex flex-1 items-center justify-center text-sm text-muted-foreground">加载工作流…</div> :
            <div className="flex min-h-0 flex-1 overflow-hidden">
              <div hidden={viewMode === 'yaml'} className={`${viewMode === 'split' ? 'w-1/2 border-r border-border' : 'flex-1'} min-w-0 overflow-hidden`}>
                <EditorCanvas onNodeClick={(nodeId) => {
                  if (hasPendingEdits) return
                  setShowWorkflowSettings(false)
                  selectNode(nodeId)
                }} />
              </div>
              {/* Keep mounted: switching views must not destroy unapplied YAML. */}
              <div hidden={viewMode === 'visual'} className={`${viewMode === 'split' ? 'w-1/2' : 'flex-1'} min-w-0 overflow-hidden`}>
                <YamlEditor spec={spec} onImport={applyYamlToEditor} />
              </div>
            </div>}
        </div>
        {showWorkflowSettings ? <WorkflowSettingsPanel onClose={() => setShowWorkflowSettings(false)} /> : selectedNodeId ? <NodePropertyPanel /> :
          <aside className="w-72 shrink-0 border-l border-border bg-card p-5" aria-label="编辑指引">
            <h2 className="text-sm font-semibold text-foreground">让每一步都清晰</h2>
            <p className="mt-3 text-xs leading-6 text-muted-foreground">从左侧添加节点，连接输出与输入，再选择节点填写当前任务。只有需要时才展开高级配置。</p>
            <button type="button" onClick={() => setShowWorkflowSettings(true)} disabled={!spec || hasPendingEdits} className="mt-5 rounded-lg border border-input px-3 py-2 text-xs text-muted-foreground disabled:opacity-40">查看流程设置</button>
          </aside>}

      </div>

      <div id="editor-panel-scripts" role="tabpanel" aria-labelledby="editor-tab-scripts" hidden={workspaceTab !== 'scripts'} className={workspaceTab === 'scripts' ? 'min-h-0 flex-1 overflow-hidden' : 'hidden'}>
        {workspaceTab === 'scripts' && <ScriptFilesTab key={selectedWorkflowId ?? 'unsaved'} workflowId={selectedWorkflowId} onSave={() => void handleOpenSaveDialog()} canvasDirty={isDirty || hasPendingEdits} savePending={saving} showSaveError={false} showSaveButton={false} />}
      </div>

      {/* YAML Import Modal */}
      {showYamlImport && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30">
          <div className="w-full max-w-2xl rounded-lg bg-card p-6 shadow-xl">
            <h3 className="mb-3 text-lg font-semibold text-gray-900">导入 YAML</h3>
            <textarea
              value={yamlInput}
              onChange={(e) => setYamlInput(e.target.value)}
              rows={16}
              className="w-full rounded-md border border-gray-300 px-3 py-2 font-mono text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
              placeholder="在此粘贴 WorkflowSpec YAML…"
            />
            <div className="mt-3 flex justify-end gap-2">
              <button
                onClick={() => setShowYamlImport(false)}
                className="rounded-md border border-gray-300 px-4 py-1.5 text-sm text-gray-700 hover:bg-gray-50"
              >
                Cancel
              </button>
              <button
                onClick={handleYamlImport}
                className="rounded-md bg-primary px-4 py-1.5 text-sm font-medium text-primary-foreground hover:bg-primary/90"
              >
                Import
              </button>
            </div>
          </div>
        </div>
      )}

      {/* New Workflow Modal */}
      {showNewDialog && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30">
          <div className="w-full max-w-md rounded-lg bg-card p-6 shadow-xl">
            <h3 className="mb-3 text-lg font-semibold text-gray-900">新建工作流</h3>
            <div className="space-y-3">
              <div>
                <label htmlFor="workflow-new-id" className="mb-1 block text-sm font-medium text-gray-700">标识</label>
                <input
                  id="workflow-new-id"
                  type="text"
                  value={newId}
                  onChange={(e) => setNewId(e.target.value)}
                  className="w-full rounded-md border border-gray-300 px-3 py-1.5 text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
                  placeholder="我的工作流"
                />
              </div>
              <div>
                <label htmlFor="workflow-new-title" className="mb-1 block text-sm font-medium text-gray-700">标题</label>
                <input
                  type="text"
                  id="workflow-new-title"
                  value={newTitle}
                  onChange={(e) => setNewTitle(e.target.value)}
                  className="w-full rounded-md border border-gray-300 px-3 py-1.5 text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
                  placeholder="我的工作流"
                />
              </div>
            </div>
            <div className="mt-4 flex justify-end gap-2">
              <button
                onClick={() => setShowNewDialog(false)}
                className="rounded-md border border-gray-300 px-4 py-1.5 text-sm text-gray-700 hover:bg-gray-50"
              >
                Cancel
              </button>
              <button
                onClick={handleCreateNew}
                disabled={!newId.trim() || !newTitle.trim()}
                className="rounded-md bg-primary px-4 py-1.5 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-40"
              >
                Create
              </button>
            </div>
          </div>
        </div>
      )}
      {/* Version History Modal */}
      {showHistory && selectedWorkflowId && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-6">
          <div className="flex h-[88vh] w-full max-w-[1600px] flex-col overflow-hidden rounded-lg bg-card shadow-xl">
            <WorkflowHistoryPanel
              workflowId={selectedWorkflowId}
              onActiveVersionChange={() => setDeploymentBaselineRevision((revision) => revision + 1)}
              onClose={() => setShowHistory(false)}
            />
          </div>
        </div>
      )}

      {/* Confirm the entire saved tree, after the server checks its DB projection. */}
      {showDeployConfirm && selectedWorkflowId && deployCandidate && deployCandidate.workflowId === selectedWorkflowId && (
        <EditorDeployConfirmDialog
          candidate={deployCandidate}
          activeHistory={activeHistoryReview}
          deployNumber={latestDeploy?.deployNumber ?? null}
          hasUnsavedEdits={isDirty || hasPendingEdits}
          reviewed={deployReviewed}
          onReviewedChange={setDeployReviewed}
          onCancel={() => setShowDeployConfirm(false)}
          onChooseActiveHistory={activeHistoryReview.choose}
          onConfirm={() => { setShowDeployConfirm(false); void runDeploy() }}
        />
      )}
    </div>
  )
}
