import type { WorkflowTypeRow, NodeStatus } from '../../types'
import StatusBadge from '../StatusBadge'
import SearchInput from '../SearchInput'
import EmptyState from '../EmptyState'

function WorkflowStatusBadge({ status }: { status: string | null }) {
  const safeStatus: NodeStatus = (status as NodeStatus) ?? 'pending'
  return <StatusBadge status={safeStatus} />
}

interface SidebarProps {
  workflows: WorkflowTypeRow[]
  selectedId: string | null
  search: string
  onSearchChange: (value: string) => void
  onSelect: (id: string) => void
  onCreateClick?: () => void
  loading?: boolean
}

export default function Sidebar({
  workflows,
  selectedId,
  search,
  onSearchChange,
  onSelect,
  onCreateClick,
  loading,
}: SidebarProps) {
  return (
    <aside className="flex h-full w-72 flex-col border-r border-gray-200 bg-white">
      <div className="border-b border-gray-200 p-4">
        <div className="mb-3 flex items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <svg
              className="h-5 w-5 text-gray-400"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M4 6h16M4 12h16M4 18h16"
              />
            </svg>
            <h2 className="text-sm font-semibold text-gray-900">工作流</h2>
          </div>
          {onCreateClick && (
            <button
              onClick={onCreateClick}
              title="新建工作流"
              className="flex items-center gap-1 rounded-md bg-blue-600 px-2 py-1 text-xs font-medium text-white transition-colors hover:bg-blue-700"
            >
              <svg
                className="h-3.5 w-3.5"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2.5}
                  d="M12 4v16m8-8H4"
                />
              </svg>
              新建
            </button>
          )}
        </div>
        <SearchInput
          value={search}
          onChange={onSearchChange}
          placeholder="搜索 ID / 名称…"
        />
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto p-2">
        {loading ? (
          <div className="flex items-center justify-center py-8">
            <div className="h-5 w-5 animate-spin rounded-full border-2 border-gray-300 border-t-blue-600" />
          </div>
        ) : workflows.length === 0 ? (
          <div className="px-2 py-4">
            <EmptyState
              title="无匹配工作流"
              description={search ? '换个关键词试试' : '暂无可访问的工作流'}
            />
          </div>
        ) : (
          <div className="space-y-1">
            {workflows.map((wf) => {
              const isSelected = selectedId === wf.workflow_id
              return (
                <button
                  key={wf.workflow_id}
                  onClick={() => onSelect(wf.workflow_id)}
                  className={`w-full rounded-md px-3 py-2 text-left transition-colors ${
                    isSelected
                      ? 'bg-blue-50 ring-1 ring-blue-200'
                      : 'hover:bg-gray-50'
                  }`}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span
                      className={`truncate text-sm ${
                        isSelected ? 'font-medium text-gray-900' : 'text-gray-700'
                      }`}
                    >
                      {wf.workflow_title || wf.workflow_id}
                    </span>
                    <WorkflowStatusBadge status={wf.last_status} />
                  </div>
                  <div className="mt-0.5 truncate font-mono text-[10px] text-gray-400">
                    {wf.workflow_id}
                  </div>
                </button>
              )
            })}
          </div>
        )}
      </div>
    </aside>
  )
}
