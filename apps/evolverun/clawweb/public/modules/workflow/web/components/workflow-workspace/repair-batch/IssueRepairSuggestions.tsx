import { useEffect, useRef, useState, type ReactNode } from 'react'
import { useQuery } from '@tanstack/react-query'
import type { RepairCandidatesResponse, RepairInboxItem, RepairInboxFilter } from '../../../../server/contracts/repair-workbench'
import { repairBatches } from '../../../api/repair-batches'
import { repairReadError } from '../../../api/repair-read-error'
import { exclusion, itemTitle, states } from './repair-view'
import { changeSummary, sourceRunCount } from './RepairItemDetail'

/** Compare and select suggestions within one issue; evidence is fetched only on expansion. */
export default function IssueRepairSuggestions({ workflowId, signature, includeHistorical, initialItemId, onResult, renderItem,
  selected = [], onToggle, canEdit = false, limit = 100, cacheVersion = '', initialPage }: {
  workflowId: string; signature?: string; includeHistorical: boolean; initialItemId?: string | null
  selected?: string[]; onToggle?: (id: string) => void; canEdit?: boolean; limit?: number
  cacheVersion?: string; initialPage?: RepairCandidatesResponse
  onResult: (result: RepairCandidatesResponse) => void; renderItem: (item: RepairInboxItem, canEdit: boolean) => ReactNode
}) {
  const [page, setPage] = useState(1)
  const [state, setState] = useState<RepairInboxFilter>('all')
  const query = useQuery({ queryKey: ['repair-suggestions', workflowId, signature, includeHistorical, state, page, cacheVersion, canEdit],
    queryFn: () => repairBatches.candidates(workflowId, { signature, state, page, pageSize: 20, includeHistorical }),
    initialData: page === 1 && state === 'all' ? initialPage : undefined,
    staleTime: 30_000, gcTime: 300_000, retry: false })
  const data = query.error ? undefined : query.data
  const error = query.error ? repairReadError(query.error) : ''
  const loading = query.isPending
  const [expanded, setExpanded] = useState(initialItemId ?? '')
  const resultCallback = useRef(onResult)
  resultCallback.current = onResult
  useEffect(() => {
    if (data && !error) resultCallback.current(data)
  }, [data, error])
  return <section aria-label="选择修复建议" className="space-y-4">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <h4 className="text-sm font-semibold text-slate-900">{signature ? '此问题的修复建议' : '全部修复建议'}{data ? ` · 共 ${data.page.total} 条` : ''}</h4>
      <select aria-label="建议状态" value={state} onChange={event => { setState(event.target.value as RepairInboxFilter); setPage(1); setExpanded('') }}
        className="rounded border border-slate-200 bg-white px-2 py-1 text-sm">
        {Object.entries({ all: '全部状态', pending: '待处理', processing: '处理中', awaiting_verification: '待验证', closed: '已关闭', no_action: '暂不处理' })
          .map(([value, label]) => <option key={value} value={value}>{label}</option>)}
      </select>
    </div>
    <p className="text-xs leading-5 text-slate-500">勾选要采用的方案，再生成修复草稿供审阅。可跨问题多选，不会直接应用或部署。</p>
    {loading ? <p role="status" className="text-sm text-slate-600">正在加载修复建议…</p>
      : error ? <div role="alert" className="text-sm text-red-700">{error}<button className="ml-2 underline" onClick={() => void query.refetch()}>重试建议</button></div>
      : !data?.items.length ? <p className="text-sm text-slate-600">当前范围没有匹配的修复建议。可调整状态或历史范围，分析依据仍可查看。</p>
      : <div className="space-y-3">{data.items.map(item => {
        const reason = exclusion(item)
        const checked = selected.includes(item.itemId)
        const runs = sourceRunCount(item)
        return <article key={item.itemId} className={`rounded-lg border p-3 ${checked ? 'border-blue-300 bg-blue-50/30' : 'border-slate-200'}`}>
          <div className="flex items-start gap-3">
            <input type="checkbox" aria-label={`选择 ${itemTitle(item)}`} checked={checked}
              disabled={!canEdit || !data.canEdit || !!reason || !checked && selected.length >= limit}
              onChange={() => onToggle?.(item.itemId)} className="mt-1 h-4 w-4 shrink-0 accent-blue-600" />
            <div className="min-w-0 flex-1">
              <p className="text-sm font-semibold leading-6 text-slate-900">{itemTitle(item)}</p>
              <p className="mt-1 text-xs leading-5 text-slate-600">{changeSummary(item)}</p>
              <p className="mt-1 text-xs text-slate-500">{states[item.state]} · {runs === null ? '来源运行数未知' : `依据覆盖 ${runs} 个运行`}</p>
              {reason && <p className="mt-1 text-xs text-amber-700">{reason}</p>}
              <button type="button" aria-expanded={expanded === item.itemId} className="mt-2 text-xs font-medium text-blue-700 hover:underline focus-visible:outline"
                onClick={() => setExpanded(value => value === item.itemId ? '' : item.itemId)}>
                {expanded === item.itemId ? '收起修改与依据' : '查看修改与依据'}
              </button>
            </div>
          </div>
          {expanded === item.itemId && <div className="mt-3 border-t border-slate-200 pt-3">{renderItem(item, data.canEdit)}</div>}
        </article>
      })}</div>}
    {data && data.page.totalPages > 1 && <nav aria-label="此问题的建议分页" className="flex items-center justify-between text-sm">
      <button disabled={data.page.page <= 1} onClick={() => { setPage(data.page.page - 1); setExpanded('') }}>上一页建议</button>
      <span>建议第 {data.page.page} / {data.page.totalPages} 页</span>
      <button disabled={data.page.page >= data.page.totalPages} onClick={() => { setPage(data.page.page + 1); setExpanded('') }}>下一页建议</button>
    </nav>}
  </section>
}
