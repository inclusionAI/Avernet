import { useState, useCallback, useEffect } from 'react'
import { ReactFlowProvider } from '@xyflow/react'
import { parse as parseYaml, stringify as stringifyYaml } from 'yaml'
import { useDbWorkflow, useSaveWorkflowToDb, useFacadeBindings } from '../api/hooks'
import { useEditorStore } from '../editor/store'
import { getClientUser } from '@avernet/clawweb-shared/web/hooks/useClientUser'
import WorkflowPackSidebar from '../components/WorkflowPackSidebar'
import EditorCanvas from '../components/EditorCanvas'
import NodePalette from '../components/NodePalette'
import NodePropertyPanel from '../components/NodePropertyPanel'
import WorkflowConfigPanel from '../components/WorkflowConfigPanel'
import YamlEditor from '../components/YamlEditor'
import WorkflowHistoryPanel from '../components/WorkflowHistoryPanel'
import { api } from '@avernet/clawweb-shared/web/api/client'
import type { WorkflowSpec, DeployHistoryItem } from '@avernet/clawweb-shared/web/types'

type ViewMode = 'visual' | 'yaml' | 'split'

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
  const [latestDeploy, setLatestDeploy] = useState<DeployHistoryItem | null>(null)

  const { spec, isDirty, selectedNodeId, validationErrors, loadSpec, createNew, importYaml, selectNode, markClean } = useEditorStore()

  const { data: workflowSpec, isLoading: workflowLoading } = useDbWorkflow(selectedWorkflowId ?? '')

  const saveToDbMutation = useSaveWorkflowToDb()
  const { data: facadeBindings } = useFacadeBindings()

  const COMMAND_PATTERN = /^[a-z0-9][a-z0-9_-]*[a-z0-9]$|^[a-z0-9]$/

  const handleSelectWorkflow = useCallback(
    (workflowId: string) => {
      setSelectedWorkflowId(workflowId)
      selectNode(null)
    },
    [selectNode],
  )

  // In embedded mode the parent (workspace sidebar) controls which workflow is open
  useEffect(() => {
    if (initialWorkflowId !== undefined && initialWorkflowId !== selectedWorkflowId) {
      setSelectedWorkflowId(initialWorkflowId)
      selectNode(null)
    }
  }, [initialWorkflowId]) // eslint-disable-line react-hooks/exhaustive-deps

  // Load workflow spec when fetched
  const currentSpecId = spec?.id
  useEffect(() => {
    if (workflowSpec && workflowSpec.id !== currentSpecId) {
      loadSpec(workflowSpec)
    }
  }, [workflowSpec, currentSpecId, loadSpec])

  // Fetch the latest deploy history row to show the current effective version.
  useEffect(() => {
    if (!selectedWorkflowId) {
      setLatestDeploy(null)
      return
    }
    let cancelled = false
    api.workflows
      .getHistory(selectedWorkflowId, 1)
      .then((r) => {
        if (!cancelled) setLatestDeploy(r.history?.[0] ?? null)
      })
      .catch(() => {
        if (!cancelled) setLatestDeploy(null)
      })
    return () => {
      cancelled = true
    }
  }, [selectedWorkflowId])

  const handleOpenSaveDialog = useCallback(() => {
    if (!spec) return
    const errors = validationErrors.filter((e) => e.type === 'cycle' || (e.type === 'missing-field' && !e.nodeId))
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
  }, [spec, validationErrors])

  const handleSave = useCallback(async () => {
    if (!spec) return
    const id = saveId.trim()
    const title = saveTitle.trim()
    const command = saveCommand.trim()
    if (!id) return
    if (command && !COMMAND_PATTERN.test(command)) {
      setCommandError('命令必须为 kebab-case 或 snake-case（小写字母、数字、连字符、下划线）')
      return
    }
    if (commandError) return
    try {
      const facade = command ? { command, remark: saveRemark.trim() || undefined } : undefined
      const specToSave: WorkflowSpec = { ...spec, id, title: title || id }
      const user = getClientUser()
      if (!user?.userId) {
        setSaveMessage({ type: 'error', text: '无法获取用户信息，请重新登录。' })
        return
      }
      const originalId = selectedWorkflowId && selectedWorkflowId !== id ? selectedWorkflowId : undefined
      await saveToDbMutation.mutateAsync({
        workflowId: id,
        spec: specToSave,
        facade,
        originalWorkflowId: originalId,
        botOwnerId: user.userId,
      })
      if (id !== spec.id || title !== spec.title) {
        useEditorStore.getState().updateSpecField('id', id)
        useEditorStore.getState().updateSpecField('title', title || id)
      }
      useEditorStore.getState().updateSpecField('facade', facade)
      markClean()
      setShowSaveDialog(false)
      setSaveMessage({ type: 'success', text: '工作流已保存到数据库。' })
      setTimeout(() => setSaveMessage(null), 3000)
    } catch (err) {
      setSaveMessage({ type: 'error', text: err instanceof Error ? err.message : '保存失败' })
    }
  }, [spec, saveId, saveTitle, saveCommand, saveRemark, commandError, saveToDbMutation, markClean, COMMAND_PATTERN, selectedWorkflowId])

  const handleYamlImport = useCallback(() => {
    try {
      const parsed = parseYaml(yamlInput) as WorkflowSpec
      if (!parsed.id || !parsed.version || !parsed.title || !Array.isArray(parsed.nodes)) {
        setSaveMessage({ type: 'error', text: 'Invalid YAML: must have id, version, title, and nodes array.' })
        return
      }
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
  }, [spec])

  const handleCreateNew = useCallback(() => {
    if (!newId.trim() || !newTitle.trim()) return
    createNew(newId.trim(), newTitle.trim())
    setShowNewDialog(false)
    setNewId('')
    setNewTitle('')
    setSelectedWorkflowId(null)
  }, [newId, newTitle, createNew])

  // Keyboard shortcut: Ctrl/Cmd+S saves the current workflow.
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 's') {
        e.preventDefault()
        if (spec) handleOpenSaveDialog()
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [spec, handleOpenSaveDialog])

  const cycleError = validationErrors.find((e) => e.type === 'cycle')

  return (
    <div className={embedded ? 'flex h-full min-h-0 flex-col' : 'flex h-[calc(100vh-49px)] flex-col'}>
      {/* Toolbar */}
      <div className="flex min-h-12 items-center justify-between gap-4 border-b border-slate-200 bg-white px-4 py-2">
        <div className="flex min-w-0 items-center gap-2">
          <span className="truncate font-mono text-xs font-medium text-slate-700">
            {spec ? `${spec.id} v${spec.version}` : '未加载工作流'}
          </span>
          {spec?.title && <span className="hidden truncate text-xs text-slate-400 xl:inline">· {spec.title}</span>}
          {isDirty && <span className="shrink-0 rounded-full bg-amber-50 px-2 py-0.5 text-[10px] font-medium text-amber-700">未保存</span>}
          {selectedWorkflowId &&
            (latestDeploy ? (
              <span className="hidden shrink-0 rounded-full bg-slate-100 px-2 py-0.5 text-[10px] text-slate-500 2xl:inline">
                生效 v{latestDeploy.version} · deploy #{latestDeploy.deployNumber}
              </span>
            ) : (
              <span className="hidden text-[10px] text-slate-400 2xl:inline">仅本地草稿</span>
            ))}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <div className="flex rounded-lg bg-slate-100 p-0.5" aria-label="编辑器视图">
            <button
              onClick={() => setViewMode('visual')}
              className={`rounded-md px-3 py-1.5 text-xs font-medium transition-colors ${viewMode === 'visual' ? 'bg-white text-slate-900 shadow-sm' : 'text-slate-500 hover:text-slate-800'}`}
            >
              画布
            </button>
            <button
              onClick={() => setViewMode('split')}
              className={`rounded-md px-3 py-1.5 text-xs font-medium transition-colors ${viewMode === 'split' ? 'bg-white text-slate-900 shadow-sm' : 'text-slate-500 hover:text-slate-800'}`}
            >
              对照
            </button>
            <button
              onClick={() => setViewMode('yaml')}
              className={`rounded-md px-3 py-1.5 text-xs font-medium transition-colors ${viewMode === 'yaml' ? 'bg-white text-slate-900 shadow-sm' : 'text-slate-500 hover:text-slate-800'}`}
            >
              YAML
            </button>
          </div>
          <div className="relative">
            <button type="button" aria-label="更多操作" aria-expanded={showMore} onClick={() => setShowMore((open) => !open)} className="rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs font-medium text-slate-600 hover:bg-slate-50">更多 ···</button>
            {showMore && <><button type="button" aria-label="关闭更多操作" className="fixed inset-0 z-40 cursor-default" onClick={() => setShowMore(false)} /><div role="menu" className="absolute right-0 top-full z-50 mt-2 w-44 overflow-hidden rounded-xl border border-slate-200 bg-white p-1.5 shadow-[0_16px_40px_rgba(15,23,42,0.16)]">
              <button role="menuitem" onClick={() => { setShowNewDialog(true); setShowMore(false) }} className="block w-full rounded-lg px-3 py-2 text-left text-xs text-slate-700 hover:bg-slate-50">新建工作流</button>
              <button role="menuitem" onClick={() => { setShowYamlImport(true); setYamlInput(''); setShowMore(false) }} className="block w-full rounded-lg px-3 py-2 text-left text-xs text-slate-700 hover:bg-slate-50">粘贴 YAML</button>
              <button role="menuitem" onClick={() => { handleImportFile(); setShowMore(false) }} className="block w-full rounded-lg px-3 py-2 text-left text-xs text-slate-700 hover:bg-slate-50">导入 YAML 文件</button>
              <button role="menuitem" onClick={() => { handleYamlExport(); setShowMore(false) }} disabled={!spec} className="block w-full rounded-lg px-3 py-2 text-left text-xs text-slate-700 hover:bg-slate-50 disabled:text-slate-300">导出 YAML</button>
              <div className="my-1 border-t border-slate-100" />
              <button role="menuitem" onClick={() => { setShowHistory(true); setShowMore(false) }} disabled={!selectedWorkflowId} className="block w-full rounded-lg px-3 py-2 text-left text-xs text-slate-700 hover:bg-slate-50 disabled:text-slate-300">版本历史</button>
            </div></>}
          </div>
          <button
            onClick={handleOpenSaveDialog}
            disabled={!spec}
            className="rounded-lg bg-blue-600 px-4 py-1.5 text-xs font-medium text-white transition-colors hover:bg-blue-700 disabled:opacity-40"
          >
            {saveToDbMutation.isPending ? '保存中…' : '保存'}
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

      {/* Main layout */}
      <div className="flex flex-1 overflow-hidden">
        {!embedded && (
          <WorkflowPackSidebar
            onSelectWorkflow={handleSelectWorkflow}
            selectedWorkflowId={selectedWorkflowId}
          />
        )}

        {/* Center: Canvas / YAML / Split */}
        <div className="flex flex-1 flex-col overflow-hidden">
          {workflowLoading && !spec ? (
            <div className="flex flex-1 items-center justify-center">
              <div className="h-8 w-8 animate-spin rounded-full border-2 border-gray-300 border-t-blue-600" />
            </div>
          ) : (
            <div className="flex flex-1 overflow-hidden">
              {/* Visual Editor */}
              {(viewMode === 'visual' || viewMode === 'split') && (
                <ReactFlowProvider>
                  <div className={`${viewMode === 'split' ? 'w-1/2' : 'flex-1'} flex overflow-hidden ${viewMode === 'split' ? 'border-r border-gray-200' : ''}`}>
                    <div className="flex-1 overflow-hidden">
                      <EditorCanvas onNodeClick={(nodeId) => selectNode(nodeId)} />
                    </div>
                    {viewMode === 'visual' && <NodePalette />}
                  </div>
                </ReactFlowProvider>
              )}

              {/* YAML Editor */}
              {(viewMode === 'yaml' || viewMode === 'split') && (
                <div className={`${viewMode === 'split' ? 'w-1/2' : 'flex-1'} overflow-hidden`}>
                  <YamlEditor
                    spec={spec}
                    onImport={importYaml}
                  />
                </div>
              )}

              </div>
          )}
        </div>

        {/* Right: Property Panel or Workflow Config */}
        {selectedNodeId ? <NodePropertyPanel /> : <WorkflowConfigPanel />}
      </div>

      {/* YAML Import Modal */}
      {showYamlImport && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30">
          <div className="w-full max-w-2xl rounded-lg bg-white p-6 shadow-xl">
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
                className="rounded-md bg-blue-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-blue-700"
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
          <div className="w-full max-w-md rounded-lg bg-white p-6 shadow-xl">
            <h3 className="mb-3 text-lg font-semibold text-gray-900">新建工作流</h3>
            <div className="space-y-3">
              <div>
                <label className="mb-1 block text-sm font-medium text-gray-700">标识</label>
                <input
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
                className="rounded-md bg-blue-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-40"
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
          <div className="w-full max-w-md rounded-lg bg-white p-6 shadow-xl">
            <h3 className="mb-3 text-lg font-semibold text-gray-900">保存工作流</h3>
            <div className="space-y-3">
              <div>
                <label className="mb-1 block text-sm font-medium text-gray-700">标识</label>
                <input
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
                disabled={!saveId.trim() || !!commandError}
                className="rounded-md bg-blue-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-40"
              >
                Save to Database
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Version History Modal */}
      {showHistory && selectedWorkflowId && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-6">
          <div className="flex h-[80vh] w-full max-w-5xl flex-col overflow-hidden rounded-lg bg-white shadow-xl">
            <WorkflowHistoryPanel
              workflowId={selectedWorkflowId}
              onClose={() => setShowHistory(false)}
            />
          </div>
        </div>
      )}
    </div>
  )
}
