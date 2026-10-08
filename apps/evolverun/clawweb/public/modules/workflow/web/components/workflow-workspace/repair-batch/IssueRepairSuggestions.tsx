import { useEffect, useRef, useState, type ReactNode } from 'react'
import type { RepairCandidatesResponse, RepairInboxItem } from '../../../../server/contracts/repair-workbench'
import { repairBatches } from '../../../api/repair-batches'
import { repairReadError } from '../../../api/repair-read-error'
import { itemTitle } from './repair-view'

/** Issue-scoped navigation, independent of the outer list's page and state filter. */
export default function IssueRepairSuggestions({ workflowId, signature, includeHistorical, initialItemId, onResult, renderItem }: {
  workflowId: string; signature: string; includeHistorical: boolean; initialItemId?: string | null
  onResult: (result: RepairCandidatesResponse) => void; renderItem: (item: RepairInboxItem, canEdit: boolean) => ReactNode
}) {
  const [page, setPage] = useState(1)
  const [refresh, setRefresh] = useState(0)
  const [data, setData] = useState<RepairCandidatesResponse | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [selected, setSelected] = useState(initialItemId ?? '')
  const resultCallback = useRef(onResult)
  resultCallback.current = onResult
  useEffect(() => {
    let current = true
    setLoading(true); setError(''); setData(null)
    repairBatches.candidates(workflowId, { signature, state: 'all', page, pageSize: 20, includeHistorical }).then(result => {
      if (!current) return
      setData(result); resultCallback.current(result)
      setSelected(previous => result.items.some(item => item.itemId === previous) ? previous : result.items[0]?.itemId ?? '')
    }).catch(reason => { if (current) setError(repairReadError(reason)) })
      .finally(() => { if (current) setLoading(false) })
    return () => { current = false }
  }, [workflowId, signature, includeHistorical, page, refresh])
  if (loading) return <p role="status" className="text-sm text-slate-600">正在加载此问题的修复建议…</p>
  if (error) return <div role="alert" className="text-sm text-red-700">{error}<button className="ml-2 underline" onClick={() => setRefresh(n => n + 1)}>重试建议</button></div>
  if (!data?.items.length) return <p className="text-sm text-slate-600">此问题在当前历史范围内没有修复建议。可在“证据与历史”查看分析依据。</p>
  const item = data.items.find(value => value.itemId === selected) ?? data.items[0]
  return <>
    <label className="mb-4 block text-sm font-medium text-slate-700">此问题的修复建议 · 共 {data.page.total} 条
      <select aria-label="切换建议" value={item.itemId} onChange={event => setSelected(event.target.value)}
        className="mt-2 w-full rounded-lg border border-slate-200 bg-white p-2 text-sm">
        {data.items.map(value => <option key={value.itemId} value={value.itemId}>{itemTitle(value)}</option>)}
      </select>
    </label>
    {data.page.totalPages > 1 && <nav aria-label="此问题的建议分页" className="mb-4 flex items-center justify-between text-sm">
      <button disabled={page <= 1} onClick={() => setPage(n => n - 1)}>上一页建议</button>
      <span>{data.page.page} / {data.page.totalPages}</span>
      <button disabled={page >= data.page.totalPages} onClick={() => setPage(n => n + 1)}>下一页建议</button>
    </nav>}
    {renderItem(item, data.canEdit)}
  </>
}
