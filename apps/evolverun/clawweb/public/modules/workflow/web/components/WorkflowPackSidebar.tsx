import { useState, useMemo, useCallback } from 'react'
import { useWorkflowsPage, useFacadesPage, useFacadeBindings } from '../api/hooks'

interface SidebarWorkflowItem {
  workflowId: string
  title: string
  packId: string | null
  command?: string
  remark?: string
}

interface WorkflowPackSidebarProps {
  onSelectWorkflow: (workflowId: string) => void
  selectedWorkflowId: string | null
}

const PAGE_SIZE = 50

export default function WorkflowPackSidebar({
  onSelectWorkflow,
  selectedWorkflowId,
}: WorkflowPackSidebarProps) {
  const [search, setSearch] = useState('')
  const [debouncedSearch, setDebouncedSearch] = useState('')
  const [page, setPage] = useState(1)
  const [activeTab, setActiveTab] = useState<'workflows' | 'facades'>('workflows')

  // Debounce search input (300ms)
  const [searchTimer, setSearchTimer] = useState<ReturnType<typeof setTimeout> | null>(null)
  const handleSearchChange = useCallback((value: string) => {
    setSearch(value)
    if (searchTimer) clearTimeout(searchTimer)
    const timer = setTimeout(() => {
      setDebouncedSearch(value)
      setPage(1)
    }, 300)
    setSearchTimer(timer)
  }, [searchTimer])

  const {
    data: wfResult,
    isLoading: wfLoading,
  } = useWorkflowsPage({
    page,
    pageSize: PAGE_SIZE,
    search: debouncedSearch || undefined,
    enabled: activeTab === 'workflows',
  })

  const {
    data: fcResult,
    isLoading: fcLoading,
  } = useFacadesPage({
    page,
    pageSize: PAGE_SIZE,
    search: debouncedSearch || undefined,
    enabled: activeTab === 'facades',
  })

  // Fetch all facade bindings for badge display on workflows tab
  const { data: allFacades } = useFacadeBindings()

  const facadesMap = useMemo(() => {
    const map = new Map<string, { command: string; remark?: string }>()
    if (allFacades) {
      for (const f of allFacades) {
        map.set(f.workflowId, { command: f.command, remark: f.remark ?? undefined })
      }
    }
    return map
  }, [allFacades])

  const isLoading = activeTab === 'workflows' ? wfLoading : fcLoading
  const currentData = activeTab === 'workflows' ? wfResult : fcResult
  const pagination = currentData?.pagination
  const totalPages = pagination?.totalPages ?? 1

  // Merge workflows with facade info for the workflows tab
  const workflowItems: SidebarWorkflowItem[] = useMemo(() => {
    if (activeTab !== 'workflows' || !wfResult?.data) return []
    return wfResult.data.map((wf) => {
      const facade = facadesMap.get(wf.workflowId)
      return {
        workflowId: wf.workflowId,
        title: wf.title,
        packId: wf.packId,
        command: facade?.command,
        remark: facade?.remark,
      }
    })
  }, [activeTab, wfResult?.data, facadesMap])

  // Facade items directly from facades tab
  const facadeItems: SidebarWorkflowItem[] = useMemo(() => {
    if (activeTab !== 'facades' || !fcResult?.data) return []
    return fcResult.data.map((fc) => ({
      workflowId: fc.workflowId,
      title: fc.command,
      packId: fc.packId,
      command: fc.command,
      remark: fc.remark ?? undefined,
    }))
  }, [activeTab, fcResult?.data])

  const items = activeTab === 'workflows' ? workflowItems : facadeItems

  return (
    <div className="flex h-full w-64 flex-col border-r border-gray-200 bg-white">
      {/* Header with search */}
      <div className="border-b border-gray-200 px-3 py-2">
        <div className="relative">
          <svg
            className="absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-gray-400"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
          </svg>
          <input
            type="text"
            value={search}
            onChange={(e) => handleSearchChange(e.target.value)}
            placeholder="搜索工作流..."
            className="w-full rounded-md border border-gray-200 bg-gray-50 py-1.5 pl-8 pr-2 text-xs text-gray-700 placeholder-gray-400 focus:border-blue-400 focus:bg-white focus:outline-none focus:ring-1 focus:ring-blue-400"
          />
        </div>
      </div>

      {/* Tabs */}
      <div className="flex border-b border-gray-200">
        <button
          onClick={() => { setActiveTab('workflows'); setPage(1) }}
          className={`flex-1 px-3 py-2 text-xs font-medium transition-colors ${
            activeTab === 'workflows'
              ? 'border-b-2 border-blue-600 text-blue-700'
              : 'text-gray-500 hover:text-gray-700'
          }`}
        >
          工作流
          {wfResult?.pagination && (
            <span className="ml-1 rounded-full bg-gray-100 px-1.5 text-[10px] text-gray-500">
              {wfResult.pagination.total}
            </span>
          )}
        </button>
        <button
          onClick={() => { setActiveTab('facades'); setPage(1) }}
          className={`flex-1 px-3 py-2 text-xs font-medium transition-colors ${
            activeTab === 'facades'
              ? 'border-b-2 border-blue-600 text-blue-700'
              : 'text-gray-500 hover:text-gray-700'
          }`}
        >
          命令
          {fcResult?.pagination && (
            <span className="ml-1 rounded-full bg-gray-100 px-1.5 text-[10px] text-gray-500">
              {fcResult.pagination.total}
            </span>
          )}
        </button>
      </div>

      {/* List */}
      <nav className="flex-1 overflow-y-auto px-2 py-1">
        {isLoading && (
          <div className="flex items-center justify-center py-8">
            <div className="h-4 w-4 animate-spin rounded-full border-2 border-gray-300 border-t-blue-600" />
          </div>
        )}

        {!isLoading && items.length === 0 && (
          <p className="px-3 py-4 text-gray-400 text-xs italic">
            {debouncedSearch ? '未找到匹配项' : '暂无数据'}
          </p>
        )}

        {!isLoading && items.map((item) => {
          const isSelected = selectedWorkflowId === item.workflowId
          return (
            <button
              key={item.workflowId}
              onClick={() => onSelectWorkflow(item.workflowId)}
              className={`flex w-full items-center gap-1 rounded-md px-2 py-1.5 text-left text-sm transition-colors hover:bg-gray-100 ${
                isSelected ? 'bg-blue-50 text-blue-700' : 'text-gray-600'
              }`}
            >
              <svg className="h-3.5 w-3.5 shrink-0 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 7v10c0 2.21 3.582 4 8 4s8-1.79 8-4V7M4 7c0 2.21 3.582 4 8 4s8-1.79 8-4M4 7c0-2.21 3.582-4 8-4s8 1.79 8 4" />
              </svg>
              <span className="truncate flex-1">{item.title}</span>
              {item.command && (
                <span className="ml-auto shrink-0 rounded bg-indigo-50 px-1 py-0.5 font-mono text-[10px] text-indigo-600">
                  /{item.command}
                </span>
              )}
            </button>
          )
        })}
      </nav>

      {/* Pagination */}
      {pagination && pagination.totalPages > 1 && (
        <div className="flex items-center justify-between border-t border-gray-200 px-3 py-1.5">
          <button
            onClick={() => setPage((p) => Math.max(1, p - 1))}
            disabled={page <= 1}
            className="rounded px-1.5 py-0.5 text-xs text-gray-600 hover:bg-gray-100 disabled:opacity-30"
          >
            ‹
          </button>
          <span className="text-[11px] text-gray-500">
            {page} / {totalPages}
          </span>
          <button
            onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
            disabled={page >= totalPages}
            className="rounded px-1.5 py-0.5 text-xs text-gray-600 hover:bg-gray-100 disabled:opacity-30"
          >
            ›
          </button>
        </div>
      )}
    </div>
  )
}