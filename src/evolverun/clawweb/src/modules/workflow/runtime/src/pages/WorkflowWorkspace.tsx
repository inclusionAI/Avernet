import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useCreateWorkflow, useEvolveDiagnoses, useEvolveLessons, useWorkflowTypes } from '../api/hooks'
import { getClientUser } from '../hooks/useClientUser'
import Sidebar, { type WorkspaceView } from '../components/workflow-workspace/Sidebar'
import OverviewTab from '../components/workflow-workspace/OverviewTab'
import EditorTab from '../components/workflow-workspace/EditorTab'
import ManagementTab from '../components/workflow-workspace/ManagementTab'
import EvolutionTab from '../components/workflow-workspace/EvolutionTab'
import CreateWorkflowModal from '../components/workflow-workspace/CreateWorkflowModal'
import WorkflowPicker from '../components/workflow-workspace/WorkflowPicker'
import StatusBadge from '../components/StatusBadge'
import EmptyState from '../components/EmptyState'
import ErrorState from '../components/ErrorState'
import Dashboard from './Dashboard'
import type { WorkflowSpec } from '../types'

const VIEW_META: Record<Exclude<WorkspaceView, 'dashboard'>, { title: string; description: string }> = {
  overview: { title: '运行概览', description: '查看当前工作流的运行状态与近期表现' },
  diagnosis: { title: '问题与优化', description: '从异常证据到建议、应用与效果验证' },
  remedies: { title: '可复用经验', description: '查看经过边界审核、可复用的知识与修法' },
  editor: { title: '编辑器', description: '编辑工作流结构、节点与执行配置' },
  management: { title: '管理设置', description: '管理版本、权限、通知与回调配置' },
}

function viewFromSearch(params: URLSearchParams, isAdmin: boolean): WorkspaceView {
  const tab = params.get('tab')
  if (tab === 'dashboard') return isAdmin ? 'dashboard' : 'diagnosis'
  if (tab === 'overview' || tab === 'editor' || tab === 'management') return tab
  if (tab === 'evolution') {
    const section = params.get('evoTab')
    if (section === 'remedies') return section
    if (section === 'suggestions' || section === 'diagnosis') return 'diagnosis'
    return 'diagnosis'
  }
  return 'overview'
}

