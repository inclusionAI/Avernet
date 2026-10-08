import { useState, useCallback, useEffect } from 'react'
import { parse as parseYaml, stringify as stringifyYaml } from 'yaml'
import { useEditorStore } from '../../editor/store'
import { useDbWorkflow, useSaveWorkflowToDb, useFacadeBindings } from '../../api/hooks'
import { getClientUser } from '../../hooks/useClientUser'
import EditorCanvas from '../EditorCanvas'
import NodePalette from '../NodePalette'
import NodePropertyPanel from '../NodePropertyPanel'
import WorkflowConfigPanel from '../WorkflowConfigPanel'
import YamlEditor from '../YamlEditor'
import type { WorkflowSpec } from '../../types'

type ViewMode = 'visual' | 'yaml'

interface EditorTabProps {
  workflowId: string
}

const COMMAND_PATTERN = /^[a-z0-9][a-z0-9_-]*[a-z0-9]$|^[a-z0-9]$/

export default function EditorTab({ workflowId }: EditorTabProps) {
  const [viewMode, setViewMode] = useState<ViewMode>('visual')
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
  const [showYamlImport, setShowYamlImport] = useState(false)
  const [yamlInput, setYamlInput] = useState('')

  const { spec, isDirty, selectedNodeId, validationErrors, loadSpec, createNew, importYaml, selectNode, markClean, reset } = useEditorStore()
  const { data: workflowSpec, isLoading: workflowLoading } = useDbWorkflow(workflowId)
  const saveToDbMutation = useSaveWorkflowToDb()
  const { data: facadeBindings } = useFacadeBindings()

  // Load spec when fetched
  const currentSpecId = spec?.id
  useEffect(() => {
    if (workflowSpec && workflowSpec.id !== currentSpecId) {
      loadSpec(workflowSpec)
    }
  }, [workflowSpec, currentSpecId, loadSpec])

  useEffect(() => {
    return () => {
      reset()
    }
  }, [reset])

  const handleCreateNew = useCallback(() => {
    if (!newId.trim() || !newTitle.trim()) return
    createNew(newId.trim(), newTitle.trim())
    setShowNewDialog(false)
    setNewId('')
    setNewTitle('')
  }, [newId, newTitle, createNew])

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
      await saveToDbMutation.mutateAsync({
        workflowId: id,
        spec: specToSave,
        facade,
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
  }, [spec, saveId, saveTitle, saveCommand, saveRemark, commandError, saveToDbMutation, markClean])

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

  const cycleError = validationErrors.find((e) => e.type === 'cycle')

  if (workflowLoading && !spec) {
    return (
      <div className="flex h-full items-center justify-center">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-gray-300 border-t-blue-600" />
      </div>
    )
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      {/* Toolbar */}
      <div className="flex items-center justify-between border-b border-gray-200 bg-white px-4 py-2">
        <div className="flex items-center gap-3">
          <span className="font-mono text-sm text-gray-700">
            {spec ? `${spec.id} v${spec.version}` : '未加载工作流'}
          </span>
          {spec?.title && <span className="text-sm text-gray-400">— {spec.title}</span>}
          {isDirty && <span className="text-xs text-amber-500">● 未保存</span>}
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setShowNewDialog(true)}
            className="rounded-md border border-gray-300 px-3 py-1 text-sm text-gray-700 transition-colors hover:bg-gray-50"
          >
            New
          </button>
          <button
            onClick={() => { setShowYamlImport(true); setYamlInput('') }}
            className="rounded-md border border-gray-300 px-3 py-1 text-sm text-gray-700 transition-colors hover:bg-gray-50"
          >
            Paste YAML
          </button>
          <button
            onClick={handleImportFile}
            className="rounded-md border border-gray-300 px-3 py-1 text-sm text-gray-700 transition-colors hover:bg-gray-50"
          >
            Import File
          </button>
          <button
            onClick={handleYamlExport}
            disabled={!spec}
            className="rounded-md border border-gray-300 px-3 py-1 text-sm text-gray-700 transition-colors hover:bg-gray-50 disabled:opacity-40"
          >
            Export YAML
          </button>
          <div className="flex rounded-md border border-gray-300">
            <button
              onClick={() => setViewMode('visual')}
              className={`px-2.5 py-1 text-xs transition-colors ${
                viewMode === 'visual'
                  ? 'bg-blue-600 text-white'
                  : 'bg-white text-gray-600 hover:bg-gray-50'
              } rounded-l-md`}
            >
              Visual
            </button>
            <button
              onClick={() => setViewMode('yaml')}
              className={`px-2.5 py-1 text-xs transition-colors ${
                viewMode === 'yaml'
                  ? 'bg-blue-600 text-white'
                  : 'bg-white text-gray-600 hover:bg-gray-50'
              } rounded-r-md`}
            >
              YAML
            </button>
          </div>
          <button
            onClick={handleOpenSaveDialog}
            disabled={!spec}
            className="rounded-md bg-blue-600 px-3 py-1 text-sm font-medium text-white transition-colors hover:bg-blue-700 disabled:opacity-40"
          >
            {saveToDbMutation.isPending ? 'Saving…' : 'Save'}
          </button>
        </div>
      </div>

      {cycleError && (
        <div className="bg-red-50 px-4 py-2 text-sm text-red-700">
          {cycleError.message}
        </div>
      )}

      {saveMessage && (
        <div className={`px-4 py-2 text-sm ${saveMessage.type === 'success' ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-700'}`}>
          {saveMessage.text}
        </div>
      )}

      {!spec ? (
        <div className="flex h-full items-center justify-center text-sm text-gray-400">
          从侧边栏选择工作流或创建新工作流
        </div>
      ) : (
        <div className="flex flex-1 overflow-hidden">
          {/* Main editor area */}
          <div className="flex flex-1 overflow-hidden">
            {viewMode === 'visual' && (
              <>
                <div className="flex-1 overflow-hidden">
                  <EditorCanvas onNodeClick={(nodeId) => selectNode(nodeId)} />
                </div>
                <NodePalette />
              </>
            )}
            {viewMode === 'yaml' && (
              <div className="flex-1 overflow-hidden">
                <YamlEditor spec={spec} onImport={importYaml} />
              </div>
            )}
          </div>

          {/* Right panel */}
          {selectedNodeId ? <NodePropertyPanel /> : <WorkflowConfigPanel />}
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
                  placeholder="my-workflow"
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
                />
              </div>
              <div>
                <label className="mb-1 block text-sm font-medium text-gray-700">标题</label>
                <input
                  type="text"
                  value={saveTitle}
                  onChange={(e) => setSaveTitle(e.target.value)}
                  className="w-full rounded-md border border-gray-300 px-3 py-1.5 text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
                />
              </div>
              <div>
                <label className="mb-1 block text-sm font-medium text-gray-700">
                  触发命令 <span className="font-normal text-gray-400">(可选)</span>
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
                      const existing = facadeBindings?.find((b: { command: string; workflowId: string }) => b.command === cmd)
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
                onClick={() => void handleSave()}
                disabled={!saveId.trim() || !!commandError}
                className="rounded-md bg-blue-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-40"
              >
                Save
              </button>
            </div>
          </div>
        </div>
      )}

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
    </div>
  )
}
