import { useState } from 'react'
import type { NodeStatus, WorkflowTypeRow } from '@avernet/clawweb-shared/web/types'
import EmptyState from '../EmptyState'
import SearchInput from '../SearchInput'
import StatusBadge from '../StatusBadge'

interface WorkflowPickerProps {
  workflows: WorkflowTypeRow[]
  selectedId: string | null
  onSelect: (id: string) => void
  loading?: boolean
}

export default function WorkflowPicker({ workflows, selectedId, onSelect, loading }: WorkflowPickerProps) {
  const [open, setOpen] = useState(false)
  const [search, setSearch] = useState('')
  const selected = workflows.find((workflow) => workflow.workflow_id === selectedId)
  const selectedName = selected?.workflow_title || selected?.workflow_id || '选择工作流'
  const query = search.trim().toLowerCase()
  const visibleWorkflows = query
    ? workflows.filter((workflow) => workflow.workflow_id.toLowerCase().includes(query) || (workflow.workflow_title ?? '').toLowerCase().includes(query))
    : workflows

  return <div className="relative min-w-0">
    <button
      type="button"
      onClick={() => setOpen((current) => !current)}
      aria-label={`切换工作流，当前：${selectedName}`}
      aria-expanded={open}
      aria-haspopup="dialog"
      className="group flex max-w-[min(28rem,48vw)] items-center gap-1.5 rounded-lg px-2 py-1 text-left text-sm font-medium text-slate-600 transition hover:bg-slate-100 hover:text-slate-950 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/30"
    >
      <span className="truncate">{selectedName}</span>
      <svg aria-hidden="true" className={`h-3.5 w-3.5 shrink-0 text-slate-400 transition-transform ${open ? 'rotate-180' : ''}`} fill="none" stroke="currentColor" strokeWidth="1.8" viewBox="0 0 24 24">
        <path d="m7 10 5 5 5-5" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    </button>

    {open && <>
      <button type="button" aria-label="关闭工作流选择器" className="fixed inset-0 z-30 cursor-default" onClick={() => setOpen(false)} />
      <div role="dialog" aria-label="选择工作流" className="absolute left-0 top-full z-40 mt-2 w-[22rem] max-w-[calc(100vw-3rem)] overflow-hidden rounded-xl border border-slate-200 bg-white shadow-[0_18px_48px_rgba(15,23,42,0.18)]">
        <div className="border-b border-slate-100 px-3 py-2.5">
          <div>
            <p className="text-xs font-semibold text-slate-900">切换工作流</p>
            <p className="mt-0.5 text-[10px] text-slate-400">选择后保留当前功能视图</p>
          </div>
        </div>
        <div className="border-b border-slate-100 p-2.5 [&_input]:w-full">
          <SearchInput value={search} onChange={setSearch} placeholder="搜索工作流…" />
        </div>
        <div className="max-h-64 overflow-y-auto p-1.5">
          {loading ? <div className="flex justify-center py-8"><div className="h-4 w-4 animate-spin rounded-full border-2 border-slate-200 border-t-blue-600" /></div> : visibleWorkflows.length === 0 ? <div className="px-2 py-4"><EmptyState title="无匹配工作流" description={search ? '换个关键词试试' : '暂无可访问的工作流'} /></div> : visibleWorkflows.map((workflow) => <button
            type="button"
            key={workflow.workflow_id}
            onClick={() => { onSelect(workflow.workflow_id); setOpen(false); setSearch('') }}
            className={`flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left transition ${selectedId === workflow.workflow_id ? 'bg-blue-50' : 'hover:bg-slate-50'}`}
          >
            <span className="min-w-0 flex-1">
              <span className="block truncate text-xs font-medium text-slate-800">{workflow.workflow_title || workflow.workflow_id}</span>
              <span className="mt-0.5 block truncate font-mono text-[9px] text-slate-400">{workflow.workflow_id}</span>
            </span>
            <StatusBadge status={(workflow.last_status as NodeStatus) ?? 'pending'} />
          </button>)}
        </div>
      </div>
    </>}
  </div>
}
