import { readWorkflowDocument } from '../api/workflowDocument'
import { useState, useCallback, useContext, useEffect, useLayoutEffect, useRef, useSyncExternalStore } from 'react'
import { stringify as stringifyYaml } from 'yaml'
import { UNSAFE_LocationContext } from 'react-router-dom'
import { useEditorWorkflowDocument, useAutoHealDeploy } from '../api/hooks'
import { useEditorStore } from '../editor/store'
import WorkflowPackSidebar from '../components/WorkflowPackSidebar'
import EditorCanvas from '../components/EditorCanvas'
import NodePalette from '../components/NodePalette'
import NodePropertyPanel from '../components/NodePropertyPanel'
import WorkflowSettingsPanel from '../components/editor/WorkflowSettingsPanel'
import { parseWorkflowYaml } from '../editor/yamlDraft'
import { confirmDiscardEditor } from '../editor/navigation'
import { beginWorkflowTransition, commitWorkflowTransition } from '../editor/workflowTransition'
import YamlEditor from '../components/YamlEditor'
import ScriptFilesTab from '../components/ScriptFilesTab'
import { hasPackVersionRisk, hasPackVersionEditRisk, hasPackVersionUpload, sessionFor, validateSnapshot, packVersionsRevision, subscribePackVersions, diff } from '../editor/packVersionSession'
import { WorkspacePopPendingContext, isWorkflowPopOwned, subscribeWorkflowPopOwnership, usePackNavigationGuard, workflowPopOwnershipSnapshot } from '../editor/usePackNavigationGuard'
import { packWorkflow, sameExecutionSpec } from '../editor/packCanvas'
import { usePackCanvasSession } from '../editor/usePackCanvasSession'
import { createWorkflowPack, reconcileNewWorkflow, type CreatedWorkflow } from '../editor/newWorkflowSave'
import { deployPackCandidate, reconcilePackDeploy } from '../editor/packDeploy'
import WorkflowHistoryPanel from '../components/WorkflowHistoryPanel'
import { formatCurrentSpecForDiff, formatSpecForDiff, specsEqual } from '../components/WorkflowVersionDiff'
import { api, fetchJson } from '@avernet/clawweb-shared/web/api/client'
import type { WorkflowSpec, DeployHistoryItem, VersionSnapshot } from '@avernet/clawweb-shared/web/types'

type ViewMode = 'visual' | 'yaml' | 'split'


