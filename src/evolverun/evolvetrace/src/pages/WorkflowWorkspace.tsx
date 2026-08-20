import { useEffect, useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useWorkflowTypes } from '../api/hooks'
import { getClientUser } from '../hooks/useClientUser'
import Sidebar from '../components/workflow-workspace/Sidebar'
import OverviewTab from '../components/workflow-workspace/OverviewTab'
import EditorTab from '../components/workflow-workspace/EditorTab'
import ManagementTab from '../components/workflow-workspace/ManagementTab'
import EvolutionTab from '../components/workflow-workspace/EvolutionTab'
import StatusBadge from '../components/StatusBadge'
import EmptyState from '../components/EmptyState'
import ErrorState from '../components/ErrorState'
import type { NodeStatus } from '../types'

type WorkspaceTab = 'overview' | 'editor' | 'management' | 'evolution'

const TAB_CONFIG: { key: WorkspaceTab; label: string }[] = [
  { key: 'overview', label: '概览' },
  { key: 'editor', label: '编辑器' },
  { key: 'management', label: '管理' },
  { key: 'evolution', label: '进化' },
]

export default function WorkflowWorkspace() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const urlWorkflowId = searchParams.get('workflowId')
  const user = getClientUser()
  const isAdmin = user?.isAdmin === true

  const [search, setSearch] = useState('')
  const [selectedId, setSelectedId] = useState<string | null>(urlWorkflowId)
  const [activeTab, setActiveTab] = useState<WorkspaceTab>('overview')
  const [legacyOpen, setLegacyOpen] = useState(false)

  const {
    data: workflows,
    isLoading,
    isError,
    error,
    refetch,
  } = useWorkflowTypes(isAdmin ? undefined : user?.userId)

  const filteredWorkflows = useMemo(() => {
    if (!workflows) return []
    if (!search.trim()) return workflows
    const q = search.toLowerCase()
    return workflows.filter(
      (w) =>
        w.workflow_id.toLowerCase().includes(q) ||
        (w.workflow_title ?? '').toLowerCase().includes(q),
    )
  }, [workflows, search])

  useEffect(() => {
    if (!selectedId && filteredWorkflows.length > 0) {
      setSelectedId(filteredWorkflows[0].workflow_id)
    }
  }, [filteredWorkflows, selectedId])

  useEffect(() => {
    if (urlWorkflowId && workflows?.some((w) => w.workflow_id === urlWorkflowId)) {
      setSelectedId(urlWorkflowId)
    }
  }, [urlWorkflowId, workflows])

  useEffect(() => {
    if (!legacyOpen) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setLegacyOpen(false)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [legacyOpen])

  const selectedWorkflow = useMemo(() => {
    if (!workflows) return null
    const fromList = workflows.find((w) => w.workflow_id === selectedId)
    if (fromList) return fromList
    if (filteredWorkflows.length > 0) return filteredWorkflows[0]
    return workflows[0] ?? null
  }, [workflows, selectedId, filteredWorkflows])

  useEffect(() => {
    if (selectedWorkflow && selectedWorkflow.workflow_id !== selectedId) {
      setSelectedId(selectedWorkflow.workflow_id)
    }
  }, [selectedWorkflow, selectedId])

  const handleDeleted = () => {
    const remaining = workflows?.filter((w) => w.workflow_id !== selectedId) ?? []
    setSelectedId(remaining[0]?.workflow_id ?? null)
  }

  if (isError) {
    return (
      <div className="p-6">
        <ErrorState
          message={error instanceof Error ? error.message : '加载工作流失败'}
          onRetry={() => void refetch()}
        />
      </div>
    )
  }

  if (!isLoading && (!workflows || workflows.length === 0)) {
    return (
      <div className="p-6">
        <EmptyState title="暂无工作流" description="当前账号暂无可访问的工作流" />
      </div>
    )
  }

  return (
    <div className="flex h-[calc(100vh-3.5rem)] overflow-hidden">
      <Sidebar
        workflows={filteredWorkflows}
        selectedId={selectedWorkflow?.workflow_id ?? null}
        search={search}
        onSearchChange={setSearch}
        onSelect={setSelectedId}
        loading={isLoading}
      />

      <main className="flex min-w-0 flex-1 flex-col bg-gray-50">
        {selectedWorkflow ? (
          <>
            <div className="border-b border-gray-200 bg-white px-6 py-4">
              <div className="flex items-start justify-between gap-4">
                <div className="min-w-0">
                  <h1 className="truncate text-xl font-bold text-gray-900">
                    {selectedWorkflow.workflow_title || selectedWorkflow.workflow_id}
                  </h1>
                  <div className="mt-1 flex items-center gap-3 text-sm text-gray-500">
                    <span className="font-mono text-xs">{selectedWorkflow.workflow_id}</span>
                    <span>·</span>
                    <span>{selectedWorkflow.run_count} runs</span>
                    <span>·</span>
                    <StatusBadge status={(selectedWorkflow.last_status as NodeStatus) ?? 'pending'} />
                  </div>
                </div>
                <div className="relative shrink-0">
                  <button
                    onClick={() => setLegacyOpen((v) => !v)}
                    title="旧版入口"
                    className="rounded-md px-2 py-1 text-sm text-gray-300 transition-colors hover:bg-gray-100 hover:text-gray-500"
                  >
                    ···
                  </button>
                  {legacyOpen && (
                    <>
                      <div className="fixed inset-0 z-10" onClick={() => setLegacyOpen(false)} />
                      <div className="absolute right-0 top-full z-20 mt-1 w-40 overflow-hidden rounded-md border border-gray-200 bg-white py-1 shadow-lg">
                        <button
                          onClick={() => { setLegacyOpen(false); navigate('/workflows') }}
                          className="block w-full px-3 py-1.5 text-left text-xs text-gray-600 transition-colors hover:bg-gray-50"
                        >
                          旧版工作流
                        </button>
                        <button
                          onClick={() => { setLegacyOpen(false); navigate('/editor') }}
                          className="block w-full px-3 py-1.5 text-left text-xs text-gray-600 transition-colors hover:bg-gray-50"
                        >
                          旧版编辑器
                        </button>
                        {isAdmin && (
                          <button
                            onClick={() => { setLegacyOpen(false); navigate('/workflow-management') }}
                            className="block w-full px-3 py-1.5 text-left text-xs text-gray-600 transition-colors hover:bg-gray-50"
                          >
                            旧版工作流管理
                          </button>
                        )}
                      </div>
                    </>
                  )}
                </div>
              </div>

              <div className="mt-4 flex items-center gap-1 border-b border-gray-200">
                {TAB_CONFIG.map((tab) => (
                  <button
                    key={tab.key}
                    onClick={() => setActiveTab(tab.key)}
                    className={`border-b-2 px-4 py-2 text-sm font-medium transition-colors ${
                      activeTab === tab.key
                        ? 'border-blue-600 text-blue-600'
                        : 'border-transparent text-gray-500 hover:text-gray-700'
                    }`}
                  >
                    {tab.label}
                  </button>
                ))}
              </div>
            </div>

            {activeTab === 'editor' ? (
              <div className="min-h-0 flex-1 overflow-hidden">
                <EditorTab workflowId={selectedWorkflow.workflow_id} />
              </div>
            ) : (
              <div className="flex-1 overflow-y-auto p-6">
                {activeTab === 'overview' && <OverviewTab workflow={selectedWorkflow} />}
                {activeTab === 'management' && (
                  <ManagementTab
                    workflowId={selectedWorkflow.workflow_id}
                    workflowTitle={selectedWorkflow.workflow_title}
                    onDeleted={handleDeleted}
                  />
                )}
                {activeTab === 'evolution' && (
                  <EvolutionTab workflowId={selectedWorkflow.workflow_id} />
                )}
              </div>
            )}
          </>
        ) : (
          <div className="flex flex-1 items-center justify-center">
            <EmptyState title="选择工作流" description="从左侧列表选择一个工作流开始护航" />
          </div>
        )}
      </main>
    </div>
  )
}
