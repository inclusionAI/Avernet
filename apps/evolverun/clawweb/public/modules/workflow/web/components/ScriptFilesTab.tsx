import { useEffect, useState } from 'react'
import { fetchJson } from '@avernet/clawweb-shared/web/api/client'
import ScriptFilesPanel, { type ScriptFile } from './ScriptFilesPanel'

type Snapshot = {
  workflowId: string
  packId: string
  baseRef: string
  baseCommit: string
  files: Array<{ path: string; content: string; mode: '100644' | '100755' }>
}

type State = { workflowId: string; loading: boolean; error: string | null; snapshot: Snapshot | null }
const emptyState = (workflowId: string): State => ({ workflowId, loading: true, error: null, snapshot: null })

// Only inspect known structured responses; never display server-provided text.
function notFoundMessage(error: unknown): string {
  const body = (error as { body?: unknown } | null)?.body
  if (typeof body === 'string') {
    try {
      const value: unknown = JSON.parse(body)
      if (value && typeof value === 'object' && !Array.isArray(value)) {
        const result = value as { code?: unknown; error?: unknown }
        if (result.code === 'WORKFLOW_NOT_FOUND' || (result.code === undefined && result.error === 'Workflow not found')) return '未找到工作流。'
        if (result.code === 'SNAPSHOT_NOT_FOUND' || (result.code === undefined && result.error === 'Snapshot not found')) return '未找到工作流的 Git 快照或 Pack 清单。'
      }
    } catch {
      // Older hosts and proxies may return HTML or plain text instead of JSON.
    }
  }
  return '接口或资源未找到，请检查服务版本和路由配置。'
}

function readError(error: unknown): string {
  const status = (error as { status?: unknown } | null)?.status
  switch (status) {
    case 401: return '请先登录，再查看脚本。'
    case 403: return '没有工作流编辑权限或 Pack 访问权限，无法查看脚本。'
    case 404: return notFoundMessage(error)
    case 409: return '脚本快照存在分支冲突或共享 Pack，暂无法安全读取。'
    case 503: return '脚本服务暂不可用，请检查配置后重试。'
    default: return '无法读取脚本，请稍后重试。'
  }
}

/** Read-only until the complete Pack save and exact-candidate publication contract exists. */
export default function ScriptFilesTab({ workflowId }: { workflowId: string | null }) {
  const [state, setState] = useState<State>(() => emptyState(workflowId ?? ''))
  const [selected, setSelected] = useState<string | null>(null)
  const [retry, setRetry] = useState(0)

  useEffect(() => {
    if (!workflowId) return
    const controller = new AbortController()
    setState(emptyState(workflowId))
    setSelected(null)
    void fetchJson<Snapshot>(`/api/workflows/${encodeURIComponent(workflowId)}/pack-scripts`, { signal: controller.signal })
      .then(snapshot => {
        if (controller.signal.aborted) return
        if (snapshot.workflowId !== workflowId || !Array.isArray(snapshot.files) || !/^[0-9a-f]{40,64}$/.test(snapshot.baseCommit)) throw new Error('Invalid snapshot')
        setState({ workflowId, loading: false, error: null, snapshot })
        setSelected(snapshot.files[0]?.path ?? null)
      })
      .catch(error => {
        if (!controller.signal.aborted) setState({ workflowId, loading: false, error: readError(error), snapshot: null })
      })
    return () => controller.abort()
  }, [workflowId, retry])

  if (!workflowId) return <p className="p-5 text-sm text-muted-foreground">请先保存工作流，再查看脚本文件。</p>
  const current = state.workflowId === workflowId ? state : emptyState(workflowId)
  const files: ScriptFile[] = current.snapshot?.files.map(file => ({ path: file.path, content: file.content, savedContent: file.content, isNew: false })) ?? []
  return <div className="h-full overflow-auto p-4">
    <div className="mb-4 rounded-lg border border-border bg-muted p-3 text-xs text-muted-foreground" role="status">
      脚本读取需通过 Pack 级授权；脚本编辑和发布暂不可用，需先完成完整 Pack 保存与精确版本部署。
      {current.snapshot && <p className="mt-1 break-all">Pack：{current.snapshot.packId} · {current.snapshot.baseRef} · commit {current.snapshot.baseCommit}</p>}
    </div>
    {!current.loading && !current.error && files.length === 0 && <p className="mb-3 text-sm text-muted-foreground">当前快照没有脚本文件。</p>}
    <ScriptFilesPanel files={files} selectedPath={selected} onSelect={setSelected}
      readOnly loading={current.loading} error={current.error} onRetry={() => setRetry(value => value + 1)} />
  </div>
}