/** Extract a human-readable message from fetchJson ApiError strings ("API NNN: {json}"). */
function extractApiErrorMessage(err: unknown): string {
  const msg = err instanceof Error ? err.message : String(err)
  const jsonMatch = msg.match(/^API \d+:\s*(\{.*\})$/s)
  if (jsonMatch) {
    try {
      const parsed = JSON.parse(jsonMatch[1]) as { message?: string }
      if (parsed.message) return parsed.message
    } catch { /* not valid JSON — fall through */ }
  }
  return msg
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
  const [saveMessage, setSaveMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null)
  const [showMore, setShowMore] = useState(false)
  const [viewMode, setViewMode] = useState<ViewMode>('visual')
  const [workspaceTab, setWorkspaceTab] = useState<'workflow' | 'scripts'>('workflow')
  const [showHistory, setShowHistory] = useState(false)
  const [latestDeploy, setLatestDeploy] = useState<DeployHistoryItem | null>(null)
  const [baselineIsActive, setBaselineIsActive] = useState(false)
  const [activeSnapshot, setActiveSnapshot] = useState<VersionSnapshot | null>(null)
  const [deployCandidate, setDeployCandidate] = useState<{ workflowId: string; packId: string; commit: string; sessionId: number; fileCount: number; packRevision: number } | null>(null)
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
  const [showWorkflowSettings, setShowWorkflowSettings] = useState(() =>
    Object.keys(useEditorStore.getState().pendingEdits ?? {}).some(key => key.startsWith('workflow:')))
  const [recoveredDrafts] = useState(() => Object.keys(useEditorStore.getState().pendingEdits ?? {}).length > 0)

  const { spec, isDirty, isLayoutDirty, selectedNodeId, validationErrors, loadSpec, importYaml, selectNode,
    pendingEdits = {}, actionError, setActionError, canUndo, canRedo, undo, redo } = useEditorStore()
  const creation = spec ? sessionFor(spec.id).state.creation : undefined
  const hasPendingEdits = Object.keys(pendingEdits).length > 0
  useSyncExternalStore(subscribePackVersions, packVersionsRevision, packVersionsRevision)
  const workspacePopPending = useContext(WorkspacePopPendingContext)
  const workflowPopOwned = useSyncExternalStore(subscribeWorkflowPopOwnership, workflowPopOwnershipSnapshot, () => false)
  const packRisk = hasPackVersionRisk(selectedWorkflowId)
  const packUploading = hasPackVersionUpload(selectedWorkflowId)
  usePackNavigationGuard(!workspaceManagedPop)

  const { data: workflowSpec, isLoading: workflowLoading, error: workflowReadError } = useEditorWorkflowDocument(selectedWorkflowId ?? '')

  const packCanvas = usePackCanvasSession(selectedWorkflowId, workflowSpec)
  const deployment = selectedWorkflowId ? sessionFor(selectedWorkflowId).state.deployment : null
  const deployMutation = useAutoHealDeploy()
  const deployInFlight = useRef(false)

  const handleSelectWorkflow = useCallback(async (workflowId: string) => {
    if (workflowId === selectedWorkflowId) return
    const request = ++switchRequest.current
    const approval = beginWorkflowTransition()
    if (!approval) return
    try {
      const target = await readWorkflowDocument(workflowId)
      if (request !== switchRequest.current) return
      if (!commitWorkflowTransition(approval, target, workflowId)) {
        setSaveMessage({ type: 'error', text: '工作流切换未完成：目标无效或确认后编辑内容已变化；原草稿仍保留。' })
        return
      }
      setShowWorkflowSettings(false)
      setSelectedWorkflowId(workflowId)
      setSaveMessage(null)
    } catch (error) {
      if (request === switchRequest.current) setSaveMessage({ type: 'error', text: `工作流读取失败，原草稿仍保留：${extractApiErrorMessage(error)}` })
    }
  }, [selectedWorkflowId])

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
      setShowWorkflowSettings(false)
      return
    }
    if (workspacePopPending || workflowPopOwned || isWorkflowPopOwned()) return
    void handleSelectWorkflow(initialWorkflowId)
  }, [initialWorkflowId, navigationType, workflowPopOwned, workspaceManagedPop, workspacePopPending]) // eslint-disable-line react-hooks/exhaustive-deps

  // Only the committed selection may supply the initial loaded document.
  const currentSpecId = spec?.id
  useEffect(() => {
    if (workflowSpec && workflowSpec.id === selectedWorkflowId && !currentSpecId) loadSpec(workflowSpec)
  }, [workflowSpec, currentSpecId, selectedWorkflowId, loadSpec])

  // Fetch the latest deploy history row to show the current effective version.
  useEffect(() => {
    if (!selectedWorkflowId) {
      setLatestDeploy(null)
      setBaselineIsActive(false)
      return
    }
    setLatestDeploy(null)
    setBaselineIsActive(false)
    let cancelled = false
    api.workflows
      .getHistory(selectedWorkflowId, 50, true)
      .then((r) => {
        if (cancelled) return
        const active = r.active ?? null
        setLatestDeploy(active ?? r.history?.find((item) => item.action !== 'edit') ?? null)
        setBaselineIsActive(Boolean(active))
      })
      .catch(() => {
        if (!cancelled) {
          setLatestDeploy(null)
          setBaselineIsActive(false)
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

  const deployedSpecText = activeSnapshot ? formatSpecForDiff(activeSnapshot.specJson) : ''
  const savedSpecText = workflowSpec ? formatCurrentSpecForDiff(workflowSpec) : ''
  const hasUndeployedChanges = Boolean(activeSnapshot && workflowSpec && !specsEqual(deployedSpecText, savedSpecText))
  const deploymentBaselineLabel = baselineIsActive ? '生效' : '最新发布'

  function acceptCreated(created: CreatedWorkflow, sessionId: number) {
    const live = useEditorStore.getState()
    if (live.sessionId !== sessionId || live.spec?.id !== created.snapshot.workflowId) return
    live.completeSave(created.requestedSpec, created.requestedSpec, sessionId)
    setSelectedWorkflowId(created.snapshot.workflowId)
    setSaveMessage({ type: 'success', text: `新建完整版本已保存到 saved commit ${created.result.commit}；尚未部署。` })
  }

  const handleOpenSaveDialog = useCallback(async () => {
    if (!useEditorStore.getState().spec) return
    const sessionId = useEditorStore.getState().sessionId
    const workflowId = selectedWorkflowId
    setSaveMessage(null)
    try {
      if (!workflowId) {
        const live = useEditorStore.getState()
        if (Object.keys(live.pendingEdits ?? {}).length) throw new Error('存在未应用的配置草稿；请先应用或放弃，再保存。')
        if (live.validationErrors.length) throw new Error(`保存前请修复验证错误：${live.validationErrors[0].message}`)
        acceptCreated(await createWorkflowPack(live.spec!), sessionId)
        return
      }
      const result = await packCanvas.save()
      if (selectedWorkflowRef.current !== workflowId) return
      if (result) setSaveMessage({ type: 'success', text: `完整版本已保存到 saved commit ${result.commit}；尚未部署。节点位置仅在当前会话保留。` })
      else if (workflowId) {
        const state = sessionFor(workflowId).state
        const error = state.versions[state.version].error
        if (error && useEditorStore.getState().sessionId === sessionId) setSaveMessage({ type: 'error', text: error })
      }
    } catch (cause) {
      if (selectedWorkflowRef.current === workflowId && useEditorStore.getState().sessionId === sessionId) {
        setSaveMessage({ type: 'error', text: cause instanceof Error ? cause.message : '保存未完成，本地草稿仍保留。' })
      }
    }
  }, [selectedWorkflowId, packCanvas])

  const applyYamlToEditor = useCallback((incoming: WorkflowSpec) => {
    const previousId = useEditorStore.getState().spec?.id
    useEditorStore.getState().setActionError(null)
    importYaml(incoming)
    const failure = useEditorStore.getState().actionError
    if (failure) throw new Error(failure)
    if (incoming.id !== previousId) setSelectedWorkflowId(null)
  }, [importYaml])

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
  }, [yamlInput, importYaml])

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
  }, [spec, hasPendingEdits])

  const handleCreateNew = useCallback(() => {
    const id = newId.trim(), title = newTitle.trim()
    if (!id || !title) return
    switchRequest.current++
    const approval = beginWorkflowTransition()
    if (!approval) return
    const target: WorkflowSpec = { id, title, version: '1.0.0', nodes: [] }
    if (!commitWorkflowTransition(approval, target, id, true)) {
      setSaveMessage({ type: 'error', text: '新建未完成：目标无效或确认后编辑内容已变化；原草稿仍保留。' })
      return
    }
    setShowNewDialog(false)
    setNewId('')
    setNewTitle('')
    setSelectedWorkflowId(null)
  }, [newId, newTitle])

  // Confirm an immutable saved Git candidate, never the database's possibly stale spec.
  const runDeploy = useCallback(async () => {
    if (!selectedWorkflowId || deployInFlight.current) return
    const live = useEditorStore.getState()
    if (hasPackVersionEditRisk(selectedWorkflowId) || !deployCandidate || deployCandidate.packRevision !== packVersionsRevision() || deployCandidate.workflowId !== selectedWorkflowId || live.sessionId !== deployCandidate.sessionId
      || live.isDirty || Object.keys(live.pendingEdits ?? {}).length || live.validationErrors?.length
      || !live.spec || live.spec.id !== selectedWorkflowId) {
      setSaveMessage({ type: 'error', text: '确认期间编辑内容已变化，请先应用并保存，再重新确认部署。' })
      return
    }
    deployInFlight.current = true
    setSaveMessage(null)
    try {
      const result = await deployPackCandidate(sessionFor(selectedWorkflowId), selectedWorkflowId, deployCandidate.packId, deployCandidate.commit, request => deployMutation.mutateAsync(request))
      if (!result || selectedWorkflowRef.current !== selectedWorkflowId || useEditorStore.getState().sessionId !== deployCandidate.sessionId) return
      setSaveMessage({ type: 'success', text: `部署成功：deploy #${result.deployNumber} · v${result.version} · ${result.tagName}` })
      setTimeout(() => setSaveMessage(null), 5000)
      setDeploymentBaselineRevision((revision) => revision + 1)
    } catch (err) {
      const detail = extractApiErrorMessage(err)
      setSaveMessage({ type: 'error', text: `部署失败：${detail}。请重新检查并确认已保存的草稿。` })
    } finally {
      deployInFlight.current = false
    }
  }, [selectedWorkflowId, deployCandidate, deployMutation])

  const handleDeployClick = useCallback(async () => {
    if (!selectedWorkflowId || deployInFlight.current) return
    if (hasPackVersionEditRisk(selectedWorkflowId)) { setSaveMessage({ type: 'error', text: '版本文件存在未保存更改或保存状态不确定，请先处理后再部署。' }); return }
    if (validationErrors.length) { setSaveMessage({ type: 'error', text: `部署前请修复验证错误: ${validationErrors[0].message}` }); return }
    if (isDirty || hasPendingEdits) {
      setSaveMessage({ type: 'error', text: hasPendingEdits ? '存在未应用的配置草稿，无法部署；请先应用并保存。' : '画布有未保存改动，无法部署；请先保存完整版本。' })
      return
    }
    const requestSequence = ++candidateRequest.current
    const sessionId = useEditorStore.getState().sessionId
    const candidatePackRevision = packVersionsRevision()
    setDeployCandidate(null)
    setSaveMessage(null)
    try {
      const previous = sessionFor(selectedWorkflowId).state.deployment
      if (previous) {
        if (previous.pending) throw new Error('上次部署仍在进行，请等待或查询操作状态')
        setDeployCandidate({ workflowId: selectedWorkflowId, packId: previous.input.packId, commit: previous.input.candidateCommit,
          sessionId, fileCount: 0, packRevision: candidatePackRevision })
        setShowDeployConfirm(true)
        return
      }
      const raw = await fetchJson<unknown>(
        `/api/workflows/${encodeURIComponent(selectedWorkflowId)}/pack-version?version=draft`,
      )
      const snapshot = validateSnapshot(raw, selectedWorkflowId, 'draft')
      if (snapshot.canWrite !== true) throw new Error('此宿主未授予版本写入/部署权限')
      if (requestSequence !== candidateRequest.current || selectedWorkflowRef.current !== selectedWorkflowId) return
      const live = useEditorStore.getState()
      if (hasPackVersionEditRisk(selectedWorkflowId) || packVersionsRevision() !== candidatePackRevision || live.sessionId !== sessionId || live.isDirty || Object.keys(live.pendingEdits ?? {}).length
        || live.validationErrors?.length || !live.spec || live.spec.id !== selectedWorkflowId) {
        throw new Error('读取期间编辑内容已变化，请保存后重试')
      }
      if (!snapshot.versionFiles.length) throw new Error('服务未返回完整有效的已保存草稿 commit')
      const versions = sessionFor(selectedWorkflowId).state.versions
      const baseline = [versions.current.snapshot, versions.draft.snapshot].find(item => item
        && item.baseCommit === snapshot.baseCommit && item.packId === snapshot.packId
        && diff(item.versionFiles, snapshot.versionFiles).length === 0)
      if (!baseline || !live._savedSpec || !sameExecutionSpec(packWorkflow(snapshot.versionFiles, selectedWorkflowId, snapshot.packId).spec, live._savedSpec)) {
        throw new Error('部署候选与当前已读取基线或画布不一致；请先读取并核对草稿版本')
      }
      setDeployCandidate({ workflowId: selectedWorkflowId, packId: snapshot.packId, commit: snapshot.baseCommit, sessionId, fileCount: snapshot.versionFiles.length, packRevision: candidatePackRevision })
      setShowDeployConfirm(true)
    } catch (err) {
      if (requestSequence === candidateRequest.current && selectedWorkflowRef.current === selectedWorkflowId) {
        setSaveMessage({ type: 'error', text: `无法确认已保存的完整草稿，已阻止部署：${extractApiErrorMessage(err)}。请保存后重试。` })
      }
    }
  }, [selectedWorkflowId, isDirty, hasPendingEdits, validationErrors])

  // Keyboard shortcut: Ctrl/Cmd+S saves the current workflow.
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 's') {
        e.preventDefault()
        if (spec) handleOpenSaveDialog()
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
  }, [spec, handleOpenSaveDialog, undo, redo, showDeployConfirm, showHistory, showNewDialog, showYamlImport])


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
      {packRisk && <p role="status" className="px-4 py-2 text-sm">版本文件有未保存更改或保存状态不确定；切换标签后仍保留在本页面会话中。请先处理，不能部署旧草稿。</p>}
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
              <span className="hidden text-[10px] text-slate-400 2xl:inline">仅本地草稿</span>
            ))}
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
            disabled={!spec || packCanvas.saving || creation?.pending}
            className="rounded-lg bg-primary px-4 py-1.5 text-xs font-medium text-primary-foreground transition-colors hover:bg-primary/90 disabled:opacity-40"
          >
            {packCanvas.saving || creation?.pending ? '正在保存…' : workspaceTab === 'scripts' ? '保存工作流' : '保存'}
          </button>
          <button
            type="button"
            onClick={handleDeployClick}
            disabled={!selectedWorkflowId || deployMutation.isPending}
            className="rounded-lg border border-emerald-300 bg-emerald-50 px-3 py-1.5 text-xs font-medium text-emerald-700 hover:bg-emerald-100 disabled:text-slate-300"
          >
            {deployMutation.isPending ? '部署中…' : workspaceTab === 'scripts' ? '部署工作流' : '部署'}
          </button>
        </div>
      </div>

      {/* Validation banner */}
      {cycleError && (
        <div className="bg-red-50 px-4 py-2 text-red-700 text-sm">
          {cycleError.message}
        </div>
      )}

      {spec && <div className="border-b border-border bg-muted px-4 py-2 text-sm text-muted-foreground">
        <p>{packCanvas.loading ? '正在读取完整 Git 基线…' : packCanvas.canWrite ? '画布与文件共同保存为完整 Pack 版本，保存到 saved 后再确认部署。' : '此宿主未确认版本写入能力；仍可编辑和导出本地草稿。'}</p>
        <p>画布及未应用内容仅在当前页面会话中保留，刷新、关闭或放弃更改可能丢失。请在原编辑区域复制未应用内容。导出仅包含已应用的画布工作流 spec，不包含 Pack 版本文件（包括 YAML、脚本和资源）、未应用草稿或画布节点位置。Pack 文本改动请在版本文件界面另行复制；节点位置无法通过此导出恢复，需手动重新排列。</p>
        <button type="button" onClick={handleYamlExport} className="mt-1 rounded border border-input px-2 py-1 text-xs">导出工作流 YAML 备份</button>
      </div>}
      {/* Save message */}
      {workflowReadError && <p role="alert" className="px-4 py-2 text-sm text-destructive">工作流读取失败，草稿仍保留：{extractApiErrorMessage(workflowReadError)}</p>}
      {packCanvas.error && <div role="alert" className="border-b border-border bg-destructive/10 px-4 py-2 text-sm text-destructive">
        {packCanvas.error}
        {packCanvas.snapshot && <button type="button" onClick={packCanvas.adoptGit} className="ml-3 rounded border border-input px-2 py-1">以此 Git 基线加载画布</button>}
      </div>}
      {creation && creation.phase !== 'completed' && <section aria-label="新建保存操作" className="border-b border-border bg-muted px-4 py-2 text-sm">
        <p>{creation.pending ? '正在原子创建工作流并初始化 saved…' : creation.error}</p>
        <p className="break-all">requestId：{creation.input.requestId}</p>
        <button type="button" disabled={creation.pending || creation.phase === 'conflict'} onClick={() => void handleOpenSaveDialog()} className="mr-3 rounded border border-input px-2 py-1">重试新建保存（同一 requestId）</button>
        {[false, true].map(recover => <button key={String(recover)} type="button" disabled={creation.pending || creation.phase === 'conflict'} onClick={async () => {
          const live = useEditorStore.getState(), sessionId = live.sessionId, id = live.spec!.id
          try { acceptCreated(await reconcileNewWorkflow(id, recover), sessionId) }
          catch (cause) {
            if (useEditorStore.getState().sessionId === sessionId) setSaveMessage({ type: 'error', text: cause instanceof Error ? cause.message : '新建恢复未完成，草稿仍保留。' })
          }
        }} className="mr-3 rounded border border-input px-2 py-1">{recover ? '恢复新建保存' : '查询新建保存'}</button>)}
      </section>}
      {deployment && <section aria-label="部署操作" className="border-b border-border bg-muted px-4 py-2 text-sm">
        <p>{deployment.pending ? '部署操作正在进行。' : deployment.error}</p>
        <p className="break-all">requestId：{deployment.input.requestId} · commit：{deployment.input.candidateCommit}</p>
        {[false, true].map(recover => <button key={String(recover)} type="button" disabled={deployment.pending} onClick={async () => {
          const id = selectedWorkflowId!
          const result = await reconcilePackDeploy(sessionFor(id), recover)
          if (result && selectedWorkflowRef.current === id) {
            setSaveMessage({ type: 'success', text: `部署成功：deploy #${result.deployNumber} · v${result.version} · ${result.tagName}` })
            setDeploymentBaselineRevision(revision => revision + 1)
          }
        }} className="mr-3 rounded border border-input px-2 py-1">{recover ? '恢复部署操作' : '查询部署操作'}</button>)}
      </section>}

      {saveMessage && (
        <div className={`px-4 py-2 text-sm ${saveMessage.type === 'success' ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-700'}`}>
          {saveMessage.text}
        </div>
      )}

      {actionError && <div role="alert" className="flex items-center justify-between gap-3 bg-destructive/10 px-4 py-2 text-xs text-destructive"><span>{actionError}</span><button type="button" onClick={() => setActionError?.(null)} aria-label="关闭编辑提示">×</button></div>}
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
        {workspaceTab === 'scripts' && <ScriptFilesTab key={selectedWorkflowId ?? 'unsaved'} workflowId={selectedWorkflowId} onSave={() => void handleOpenSaveDialog()} canvasDirty={isDirty || hasPendingEdits} />}
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
          <div className="flex h-[80vh] w-full max-w-5xl flex-col overflow-hidden rounded-lg bg-card shadow-xl">
            <WorkflowHistoryPanel
              workflowId={selectedWorkflowId}
              onActiveVersionChange={() => setDeploymentBaselineRevision((revision) => revision + 1)}
              onClose={() => setShowHistory(false)}
            />
          </div>
        </div>
      )}

      {/* Confirm the exact saved Git candidate, including files outside the workflow spec. */}
      {showDeployConfirm && selectedWorkflowId && deployCandidate && deployCandidate.workflowId === selectedWorkflowId && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-6">
          <div className="w-full max-w-md rounded-lg bg-card p-6 shadow-xl">
            <h3 className="text-lg font-semibold text-gray-900">部署工作流</h3>
            <p className="mt-3 text-sm text-gray-600">即将部署以下已保存的完整 Pack 快照（包含工作流、脚本和其他文件）。</p>
            <p className="mt-2 break-all text-xs text-gray-600">Pack：{deployCandidate.packId}<br />草稿 commit：{deployCandidate.commit}<br />版本文件：{deployCandidate.fileCount} 个</p>
            <p className="mt-2 text-xs text-gray-500">此确认以 Git commit 为准；未保存的文件编辑不会部署。页面显示的数据库差异不代表完整 Pack 的变更。</p>
            <div className="mt-5 flex justify-end gap-2">
              <button onClick={() => setShowDeployConfirm(false)} className="rounded-md border border-gray-300 px-4 py-1.5 text-sm text-gray-700 hover:bg-gray-50">取消</button>
              <button onClick={() => { setShowDeployConfirm(false); void runDeploy() }} className="rounded-md bg-emerald-600 px-4 py-1.5 text-sm font-medium text-primary-foreground hover:bg-emerald-700">确认部署</button>
            </div>
          </div>
        </div>
      )}


    </div>
  )
}
