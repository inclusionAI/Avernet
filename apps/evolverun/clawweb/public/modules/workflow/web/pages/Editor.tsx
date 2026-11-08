import { useState, useCallback, useEffect, useMemo, useRef } from 'react'
import { stringify as stringifyYaml } from 'yaml'
import { useDbWorkflow, useSaveWorkflowToDb, useFacadeBindings, useAutoHealSave, useAutoHealDeploy } from '../api/hooks'
import { useEditorStore } from '../editor/store'
import { getClientUser } from '@avernet/clawweb-shared/web/hooks/useClientUser'
import WorkflowPackSidebar from '../components/WorkflowPackSidebar'
import EditorCanvas from '../components/EditorCanvas'
import NodePalette from '../components/NodePalette'
import NodePropertyPanel from '../components/NodePropertyPanel'
import WorkflowSettingsPanel from '../components/editor/WorkflowSettingsPanel'
import { parseWorkflowYaml } from '../editor/yamlDraft'
import { confirmDiscardEditor } from '../editor/navigation'
import YamlEditor from '../components/YamlEditor'
import WorkflowHistoryPanel from '../components/WorkflowHistoryPanel'
import { compactDiff, formatCurrentSpecForDiff, formatSpecForDiff, specsEqual, unifiedDiff } from '../components/WorkflowVersionDiff'
import { api } from '@avernet/clawweb-shared/web/api/client'
import type { WorkflowSpec, DeployHistoryItem, VersionSnapshot, AutoHealSaveResult } from '@avernet/clawweb-shared/web/types'

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
}

