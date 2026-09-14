import { useCallback, useEffect, useMemo, useState } from 'react'
import { parse as parseYaml } from 'yaml'
import type { WorkflowSpec } from '@avernet/clawweb-shared/web/types'

interface CreateWorkflowModalProps {
  open: boolean
  onClose: () => void
  onSubmit: (input: {
    workflowId: string
    spec: WorkflowSpec
    facade?: { command?: string; remark?: string }
  }) => Promise<void>
  isPending?: boolean
}

const COMMAND_PATTERN = /^[a-z0-9][a-z0-9_-]*[a-z0-9]$|^[a-z0-9]$/
type CreateSource = 'blank' | 'paste' | 'file'

function buildInitialSpec(workflowId: string, title: string): WorkflowSpec {
  return {
    id: workflowId,
    version: '1.0.0',
    title,
    nodes: [
      {
        id: `${workflowId}_start`,
        title: '开始',
        executor: { type: 'done' },
      },
    ],
  }
}

export default function CreateWorkflowModal({ open, onClose, onSubmit, isPending }: CreateWorkflowModalProps) {
  const [workflowId, setWorkflowId] = useState('')
  const [title, setTitle] = useState('')
  const [command, setCommand] = useState('')
  const [remark, setRemark] = useState('')
  const [commandError, setCommandError] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [source, setSource] = useState<CreateSource>('blank')
  const [yamlInput, setYamlInput] = useState('')

  useEffect(() => {
    if (open) {
      setWorkflowId('')
      setTitle('')
      setCommand('')
      setRemark('')
      setCommandError(null)
      setError(null)
      setSource('blank')
      setYamlInput('')
    }
  }, [open])

  const idError = useMemo(() => {
    const id = workflowId.trim()
    if (!id) return null
    if (/\s/.test(id)) return '工作流 ID 不能包含空格'
    if (!/^[^/]+$/.test(id)) return '工作流 ID 不能包含 "/"'
    return null
  }, [workflowId])

  useEffect(() => {
    const cmd = command.trim()
    if (!cmd) {
      setCommandError(null)
      return
    }
    if (!COMMAND_PATTERN.test(cmd)) {
      setCommandError('命令必须为 kebab-case 或 snake-case（小写字母、数字、连字符、下划线）')
    } else {
      setCommandError(null)
    }
  }, [command])

  const canSubmit = useMemo(() => {
    if (source !== 'blank') return !!yamlInput.trim()
    return !!workflowId.trim() && !!title.trim() && !idError && !commandError
  }, [source, yamlInput, workflowId, title, idError, commandError])

  const handleClose = useCallback(() => {
    if (isPending) return
    onClose()
  }, [isPending, onClose])

  const handleSubmit = useCallback(async () => {
    if (!canSubmit || isPending) return
    try {
      if (source !== 'blank') {
        const parsed = parseYaml(yamlInput) as WorkflowSpec
        if (!parsed?.id || !parsed?.title || !parsed?.version || !Array.isArray(parsed.nodes)) {
          throw new Error('YAML 必须包含 id、version、title 和 nodes。')
        }
        const facade = parsed.facade?.command
          ? { command: parsed.facade.command, remark: parsed.facade.remark }
          : undefined
        await onSubmit({ workflowId: parsed.id, spec: parsed, facade })
      } else {
        const id = workflowId.trim()
        const displayTitle = title.trim() || id
        const cmd = command.trim()
        const facade = cmd ? { command: cmd, remark: remark.trim() || undefined } : undefined
        await onSubmit({ workflowId: id, spec: buildInitialSpec(id, displayTitle), facade })
      }
      onClose()
    } catch (err) {
      setError(err instanceof Error ? err.message : '创建工作流失败')
    }
  }, [canSubmit, isPending, source, yamlInput, workflowId, title, command, remark, onSubmit, onClose])

  const handleImportFile = useCallback((file?: File) => {
    if (!file) return
    const reader = new FileReader()
    reader.onload = () => {
      setYamlInput(String(reader.result ?? ''))
      setSource('file')
      setError(null)
    }
    reader.readAsText(file)
  }, [])

  if (!open) return null

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      onClick={handleClose}
    >
      <div
        className="w-full max-w-lg overflow-hidden rounded-xl bg-white shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="border-b border-gray-200 px-6 py-4">
          <div className="flex items-center justify-between">
            <h2 className="text-lg font-semibold text-gray-900">新建工作流</h2>
            <button
              onClick={handleClose}
              disabled={isPending}
              className="text-gray-400 transition-colors hover:text-gray-600 disabled:opacity-40"
              aria-label="关闭"
            >
              ✕
            </button>
          </div>
          <p className="mt-1 text-sm text-gray-500">
            选择创建方式；已有工作流只在画布或 YAML 中修改。
          </p>
        </div>

        <div className="space-y-4 px-6 py-5">
          <div className="grid grid-cols-3 gap-2" role="group" aria-label="创建方式">
            {([
              ['blank', '空白工作流'],
              ['paste', '粘贴 YAML'],
              ['file', '导入 YAML'],
            ] as const).map(([value, label]) => (
              <button
                key={value}
                type="button"
                onClick={() => { setSource(value); setError(null) }}
                className={`rounded-lg border px-2 py-2 text-xs font-medium transition-colors ${source === value ? 'border-blue-500 bg-blue-50 text-blue-700' : 'border-slate-200 text-slate-600 hover:bg-slate-50'}`}
              >
                {label}
              </button>
            ))}
          </div>

          {source === 'blank' ? <>
          <div>
            <label className="mb-1.5 block text-sm font-medium text-gray-700">
              工作流 ID <span className="text-red-500">*</span>
            </label>
            <input
              type="text"
              value={workflowId}
              onChange={(e) => setWorkflowId(e.target.value)}
              placeholder="例如 my-workflow"
              disabled={isPending}
              className={`w-full rounded-md border px-3 py-2 text-sm outline-none transition-colors focus:border-blue-500 focus:ring-2 focus:ring-blue-200 disabled:bg-gray-50 ${
                idError ? 'border-red-300' : 'border-gray-300'
              }`}
            />
            {idError ? (
              <p className="mt-1 text-xs text-red-600">{idError}</p>
            ) : (
              <p className="mt-1 text-xs text-gray-400">唯一标识，保存后不可修改</p>
            )}
          </div>

          <div>
            <label className="mb-1.5 block text-sm font-medium text-gray-700">
              工作流名称 <span className="text-red-500">*</span>
            </label>
            <input
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="例如 我的工作流"
              disabled={isPending}
              className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm outline-none transition-colors focus:border-blue-500 focus:ring-2 focus:ring-blue-200 disabled:bg-gray-50"
            />
          </div>

          <div>
            <label className="mb-1.5 block text-sm font-medium text-gray-700">
              触发命令（可选）
            </label>
            <div className="relative">
              <span className="absolute left-3 top-1/2 -translate-y-1/2 text-sm text-gray-400">/</span>
              <input
                type="text"
                value={command}
                onChange={(e) => setCommand(e.target.value)}
                placeholder="my-command"
                disabled={isPending}
                className={`w-full rounded-md border py-2 pl-7 pr-3 text-sm outline-none transition-colors focus:border-blue-500 focus:ring-2 focus:ring-blue-200 disabled:bg-gray-50 ${
                  commandError ? 'border-red-300' : 'border-gray-300'
                }`}
              />
            </div>
            {commandError ? (
              <p className="mt-1 text-xs text-red-600">{commandError}</p>
            ) : (
              <p className="mt-1 text-xs text-gray-400">用户可通过 /{command.trim() || '命令'} 触发</p>
            )}
          </div>

          <div>
            <label className="mb-1.5 block text-sm font-medium text-gray-700">
              命令说明（可选）
            </label>
            <input
              type="text"
              value={remark}
              onChange={(e) => setRemark(e.target.value)}
              placeholder="一句话描述该命令的用途"
              disabled={isPending}
              className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm outline-none transition-colors focus:border-blue-500 focus:ring-2 focus:ring-blue-200 disabled:bg-gray-50"
            />
          </div>
          </> : (
            <div>
              {source === 'paste' ? (
                <>
                  <label className="mb-1.5 block text-sm font-medium text-gray-700">Workflow YAML</label>
                  <textarea
                    value={yamlInput}
                    onChange={(event) => setYamlInput(event.target.value)}
                    rows={12}
                    disabled={isPending}
                    placeholder={'id: my-workflow\nversion: 1.0.0\ntitle: 我的工作流\nnodes: []'}
                    className="w-full rounded-md border border-gray-300 px-3 py-2 font-mono text-xs leading-5 outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-200 disabled:bg-gray-50"
                  />
                  <p className="mt-1 text-xs text-gray-400">YAML 中的 ID、名称和节点配置会原样创建为草稿。</p>
                </>
              ) : (
                <>
                  <label className="mb-1.5 block text-sm font-medium text-gray-700">选择 YAML 文件</label>
                  <input
                    type="file"
                    accept=".yaml,.yml,text/yaml,application/x-yaml"
                    disabled={isPending}
                    onChange={(event) => handleImportFile(event.target.files?.[0])}
                    className="block w-full rounded-md border border-dashed border-slate-300 px-3 py-3 text-sm text-slate-600 file:mr-3 file:rounded-md file:border-0 file:bg-blue-50 file:px-3 file:py-1.5 file:text-xs file:font-medium file:text-blue-700 hover:border-blue-300"
                  />
                  {yamlInput && <p className="mt-2 text-xs text-emerald-700">已读取 YAML，可创建工作流。</p>}
                </>
              )}
            </div>
          )}

          {error && (
            <div className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">{error}</div>
          )}
        </div>

        <div className="flex items-center justify-end gap-3 border-t border-gray-200 bg-gray-50 px-6 py-4">
          <button
            onClick={handleClose}
            disabled={isPending}
            className="rounded-md px-4 py-2 text-sm font-medium text-gray-700 transition-colors hover:bg-gray-100 disabled:opacity-40"
          >
            取消
          </button>
          <button
            onClick={handleSubmit}
            disabled={!canSubmit || isPending}
            className="flex min-w-[80px] items-center justify-center rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-blue-700 disabled:cursor-not-allowed disabled:bg-blue-300"
          >
            {isPending ? (
              <>
                <span className="mr-2 inline-block h-4 w-4 animate-spin rounded-full border-2 border-white/40 border-t-white" />
                创建中…
              </>
            ) : (
              source === 'blank' ? '创建' : '从 YAML 创建'
            )}
          </button>
        </div>
      </div>
    </div>
  )
}