export default function WorkflowWorkspace() {
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const user = getClientUser()
  const isAdmin = user?.isAdmin === true
  const activeView = viewFromSearch(searchParams, isAdmin)
  const urlWorkflowId = searchParams.get('workflowId')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [legacyOpen, setLegacyOpen] = useState(false)
  const [createOpen, setCreateOpen] = useState(false)

  const { data: workflows, isLoading, isError, error, refetch } = useWorkflowTypes(isAdmin ? undefined : user?.userId)
  const createMutation = useCreateWorkflow()
  const selectedWorkflow = useMemo(() => {
    if (!workflows) return null
    const urlSelection = urlWorkflowId && workflows.some((workflow) => workflow.workflow_id === urlWorkflowId)
      ? urlWorkflowId
      : null
    return workflows.find((workflow) => workflow.workflow_id === (urlSelection ?? selectedId)) ?? workflows[0] ?? null
  }, [workflows, selectedId, urlWorkflowId])
  const workflowId = selectedWorkflow?.workflow_id ?? ''
  const diagnosesQ = useEvolveDiagnoses({ workflowId, limit: 100, enabled: Boolean(workflowId) })
  const lessonsQ = useEvolveLessons({ workflowId, limit: 100, enabled: Boolean(workflowId) })
  const counts = {
    diagnosis: new Set((diagnosesQ.data?.diagnoses ?? []).map((item) => item.failure_signature)).size,
    remedies: lessonsQ.data?.lessons.length ?? 0,
  }

  useEffect(() => {
    if (!legacyOpen) return
    const close = (event: KeyboardEvent) => { if (event.key === 'Escape') setLegacyOpen(false) }
    window.addEventListener('keydown', close)
    return () => window.removeEventListener('keydown', close)
  }, [legacyOpen])

  const changeView = useCallback((view: WorkspaceView) => {
    const next = new URLSearchParams(searchParams)
    if (selectedWorkflow) next.set('workflowId', selectedWorkflow.workflow_id)
    if (view === 'dashboard' || view === 'overview' || view === 'editor' || view === 'management') {
      next.set('tab', view)
      next.delete('evoTab')
    } else {
      next.set('tab', 'evolution')
      next.set('evoTab', view)
    }
    setSearchParams(next)
  }, [searchParams, selectedWorkflow, setSearchParams])

  const selectWorkflow = (id: string) => {
    setSelectedId(id)
    const next = new URLSearchParams(searchParams)
    next.set('workflowId', id)
    if (activeView === 'dashboard') {
      next.set('tab', 'evolution')
      next.set('evoTab', 'diagnosis')
    }
    setSearchParams(next)
  }

  const handleDeleted = () => {
    const remaining = workflows?.filter((workflow) => workflow.workflow_id !== selectedId) ?? []
    selectWorkflow(remaining[0]?.workflow_id ?? '')
  }

  const handleCreate = useCallback(async (input: { workflowId: string; spec: WorkflowSpec; facade?: { command?: string; remark?: string } }) => {
    if (!user?.userId) throw new Error('无法获取用户信息，请重新登录')
    await createMutation.mutateAsync({ workflowId: input.workflowId, spec: input.spec, facade: input.facade, botOwnerId: user.userId })
    setSelectedId(input.workflowId)
    setSearchParams({ workflowId: input.workflowId, tab: 'editor' })
  }, [createMutation, setSearchParams, user?.userId])

  if (isError) return <div className="p-6"><ErrorState message={error instanceof Error ? error.message : '加载工作流失败'} onRetry={() => void refetch()} /></div>

  return <div className="flex h-[calc(100vh-3.5rem)] overflow-hidden bg-slate-50">
    <Sidebar activeView={activeView} onViewChange={changeView} isAdmin={isAdmin} hasWorkflow={Boolean(selectedWorkflow)} counts={counts} />

    <main className="flex min-w-0 flex-1 flex-col bg-slate-50">
      {activeView === 'dashboard' && isAdmin ? (
        <div className="min-h-0 flex-1 overflow-y-auto"><Dashboard embedded /></div>
      ) : selectedWorkflow ? (
        <>
          <header className="flex items-start justify-between gap-4 border-b border-slate-200 bg-white px-7 py-4">
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                <h1 className="truncate text-lg font-semibold tracking-tight text-slate-950">{VIEW_META[activeView as Exclude<WorkspaceView, 'dashboard'>].title}</h1>
                <span className="text-xs text-slate-300">/</span>
                <WorkflowPicker workflows={workflows ?? []} selectedId={selectedWorkflow.workflow_id} onSelect={selectWorkflow} loading={isLoading} />
                <button
                  type="button"
                  aria-label="新建工作流"
                  onClick={() => setCreateOpen(true)}
                  className="inline-flex items-center gap-1 rounded-lg border border-slate-200 bg-white px-2 py-1 text-xs font-medium text-slate-600 shadow-sm transition hover:border-blue-200 hover:bg-blue-50 hover:text-blue-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/30"
                >
                  <svg aria-hidden="true" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth="1.8" viewBox="0 0 24 24">
                    <path d="M12 5v14M5 12h14" strokeLinecap="round" />
                  </svg>
                  <span>新建</span>
                </button>
              </div>
              <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-slate-400">
                <span>{VIEW_META[activeView as Exclude<WorkspaceView, 'dashboard'>].description}</span><span>·</span><span className="font-mono">{selectedWorkflow.workflow_id}</span><span>·</span><span>{selectedWorkflow.run_count} 次运行</span><StatusBadge status={(selectedWorkflow.last_status as import('../types').NodeStatus) ?? 'pending'} />
              </div>
            </div>
            <div className="relative shrink-0">
              <button type="button" onClick={() => setLegacyOpen((open) => !open)} title="旧版入口" className="rounded-lg px-2 py-1 text-sm text-slate-300 hover:bg-slate-100 hover:text-slate-500">···</button>
              {legacyOpen && <><div className="fixed inset-0 z-10" onClick={() => setLegacyOpen(false)} /><div className="absolute right-0 top-full z-20 mt-1 w-40 overflow-hidden rounded-lg border border-slate-200 bg-white py-1 shadow-lg"><button onClick={() => { setLegacyOpen(false); navigate('/workflows') }} className="block w-full px-3 py-2 text-left text-xs text-slate-600 hover:bg-slate-50">旧版工作流</button><button onClick={() => { setLegacyOpen(false); navigate('/editor') }} className="block w-full px-3 py-2 text-left text-xs text-slate-600 hover:bg-slate-50">旧版编辑器</button>{isAdmin && <button onClick={() => { setLegacyOpen(false); navigate('/workflow-management') }} className="block w-full px-3 py-2 text-left text-xs text-slate-600 hover:bg-slate-50">旧版工作流管理</button>}</div></>}
            </div>
          </header>
          {activeView === 'editor' ? <div className="min-h-0 flex-1 overflow-hidden"><EditorTab workflowId={workflowId} /></div> : <div className="min-h-0 flex-1 overflow-y-auto p-6">
            {activeView === 'overview' && <OverviewTab workflow={selectedWorkflow} />}
            {activeView === 'management' && <ManagementTab workflowId={workflowId} workflowTitle={selectedWorkflow.workflow_title} onDeleted={handleDeleted} />}
            {(activeView === 'diagnosis' || activeView === 'remedies') && <EvolutionTab
              workflowId={workflowId}
              runId={searchParams.get('runId') ?? undefined}
              analysisId={searchParams.get('analysisId') ?? undefined}
              issueSignature={searchParams.get('issueSignature') ?? undefined}
              section={activeView}
              onSectionChange={changeView}
            />}
          </div>}
        </>
      ) : <div className="flex flex-1 items-center justify-center"><EmptyState title="暂无可访问的工作流" description="新建工作流后即可开始任务护航" /></div>}
    </main>

    <CreateWorkflowModal open={createOpen} onClose={() => setCreateOpen(false)} onSubmit={handleCreate} isPending={createMutation.isPending} />
  </div>
}