export default function Editor({ embedded = false, initialWorkflowId }: EditorProps) {
  const [selectedWorkflowId, setSelectedWorkflowId] = useState<string | null>(initialWorkflowId ?? null)
  const [showYamlImport, setShowYamlImport] = useState(false)
  const [yamlInput, setYamlInput] = useState('')
  const [showNewDialog, setShowNewDialog] = useState(false)
  const [newId, setNewId] = useState('')
  const [newTitle, setNewTitle] = useState('')
  const [showSaveDialog, setShowSaveDialog] = useState(false)
  const [saveId, setSaveId] = useState('')
  const [saveTitle, setSaveTitle] = useState('')
  const [saveCommand, setSaveCommand] = useState('')
  const [saveRemark, setSaveRemark] = useState('')
  const [commandError, setCommandError] = useState<string | null>(null)
  const [saveMessage, setSaveMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null)
  const [showMore, setShowMore] = useState(false)
  const [viewMode, setViewMode] = useState<ViewMode>('visual')
  const [showHistory, setShowHistory] = useState(false)
  const [showDeployGuide, setShowDeployGuide] = useState(false)
  const [deployCommandCopied, setDeployCommandCopied] = useState(false)
  const [latestDeploy, setLatestDeploy] = useState<DeployHistoryItem | null>(null)
  const [baselineIsActive, setBaselineIsActive] = useState(false)
  const [activeSnapshot, setActiveSnapshot] = useState<VersionSnapshot | null>(null)
  const [showDeploymentDiff, setShowDeploymentDiff] = useState(false)
  const [deploymentBaselineRevision, setDeploymentBaselineRevision] = useState(0)
  const [showDeployConfirm, setShowDeployConfirm] = useState(false)
  const [showWorkflowSettings, setShowWorkflowSettings] = useState(() =>
    Object.keys(useEditorStore.getState().pendingEdits ?? {}).some(key => key.startsWith('workflow:')))
  const [recoveredDrafts] = useState(() => Object.keys(useEditorStore.getState().pendingEdits ?? {}).length > 0)
  const saveInFlight = useRef(false)

  const { spec, isDirty, selectedNodeId, validationErrors, loadSpec, createNew, importYaml, selectNode,
    pendingEdits = {}, actionError, setActionError, canUndo, canRedo, undo, redo } = useEditorStore()
  const hasPendingEdits = Object.keys(pendingEdits).length > 0

  const { data: workflowSpec, isLoading: workflowLoading } = useDbWorkflow(selectedWorkflowId ?? '')

  const saveToDbMutation = useSaveWorkflowToDb()
  // Git snapshot + server-side deploy share the same auto-heal services as AutoHealPanel.
  const gitSaveMutation = useAutoHealSave()
  const deployMutation = useAutoHealDeploy()
  const deployInFlight = useRef(false)
  const { data: facadeBindings } = useFacadeBindings()

  const COMMAND_PATTERN = /^[a-z0-9][a-z0-9_-]*[a-z0-9]$|^[a-z0-9]$/

  const handleSelectWorkflow = useCallback(
    (workflowId: string) => {
      if (workflowId === selectedWorkflowId || !confirmDiscardEditor()) return
      setShowWorkflowSettings(false)
      setSelectedWorkflowId(workflowId)
      selectNode(null)
    },
    [selectNode, selectedWorkflowId],
  )

  // In embedded mode the parent (workspace sidebar) controls which workflow is open
  useEffect(() => {
    if (initialWorkflowId !== undefined && initialWorkflowId !== selectedWorkflowId) {
      if (!confirmDiscardEditor()) return
      setShowWorkflowSettings(false)
      setSelectedWorkflowId(initialWorkflowId)
      selectNode(null)
    }
  }, [initialWorkflowId]) // eslint-disable-line react-hooks/exhaustive-deps

  // Load workflow spec when fetched
  const currentSpecId = spec?.id
  useEffect(() => {
    if (workflowSpec && workflowSpec.id !== currentSpecId) {
      if (currentSpecId && !confirmDiscardEditor()) return
      loadSpec(workflowSpec)
    }
  }, [workflowSpec, currentSpecId, loadSpec])

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
  const deploymentDiffLines = useMemo(
    () => hasUndeployedChanges ? compactDiff(unifiedDiff(deployedSpecText, savedSpecText)) : [],
    [deployedSpecText, hasUndeployedChanges, savedSpecText],
  )

  const handleOpenSaveDialog = useCallback(() => {
    if (!spec || saveInFlight.current) return
    if (hasPendingEdits) {
      setSaveMessage({ type: 'error', text: '请先应用或放弃未完成的配置草稿，再保存。' })
      return
    }
    const errors = validationErrors
    if (errors.length > 0) {
      setSaveMessage({ type: 'error', text: `保存前请修复验证错误: ${errors[0].message}` })
      return
    }
    setSaveId(spec.id)
    setSaveTitle(spec.title)
    setSaveCommand(spec.facade?.command ?? '')
    setSaveRemark(spec.facade?.remark ?? '')
    setCommandError(null)
    setShowSaveDialog(true)
  }, [spec, validationErrors, hasPendingEdits])

  const handleSave = useCallback(async () => {
    if (!spec || saveInFlight.current) return
    if (Object.keys(useEditorStore.getState().pendingEdits ?? {}).length) {
      setSaveMessage({ type: 'error', text: '请先应用或放弃配置草稿，再保存。' })
      return
    }
    const currentErrors = useEditorStore.getState().validationErrors ?? []
    if (currentErrors.length) { setSaveMessage({ type: 'error', text: `保存前请修复验证错误: ${currentErrors[0].message}` }); return }
    const id = saveId.trim()
    const title = saveTitle.trim()
    const command = saveCommand.trim()
    if (!id) return
    if (command && !COMMAND_PATTERN.test(command)) {
      setCommandError('命令必须为 kebab-case 或 snake-case（小写字母、数字、连字符、下划线）')
      return
    }
    if (commandError) return
    const saveSessionId = useEditorStore.getState().sessionId
    const specAtRequest = useEditorStore.getState().spec
    if (!specAtRequest) return
    saveInFlight.current = true
    try {
      const facade = command ? { command, remark: saveRemark.trim() || undefined } : undefined
      const nextFacade = { ...spec.facade }
      if (command) { nextFacade.command = command; nextFacade.remark = saveRemark.trim() || undefined }
      else { delete nextFacade.command; delete nextFacade.remark }
      const specToSave: WorkflowSpec = { ...spec, id, title: title || id, facade: Object.keys(nextFacade).length ? nextFacade : undefined }
      const user = getClientUser()
      if (!user?.userId) {
        setSaveMessage({ type: 'error', text: '无法获取用户信息，请重新登录。' })
        return
      }
      const originalId = selectedWorkflowId && selectedWorkflowId !== id ? selectedWorkflowId : undefined
      // Step 1: DB upsert + ID cascade + facade binding + creation grants (unchanged).
      await saveToDbMutation.mutateAsync({
        workflowId: id,
        spec: specToSave,
        facade,
        originalWorkflowId: originalId,
        botOwnerId: user.userId,
        // The editor writes no deploy_history row here; deploy rows come from the
        // server-side deploy below (auto-heal deploy service), same as AutoHealPanel.
        skipDeployHistory: true,
      })
      // Step 2: git snapshot on saved/{workflowId} via the same service the auto-heal
      // panel uses, so an editor save matches AutoHealPanel's saved state exactly.
      let gitResult: AutoHealSaveResult | null = null
      let gitError: string | null = null
      try {
        gitResult = await gitSaveMutation.mutateAsync({ workflowId: id, spec: specToSave, note: 'editor save' })
      } catch (gitErr) {
        gitError = extractApiErrorMessage(gitErr)
      }
      // Merge the authorized save identity with any edits made while DB/Git were
      // in flight. completeSave owns baseline history and refuses normalized loss.
      if (!useEditorStore.getState().completeSave(specToSave, specAtRequest, saveSessionId)) return
      setSelectedWorkflowId(id)
      setShowSaveDialog(false)
      if (gitResult) {
        const shortCommit = gitResult.commit ? gitResult.commit.slice(0, 8) : ''
        setSaveMessage({ type: 'success', text: `工作流已保存（git ${gitResult.branch}${shortCommit ? ` · ${shortCommit}` : ''}）` })
        setTimeout(() => setSaveMessage(null), 3000)
      } else {
        // Degraded: DB saved, git snapshot failed. Keep the warning visible (no
        // auto-dismiss) like other error paths; save is idempotent — retry recovers.
        setSaveMessage({ type: 'error', text: `数据库已保存，但 git 快照失败：${gitError}。重试保存可恢复。` })
      }
    } catch (err) {
      if (useEditorStore.getState().sessionId !== saveSessionId) return
      setSaveMessage({ type: 'error', text: err instanceof Error ? err.message : '保存失败' })
    } finally {
      saveInFlight.current = false
    }
  }, [spec, saveId, saveTitle, saveCommand, saveRemark, commandError, saveToDbMutation, gitSaveMutation, COMMAND_PATTERN, selectedWorkflowId])

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
    setSaveMessage({ type: 'success', text: `Exported as ${filename}` })
    setTimeout(() => setSaveMessage(null), 2000)
  }, [spec, hasPendingEdits])

  const handleCreateNew = useCallback(() => {
    if (!newId.trim() || !newTitle.trim() || !confirmDiscardEditor()) return
    createNew(newId.trim(), newTitle.trim())
    setShowNewDialog(false)
    setNewId('')
    setNewTitle('')
    setSelectedWorkflowId(null)
  }, [newId, newTitle, createNew])

  const handleCopyDeployCommand = useCallback(async () => {
    if (!selectedWorkflowId) return
    try {
      await navigator.clipboard.writeText(`/workflow deploy ${selectedWorkflowId}`)
      setDeployCommandCopied(true)
      setTimeout(() => setDeployCommandCopied(false), 2000)
    } catch {
      setSaveMessage({ type: 'error', text: '复制发布命令失败，请手动复制。' })
    }
  }, [selectedWorkflowId])

  // Server-side deploy via the same service the auto-heal panel uses: reserve deploy
  // number → git tag + push wf/{workflowId} → activate deploy history. autoRun stays
  // off because the editor has no source-run context (AutoHealPanel does the same).
  const runDeploy = useCallback(async () => {
    if (!selectedWorkflowId || deployInFlight.current) return
    const live = useEditorStore.getState()
    if (live.isDirty || Object.keys(live.pendingEdits ?? {}).length || live.validationErrors?.length
      || (live.spec && live.spec.id !== selectedWorkflowId)) {
      setSaveMessage({ type: 'error', text: '确认期间编辑内容已变化，请先应用并保存，再重新确认部署。' })
      return
    }
    deployInFlight.current = true
    setSaveMessage(null)
    try {
      const result = await deployMutation.mutateAsync({
        workflowId: selectedWorkflowId,
        note: 'editor deploy',
        autoRun: false,
      })
      setSaveMessage({ type: 'success', text: `部署成功：deploy #${result.deployNumber} · v${result.version} · ${result.tagName}` })
      setTimeout(() => setSaveMessage(null), 5000)
      // Refresh the comparison baseline so the「待部署」badge clears immediately.
      setDeploymentBaselineRevision((revision) => revision + 1)
    } catch (err) {
      const detail = extractApiErrorMessage(err)
      setSaveMessage({ type: 'error', text: `部署失败：${detail}。兜底：在 Bot 对话执行 /workflow deploy ${selectedWorkflowId}` })
    } finally {
      deployInFlight.current = false
    }
  }, [selectedWorkflowId, deployMutation])

  const handleDeployClick = useCallback(() => {
    if (!selectedWorkflowId) return
    if (validationErrors.length) { setSaveMessage({ type: 'error', text: `部署前请修复验证错误: ${validationErrors[0].message}` }); return }
    // Block deploying unsaved canvas edits — deploy always publishes the saved DB spec.
    if (isDirty || hasPendingEdits) {
      setSaveMessage({ type: 'error', text: hasPendingEdits ? '存在未应用的配置草稿，请先应用并保存再部署。' : '画布有未保存改动，请先保存再部署。' })
      return
    }
    if (hasUndeployedChanges) {
      // Diff preview doubles as the deploy confirmation (Q5).
      setShowDeploymentDiff(true)
    } else {
      setShowDeployConfirm(true)
    }
  }, [selectedWorkflowId, isDirty, hasPendingEdits, hasUndeployedChanges, validationErrors])

  // Keyboard shortcut: Ctrl/Cmd+S saves the current workflow.
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 's') {
        e.preventDefault()
        if (spec) handleOpenSaveDialog()
      }
      if (showSaveDialog || showDeployConfirm || showDeploymentDiff || showHistory || showNewDialog || showYamlImport) return
      const target = e.target as HTMLElement | null
      if (target?.closest('input, textarea, select, [contenteditable=true]')) return
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'z') {
        e.preventDefault()
        if (e.shiftKey) redo?.(); else undo?.()
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [spec, handleOpenSaveDialog, undo, redo, showSaveDialog, showDeployConfirm, showDeploymentDiff, showHistory, showNewDialog, showYamlImport])

  useEffect(() => {
    const beforeUnload = (event: BeforeUnloadEvent) => {
      if (!isDirty && !hasPendingEdits) return
      event.preventDefault()
      event.returnValue = ''
    }
    window.addEventListener('beforeunload', beforeUnload)
    return () => window.removeEventListener('beforeunload', beforeUnload)
  }, [isDirty, hasPendingEdits])

  const cycleError = validationErrors.find((e) => e.type === 'cycle')

  return (
    <div className={embedded ? 'flex h-full min-h-0 flex-col' : 'flex h-[calc(100vh-49px)] flex-col'}>
      {/* Toolbar */}
      <div className="flex min-h-12 items-center justify-between gap-4 border-b border-border bg-card px-4 py-2">
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
            <button
              type="button"
              onClick={() => setShowDeploymentDiff(true)}
              className="shrink-0 rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-medium text-amber-800 hover:bg-amber-200"
            >
              与{deploymentBaselineLabel} v{latestDeploy.version} 有差异 · 待部署
            </button>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <div className="flex rounded-lg bg-slate-100 p-0.5" aria-label="编辑器视图">
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
          <button type="button" onClick={() => {
            if (hasPendingEdits) { setActionError?.('请先应用或放弃未完成的配置草稿'); return }
            setShowWorkflowSettings(open => !open)
          }} disabled={!spec} className="rounded-lg border border-input bg-card px-3 py-1.5 text-xs text-muted-foreground disabled:opacity-40">流程设置</button>
          <button type="button" onClick={() => undo?.()} disabled={!canUndo || hasPendingEdits} aria-label="撤销" className="rounded px-2 py-1 text-muted-foreground disabled:opacity-30">↶</button>
          <button type="button" onClick={() => redo?.()} disabled={!canRedo || hasPendingEdits} aria-label="重做" className="rounded px-2 py-1 text-muted-foreground disabled:opacity-30">↷</button>
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
              <button role="menuitem" onClick={() => { setShowDeployGuide(true); setShowMore(false) }} disabled={!selectedWorkflowId} className="block w-full rounded-lg px-3 py-2 text-left text-xs text-slate-700 hover:bg-slate-50 disabled:text-slate-300">Bot 命令部署（复制命令兜底）</button>
            </div></>}
          </div>}
          <button
            onClick={handleOpenSaveDialog}
            disabled={!spec}
            className="rounded-lg bg-primary px-4 py-1.5 text-xs font-medium text-primary-foreground transition-colors hover:bg-primary/90 disabled:opacity-40"
          >
            {saveToDbMutation.isPending ? '保存中…' : '保存'}
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

      {/* Save message */}
      {saveMessage && (
        <div className={`px-4 py-2 text-sm ${saveMessage.type === 'success' ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-700'}`}>
          {saveMessage.text}
        </div>
      )}

      {actionError && <div role="alert" className="flex items-center justify-between gap-3 bg-destructive/10 px-4 py-2 text-xs text-destructive"><span>{actionError}</span><button type="button" onClick={() => setActionError?.(null)} aria-label="关闭编辑提示">×</button></div>}
      {recoveredDrafts && hasPendingEdits && <p role="status" className="border-b border-border bg-muted px-4 py-2 text-xs text-muted-foreground">已保留当前会话中未应用的草稿。请在 YAML 视图或右侧配置区域继续编辑，应用或放弃后再保存。</p>}
      {hasPendingEdits && <div role="status" className="bg-warning/10 px-4 py-2 text-xs text-warning-foreground">有 {Object.keys(pendingEdits).length} 项未应用的配置。请在原编辑区域应用或放弃；画布修改、保存和部署已保护。</div>}

      {/* Main layout */}
      <div className="flex flex-1 overflow-hidden">
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
                <label className="mb-1 block text-sm font-medium text-gray-700">标题</label>
                <input
                  type="text"
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
    {/* Save Dialog */}
      {showSaveDialog && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30">
          <div className="w-full max-w-md rounded-lg bg-card p-6 shadow-xl">
            <h3 className="mb-3 text-lg font-semibold text-gray-900">保存工作流</h3>
            <div className="space-y-3">
              <div>
                <label htmlFor="workflow-save-id" className="mb-1 block text-sm font-medium text-gray-700">标识</label>
                <input
                  id="workflow-save-id"
                  type="text"
                  value={saveId}
                  onChange={(e) => setSaveId(e.target.value)}
                  className="w-full rounded-md border border-gray-300 px-3 py-1.5 text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
                  placeholder="我的工作流"
                />
              </div>
              <div>
                <label className="mb-1 block text-sm font-medium text-gray-700">标题</label>
                <input
                  type="text"
                  value={saveTitle}
                  onChange={(e) => setSaveTitle(e.target.value)}
                  className="w-full rounded-md border border-gray-300 px-3 py-1.5 text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
                  placeholder="我的工作流"
                />
              </div>
              <div>
                <label className="mb-1 block text-sm font-medium text-gray-700">
                  外观命令 <span className="font-normal text-gray-400">(可选)</span>
                </label>
                <div className="flex items-center gap-1">
                  <span className="text-sm text-gray-400">/</span>
                  <input
                    type="text"
                    value={saveCommand}
                    onChange={(e) => {
                      setSaveCommand(e.target.value)
                      setCommandError(null)
                    }}
                    onBlur={() => {
                      const cmd = saveCommand.trim()
                      if (!cmd) { setCommandError(null); return }
                      if (!COMMAND_PATTERN.test(cmd)) {
                        setCommandError('命令必须为 kebab-case 或 snake-case（小写字母、数字、连字符、下划线）')
                        return
                      }
                      const existing = facadeBindings?.find((b) => b.command === cmd)
                      if (existing && existing.workflowId !== spec?.id) {
                        setCommandError(`Command "/${cmd}" 已绑定到工作流 "${existing.workflowId}"`)
                      }
                    }}
                    className={`w-full rounded-md border px-3 py-1.5 text-sm focus:outline-none focus:ring-1 ${
                      commandError
                        ? 'border-red-400 focus:border-red-500 focus:ring-red-500'
                        : 'border-gray-300 focus:border-blue-500 focus:ring-blue-500'
                    }`}
                    placeholder="code-review"
                  />
                </div>
                {commandError && <p className="mt-1 text-xs text-red-600">{commandError}</p>}
                {!commandError && <p className="mt-1 text-xs text-gray-400">用户可通过 /{saveCommand || '命令'}</p>}
              </div>
              <div>
                <label className="mb-1 block text-sm font-medium text-gray-700">
                  备注 <span className="font-normal text-gray-400">(可选)</span>
                </label>
                <input
                  type="text"
                  value={saveRemark}
                  onChange={(e) => setSaveRemark(e.target.value)}
                  className="w-full rounded-md border border-gray-300 px-3 py-1.5 text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
                  placeholder="命令的简要描述"
                />
              </div>
            </div>
            <div className="mt-4 flex justify-end gap-2">
              <button
                onClick={() => setShowSaveDialog(false)}
                className="rounded-md border border-gray-300 px-4 py-1.5 text-sm text-gray-700 hover:bg-gray-50"
              >
                Cancel
              </button>
              <button
                onClick={handleSave}
                disabled={!saveId.trim() || !!commandError || saveToDbMutation.isPending || gitSaveMutation.isPending}
                className="rounded-md bg-primary px-4 py-1.5 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-40"
              >
                {saveToDbMutation.isPending || gitSaveMutation.isPending ? '保存中…' : '保存工作流'}
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

      {showDeploymentDiff && activeSnapshot && latestDeploy && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-6">
          <div className="flex h-[80vh] w-full max-w-4xl flex-col overflow-hidden rounded-lg bg-card shadow-xl">
            <div className="flex items-center justify-between border-b border-border bg-gray-50 px-4 py-3">
              <div>
                <h3 className="text-sm font-semibold text-gray-900">部署前差异</h3>
                <p className="mt-0.5 text-xs text-gray-500">{deploymentBaselineLabel} v{latestDeploy.version} · deploy #{latestDeploy.deployNumber} → 当前已保存内容</p>
              </div>
              <button onClick={() => setShowDeploymentDiff(false)} className="rounded border border-gray-300 bg-card px-2 py-0.5 text-xs text-gray-600 hover:bg-gray-50">关闭</button>
            </div>
            <div className="flex-1 overflow-auto bg-gray-900 font-mono text-xs leading-relaxed">
              {deploymentDiffLines.map((line, index) => {
                if (line.type === 'skip') {
                  return <div key={`skip-${index}`} className="border-y border-gray-700 bg-gray-800 px-3 py-1 text-center text-gray-500">{line.text}</div>
                }
                const isAdded = line.type === 'add'
                const isDeleted = line.type === 'del'
                const lineNumber = isDeleted ? line.fromLine : line.toLine
                return (
                  <div key={index} className={`flex ${isAdded ? 'bg-green-900/30' : isDeleted ? 'bg-red-900/30' : ''}`}>
                    <span className="w-10 shrink-0 select-none border-r border-gray-700 px-1 text-right text-gray-500">{lineNumber ?? ''}</span>
                    <span className={`shrink-0 select-none px-1 ${isAdded ? 'text-green-300' : isDeleted ? 'text-red-300' : 'text-gray-300'}`}>{isAdded ? '+' : isDeleted ? '-' : ' '}</span>
                    <span className={`whitespace-pre px-1 ${isAdded ? 'text-green-300' : isDeleted ? 'text-red-300' : 'text-gray-300'}`}>{line.text}</span>
                  </div>
                )
              })}
            </div>
            <div className="flex items-center justify-between gap-2 border-t border-border bg-gray-50 px-4 py-2">
              <span className="truncate text-xs text-gray-400">Bot 命令兜底：<code className="rounded bg-gray-100 px-1">/workflow deploy {selectedWorkflowId}</code></span>
              <span className="flex shrink-0 gap-2">
                <button onClick={() => setShowDeploymentDiff(false)} className="rounded border border-gray-300 bg-card px-3 py-1 text-xs text-gray-700 hover:bg-gray-50">稍后部署</button>
                <button onClick={() => { setShowDeploymentDiff(false); void runDeploy() }} className="rounded bg-emerald-600 px-3 py-1 text-xs font-medium text-primary-foreground hover:bg-emerald-700">确认部署</button>
              </span>
            </div>
          </div>
        </div>
      )}

      {/* Lightweight deploy confirmation when the canvas matches the live version (Q5). */}
      {showDeployConfirm && selectedWorkflowId && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-6">
          <div className="w-full max-w-md rounded-lg bg-card p-6 shadow-xl">
            <h3 className="text-lg font-semibold text-gray-900">部署工作流</h3>
            <p className="mt-3 text-sm text-gray-600">
              {activeSnapshot
                ? '当前内容与线上生效版本一致，仍要重新部署吗？将生成新的部署记录（deploy #、git tag）。'
                : latestDeploy
                  ? '无法获取线上生效版本的快照，仍要部署当前已保存内容吗？'
                  : '该工作流尚无线上生效版本，部署后将创建首个生效版本。'}
            </p>
            <p className="mt-2 text-xs text-gray-400">
              Bot 命令兜底：在 Bot 对话执行 <code className="rounded bg-gray-100 px-1">/workflow deploy {selectedWorkflowId}</code>
            </p>
            <div className="mt-5 flex justify-end gap-2">
              <button onClick={() => setShowDeployConfirm(false)} className="rounded-md border border-gray-300 px-4 py-1.5 text-sm text-gray-700 hover:bg-gray-50">取消</button>
              <button onClick={() => { setShowDeployConfirm(false); void runDeploy() }} className="rounded-md bg-emerald-600 px-4 py-1.5 text-sm font-medium text-primary-foreground hover:bg-emerald-700">确认部署</button>
            </div>
          </div>
        </div>
      )}

      {/* Fallback entry (Q3): the Bot command guide only opens from the 更多 menu or after a failed direct deploy. */}
      {showDeployGuide && selectedWorkflowId && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-6">
          <div className="w-full max-w-lg rounded-lg bg-card p-6 shadow-xl">
            <h3 className="text-lg font-semibold text-gray-900">部署工作流</h3>
            <div className="mt-4 rounded-md border border-border bg-slate-50 p-3">
              <div className="text-xs text-slate-500">在对应 Bot 对话中执行</div>
              <code className="mt-1 block select-all font-mono text-sm text-slate-800">/workflow deploy {selectedWorkflowId}</code>
            </div>
            <div className="mt-5 flex justify-end gap-2">
              <button onClick={() => setShowDeployGuide(false)} className="rounded-md border border-gray-300 px-4 py-1.5 text-sm text-gray-700 hover:bg-gray-50">关闭</button>
              <button onClick={() => void handleCopyDeployCommand()} className="rounded-md bg-emerald-600 px-4 py-1.5 text-sm font-medium text-primary-foreground hover:bg-emerald-700">
                {deployCommandCopied ? '已复制' : '复制部署命令'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
