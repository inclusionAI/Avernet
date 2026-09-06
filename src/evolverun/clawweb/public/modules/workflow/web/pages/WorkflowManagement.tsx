import { useState, useMemo, useCallback, useEffect } from 'react'
import { useDebounce } from '@avernet/workflow/web/hooks/useDebounce'
import { getClientUser } from '@avernet/clawweb-shared/web/hooks/useClientUser'
import { api } from '@avernet/clawweb-shared/web/api/client'
import WorkflowCard from '../components/WorkflowCard'
import SearchInput from '@avernet/workflow/web/components/SearchInput'
import Pagination from '../components/Pagination'
import type { FacadeBinding } from '@avernet/clawweb-shared/web/types'

interface WorkflowSummary {
  workflowId: string
  title: string
  packId: string | null
  updatedAt: number | null
}

interface PaginationInfo {
  page: number
  pageSize: number
  total: number
  totalPages: number
}

export default function WorkflowManagement() {
  const [workflows, setWorkflows] = useState<WorkflowSummary[]>([])
  const [facades, setFacades] = useState<FacadeBinding[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [pagination, setPagination] = useState<PaginationInfo | null>(null)
  const debouncedSearch = useDebounce(search, 300)

  const user = getClientUser()
  const isAdmin = user?.isAdmin === true

  const loadData = useCallback(async () => {
    setLoading(true)
    setError(null)
    let wfError: string | null = null
    let facadeError: string | null = null

    // Fetch workflows and facades independently so one failure doesn't wipe the other
    const wfPromise = api.workflows
      .listPage({ page, pageSize, search: debouncedSearch || undefined })
      .then((wfResult) => {
        setWorkflows(wfResult.data)
        setPagination(wfResult.pagination)
      })
      .catch((err) => {
        wfError = err instanceof Error ? err.message : '工作流列表加载失败'
      })

    const facadePromise = api.facades
      .list()
      .then((facadeList) => {
        setFacades(facadeList)
      })
      .catch((err) => {
        facadeError = err instanceof Error ? err.message : '命令列表加载失败'
      })

    await Promise.allSettled([wfPromise, facadePromise])

    if (wfError && facadeError) {
      setError(`${wfError}；${facadeError}`)
    } else if (wfError) {
      setError(wfError)
    } else if (facadeError) {
      setError(facadeError)
    }
    setLoading(false)
  }, [page, pageSize, debouncedSearch])

  useEffect(() => {
    void loadData()
  }, [loadData])

  const facadesByWorkflow = useMemo(() => {
    const map = new Map<string, FacadeBinding[]>()
    for (const f of facades) {
      const list = map.get(f.workflowId) ?? []
      list.push(f)
      map.set(f.workflowId, list)
    }
    return map
  }, [facades])

  const handleDelete = useCallback(async (workflowId: string) => {
    try {
      await api.workflows.delete(workflowId)
      setFacades((prev) => prev.filter((f) => f.workflowId !== workflowId))
      // Reload current page to reflect deletion
      void loadData()
    } catch (err) {
      setError(err instanceof Error ? err.message : '删除失败')
    }
  }, [loadData])

  const handlePageChange = useCallback((newPage: number, newPageSize: number) => {
    setPage(newPage)
    setPageSize(newPageSize)
  }, [])

  if (!isAdmin) {
    return (
      <div className="mx-auto max-w-7xl px-4 py-6 sm:px-6 lg:px-8">
        <div className="rounded-lg border border-red-200 bg-red-50 p-6">
          <h2 className="text-lg font-semibold text-red-800">权限不足</h2>
          <p className="mt-2 text-sm text-red-600">
            仅超级管理员可访问工作流管理页面。如需访问，请联系管理员将您加入配置中的 admins 列表。
          </p>
        </div>
      </div>
    )
  }

  return (
    <div className="mx-auto max-w-7xl px-4 py-6 sm:px-6 lg:px-8">
      {/* Header */}
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">工作流管理</h1>
          <p className="mt-1 text-sm text-gray-400">
            {pagination ? `${pagination.total} 个工作流` : '加载中…'}
          </p>
        </div>
        <button
          onClick={() => void loadData()}
          disabled={loading}
          className="inline-flex items-center gap-1.5 rounded-md border border-gray-300 bg-white px-3 py-1.5 text-sm font-medium text-gray-700 shadow-sm transition-colors hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-50"
        >
          <svg
            className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`}
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={2}
              d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"
            />
          </svg>
          {loading ? '加载中…' : '刷新'}
        </button>
      </div>

      {/* Search */}
      <div className="mb-4">
        <SearchInput
          value={search}
          onChange={(val) => { setSearch(val); setPage(1) }}
          placeholder="搜索工作流 ID 或名称…"
        />
      </div>

      {/* Error */}
      {error && (
        <div className="mb-4 rounded-lg border border-red-200 bg-red-50 p-4">
          <p className="text-sm text-red-700">{error}</p>
          <button
            onClick={() => setError(null)}
            className="mt-2 text-sm font-medium text-red-600 hover:text-red-800"
          >
            关闭
          </button>
        </div>
      )}

      {/* Content */}
      {loading ? (
        <div className="flex items-center justify-center py-16">
          <div className="h-8 w-8 animate-spin rounded-full border-2 border-gray-300 border-t-blue-600" />
        </div>
      ) : workflows.length === 0 ? (
        <div className="rounded-lg border border-gray-200 bg-white px-6 py-12 text-center">
          <p className="text-gray-500">
            {error
              ? '数据加载失败，请点击刷新重试'
              : debouncedSearch ? '未找到匹配的工作流' : '暂无工作流'}
          </p>
        </div>
      ) : (
        <>
          <div className="space-y-3">
            {workflows.map((wf) => (
              <WorkflowCard
                key={wf.workflowId}
                workflowId={wf.workflowId}
                title={wf.title}
                packId={wf.packId}
                updatedAt={wf.updatedAt != null ? String(wf.updatedAt) : null}
                facades={facadesByWorkflow.get(wf.workflowId) ?? []}
                onDelete={handleDelete}
              />
            ))}
          </div>
          {pagination && (
            <Pagination
              page={pagination.page}
              pageSize={pagination.pageSize}
              total={pagination.total}
              onChange={handlePageChange}
            />
          )}
        </>
      )}
    </div>
  )
}
