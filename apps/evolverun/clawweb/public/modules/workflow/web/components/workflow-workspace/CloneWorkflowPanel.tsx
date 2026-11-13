import { useCallback, useEffect, useMemo, useState } from 'react'
import type { WorkflowCloneCandidate } from '@avernet/clawweb-shared/web/api/client'
import { useWorkflowCloneCandidates, useWorkflowClonePreview } from '../../api/hooks'

const COMMAND_PATTERN = /^[a-z0-9][a-z0-9_-]*[a-z0-9]$|^[a-z0-9]$/
const WORKFLOW_ID_PATTERN = /^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$/

const isValidTargetWorkflowId = (id: string) => WORKFLOW_ID_PATTERN.test(id)
  && !id.includes('..') && !id.endsWith('.') && !id.endsWith('.lock') && !/^__.+__$/.test(id)

export interface CloneWorkflowFormInput {
  sourceWorkflowId: string
  targetWorkflowId: string
  title: string
  facade?: { command?: string; remark?: string }
}

interface CloneWorkflowPanelProps {
  open: boolean
  isPending?: boolean
  onSubmit: (input: CloneWorkflowFormInput) => Promise<void>
}

export default function CloneWorkflowPanel({ open, isPending, onSubmit }: CloneWorkflowPanelProps) {
  const candidatesQuery = useWorkflowCloneCandidates(open)
  const candidates = candidatesQuery.data ?? []
  const [search, setSearch] = useState('')
  const [sourceWorkflowId, setSourceWorkflowId] = useState('')
  const [previewOpen, setPreviewOpen] = useState(true)
  const [workflowId, setWorkflowId] = useState('')
  const [title, setTitle] = useState('')
  const [command, setCommand] = useState('')
  const [remark, setRemark] = useState('')
  const [commandError, setCommandError] = useState<string | null>(null)
  const previewQuery = useWorkflowClonePreview(sourceWorkflowId, open && Boolean(sourceWorkflowId))

  useEffect(() => {
    if (!open) return
    setSearch('')
    setSourceWorkflowId('')
    setPreviewOpen(true)
    setWorkflowId('')
    setTitle('')
    setCommand('')
    setRemark('')
    setCommandError(null)
  }, [open])

  const idError = useMemo(() => {
    const id = workflowId.trim()
    if (!id) return null
    if (/\s/.test(id)) return '工作流 ID 不能包含空格'
    if (id.startsWith('__') && id.endsWith('__')) return '工作流 ID 不能使用保留前缀 __foo__ 格式'
    if (!isValidTargetWorkflowId(id)) {
      return '工作流 ID 需以字母、数字或下划线开头，只能包含字母、数字、下划线、点、连字符，且不能以 `.` 或 `.lock` 结尾'
    }
    return null
  }, [workflowId])

  useEffect(() => {
    const cmd = command.trim()
    if (!cmd) {
      setCommandError(null)
      return
    }
    setCommandError(COMMAND_PATTERN.test(cmd)
      ? null
      : '命令必须为 kebab-case 或 snake_case（小写字母、数字、连字符、下划线）')
  }, [command])

  const query = search.trim().toLowerCase()
  const visibleCandidates = useMemo(() => {
    return query
      ? candidates.filter((candidate) =>
        candidate.workflowId.toLowerCase().includes(query)
        || candidate.title.toLowerCase().includes(query))
      : candidates
  }, [candidates, query])

  const selectedCandidate = useMemo(
    () => candidates.find((candidate) => candidate.workflowId === sourceWorkflowId) ?? null,
    [candidates, sourceWorkflowId],
  )

  const canSubmit = Boolean(selectedCandidate)
    && !!workflowId.trim()
    && !!title.trim()
    && !idError
    && !commandError

  const handleSubmit = useCallback(async () => {
    if (!selectedCandidate || !canSubmit || isPending) return
    const targetWorkflowId = workflowId.trim()
    const cmd = command.trim()
    const facade = cmd ? { command: cmd, remark: remark.trim() || undefined } : undefined
    await onSubmit({
      sourceWorkflowId: selectedCandidate.workflowId,
      targetWorkflowId,
      title: title.trim(),
      facade,
    })
  }, [selectedCandidate, canSubmit, isPending, workflowId, title, command, remark, onSubmit])

  return (
    <div className="space-y-4">
      <div>
        <label htmlFor="clone-source-search" className="mb-1.5 block text-sm font-medium text-foreground">
          搜索源工作流
        </label>
        <input
          id="clone-source-search"
          type="search"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="按名称或 ID 搜索"
          disabled={isPending}
          className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm text-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
        />
      </div>

      <section aria-label="候选源工作流" className="rounded-md border border-border bg-card">
        {candidatesQuery.isLoading ? (
          <p className="px-3 py-4 text-sm text-muted-foreground">正在读取候选工作流…</p>
        ) : candidatesQuery.isError ? (
          <div className="px-3 py-3 text-sm text-destructive">
            <p>候选工作流读取失败。</p>
            <button
              type="button"
              onClick={() => void candidatesQuery.refetch()}
              className="mt-1 text-primary underline"
            >
              重试
            </button>
          </div>
        ) : visibleCandidates.length === 0 ? (
          <p className="px-3 py-4 text-sm text-muted-foreground">没有匹配的候选工作流。</p>
        ) : (
          <ul className="max-h-56 divide-y divide-border overflow-y-auto">
            {visibleCandidates.map((candidate) => (
              <li key={candidate.workflowId}>
                <button
                  type="button"
                  disabled={isPending}
                  aria-pressed={candidate.workflowId === sourceWorkflowId}
                  onClick={() => {
                    setSourceWorkflowId(candidate.workflowId)
                    setPreviewOpen(true)
                  }}
                  className={`flex w-full flex-col gap-0.5 px-3 py-2 text-left disabled:opacity-50 ${
                    candidate.workflowId === sourceWorkflowId
                      ? 'border-l-2 border-primary bg-primary/10 text-primary'
                      : 'border-l-2 border-transparent text-foreground hover:bg-muted'
                  }`}
                >
                  <span className="text-sm font-medium">{candidate.title}</span>
                  <span className="font-mono text-xs text-muted-foreground">{candidate.workflowId}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

      {selectedCandidate && (
        <section className="rounded-md border border-input bg-card p-3" aria-label="源工作流只读预览">
          <button
            type="button"
            aria-expanded={previewOpen}
            aria-controls="clone-source-preview"
            onClick={() => setPreviewOpen((opening) => !opening)}
            className="text-sm font-medium text-primary"
          >
            {previewOpen ? '收起源工作流预览' : '展开源工作流预览'}
          </button>
          <div id="clone-source-preview" hidden={!previewOpen} className="mt-2 space-y-2">
            <dl className="grid grid-cols-1 gap-1 text-xs text-muted-foreground">
              <div><dt className="font-medium">源 ID</dt><dd className="break-all font-mono text-foreground">{selectedCandidate.workflowId}</dd></div>
              <div><dt className="font-medium">DB Pack</dt><dd className="break-all font-mono text-foreground">{selectedCandidate.packId ?? '未设置'}</dd></div>
              {previewQuery.isLoading ? (
                <div><dt className="font-medium">快照</dt><dd>正在读取…</dd></div>
              ) : previewQuery.isError ? (
                <div><dt className="font-medium">快照</dt><dd className="text-destructive">读取失败，可重新选择以重试</dd></div>
              ) : previewQuery.data ? (<>
                <div><dt className="font-medium">Git Pack</dt><dd className="break-all font-mono text-foreground">{previewQuery.data.sourcePackId}</dd></div>
                <div><dt className="font-medium">源 Git commit</dt><dd className="break-all font-mono text-foreground">{previewQuery.data.baseCommit}</dd></div>
                <div><dt className="font-medium">脚本数量</dt><dd className="text-foreground">{previewQuery.data.scriptCount}</dd></div>
              </>) : null}
            </dl>
          </div>
        </section>
      )}

      <div>
        <label htmlFor="clone-workflow-id" className="mb-1.5 block text-sm font-medium text-foreground">
          新工作流 ID <span className="text-destructive">*</span>
        </label>
        <input
          id="clone-workflow-id"
          type="text"
          value={workflowId}
          onChange={(event) => setWorkflowId(event.target.value)}
          placeholder="例如 my-workflow-copy"
          disabled={isPending}
          className={`w-full rounded-md border bg-background px-3 py-2 text-sm text-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50 ${
            idError ? 'border-destructive' : 'border-input'
          }`}
        />
        {idError
          ? <p className="mt-1 text-xs text-destructive">{idError}</p>
          : <p className="mt-1 text-xs text-muted-foreground">独立 Pack ID 将默认使用该 ID。</p>}
      </div>

      <div>
        <label htmlFor="clone-workflow-title" className="mb-1.5 block text-sm font-medium text-foreground">
          新工作流标题 <span className="text-destructive">*</span>
        </label>
        <input
          id="clone-workflow-title"
          type="text"
          value={title}
          onChange={(event) => setTitle(event.target.value)}
          placeholder="例如 复制后的工作流"
          disabled={isPending}
          className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm text-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
        />
      </div>

      <div>
        <label htmlFor="clone-command" className="mb-1.5 block text-sm font-medium text-foreground">
          触发命令（可选）
        </label>
        <div className="relative">
          <span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-sm text-muted-foreground">/</span>
          <input
            id="clone-command"
            type="text"
            value={command}
            onChange={(event) => setCommand(event.target.value)}
            placeholder="填写独立的新命令"
            disabled={isPending}
            className={`w-full rounded-md border bg-background py-2 pl-7 pr-3 text-sm text-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50 ${
              commandError ? 'border-destructive' : 'border-input'
            }`}
          />
        </div>
        {commandError
          ? <p className="mt-1 text-xs text-destructive">{commandError}</p>
          : <p className="mt-1 text-xs text-muted-foreground">不会复制源工作流命令，可留空。</p>}
      </div>

      <div>
        <label htmlFor="clone-remark" className="mb-1.5 block text-sm font-medium text-foreground">
          命令说明（可选）
        </label>
        <input
          id="clone-remark"
          type="text"
          maxLength={256}
          value={remark}
          onChange={(event) => setRemark(event.target.value)}
          placeholder="仅说明新命令的用途"
          disabled={isPending}
          className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm text-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
        />
      </div>

      <button
        type="button"
        onClick={() => void handleSubmit()}
        disabled={!canSubmit || isPending}
        className="w-full rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground disabled:cursor-not-allowed disabled:opacity-40"
      >
        {isPending ? '创建中…' : '基于源工作流创建'}
      </button>
    </div>
  )
}
