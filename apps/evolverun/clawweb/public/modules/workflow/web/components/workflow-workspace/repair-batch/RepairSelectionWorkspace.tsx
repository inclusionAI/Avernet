import { useEffect, useState } from 'react'
import { repairBatches } from '../../../api/repair-batches'
import type { RepairCandidatesResponse, RepairInboxItem } from '../../../../server/contracts/repair-workbench'
import RepairWorkspace from './RepairWorkspace'
import { changeSummary, sourceRunCount } from './RepairItemDetail'
import { exclusion, itemTitle, primary, states } from './repair-view'
import { selectionConflicts } from './repair-selection'

export default function RepairSelectionWorkspace({ workflowId, includeHistorical, selected, selectedItems, instructions, busy, reason, error,
  onInstructions, onToggle, onResult, onDetail, onGenerate, onBack }: {
  workflowId: string; includeHistorical: boolean; selected: string[]; selectedItems: RepairInboxItem[];
  instructions: string; busy: boolean; reason: string; error: string;
  onInstructions: (value: string) => void; onToggle: (id: string) => void; onResult: (result: RepairCandidatesResponse) => void;
  onDetail: (item: RepairInboxItem) => void; onGenerate: () => void; onBack: () => void
}) {
  const [page, setPage] = useState(1)
  const [data, setData] = useState<RepairCandidatesResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [readError, setReadError] = useState('')
  const [query, setQuery] = useState('')
  const [node, setNode] = useState('')
  const [failureMode, setFailureMode] = useState('')
  const [modes, setModes] = useState<string[]>([])
  const [reload, setReload] = useState(0)
  useEffect(() => {
    let current = true
    setLoading(true); setReadError('')
    repairBatches.candidates(workflowId, { state: 'all', page, pageSize: 20, includeHistorical, ...(node ? { nodeId: node } : {}), ...(failureMode ? { failureMode } : {}) })
      .then(result => { if (current) { setData(result); onResult(result); setModes(previous => [...new Set([...previous, ...result.items.map(itemMode).filter(Boolean)])]) } })
      .catch(e => { if (current) { setReadError(e instanceof Error ? e.message : String(e)); setData(null) } })
      .finally(() => { if (current) setLoading(false) })
    return () => { current = false }
  }, [workflowId, includeHistorical, page, node, failureMode, reload])
  function itemMode(item: RepairInboxItem): string {
    const diagnoses = item.context?.diagnoses as Record<string, unknown>[] | undefined
    return String(item.context?.failureMode ?? diagnoses?.[0]?.failureMode ?? '')
  }
  const rows = (data?.items ?? []).filter(item => !query || `${itemTitle(item)} ${item.context?.nodeId ?? ''}`.toLowerCase().includes(query.toLowerCase()))
  const selectable = rows.filter(item => !exclusion(item))
  const allSelected = selectable.length > 0 && selectable.every(item => selected.includes(item.itemId))
  const nodes = [...new Set([...selectedItems, ...(data?.items ?? [])].map(item => String(item.context?.nodeId ?? '')).filter(Boolean))]
  const conflicts = selectionConflicts(selectedItems)
  const blocker = reason || (readError ? '建议读取失败，请重试后生成。' : loading ? '正在核对建议来源…' : !data?.canEdit ? '当前只有查看权限。' : '')
  return <RepairWorkspace stage={1} title="把多条建议，变成一次修复" subtitle={`${workflowId} · 选择独立建议，生成完整 Pack 候选`} onBack={onBack}>
    <div className="grid items-start gap-6 xl:grid-cols-[minmax(0,1fr)_19rem]">
      <section className="overflow-hidden rounded-lg border border-slate-200 bg-white">
        <div className="border-b border-slate-200 px-5 py-4"><h3 className="text-base font-semibold text-slate-900">本次准备解决哪些问题？</h3>
          <p className="mt-1 text-xs text-slate-500">跨问题、跨页保留选择。翻页不会自动增加建议。</p></div>
        <div className="flex gap-3 border-b border-slate-200 p-4">
          <input aria-label="搜索本页建议" value={query} onChange={e => setQuery(e.target.value)} placeholder="搜索本页建议 / 节点" className="min-w-0 flex-1 rounded border border-slate-200 px-3 py-2 text-xs" />
          <select aria-label="修复节点" value={node} onChange={e => { setNode(e.target.value); setPage(1) }} className="rounded border border-slate-200 px-2 text-xs"><option value="">全部节点</option>{nodes.map(n => <option key={n}>{n}</option>)}</select>
          <select aria-label="修复问题类型" value={failureMode} onChange={e => { setFailureMode(e.target.value); setPage(1) }} className="rounded border border-slate-200 px-2 text-xs"><option value="">全部问题类型</option>{modes.map(mode => <option key={mode}>{mode}</option>)}</select>
        </div>
        {loading && <p role="status" className="px-5 py-3 text-xs text-slate-500">读取建议…</p>}
        {readError && <p role="alert" className="px-5 py-3 text-xs text-red-700">{readError} <button onClick={() => setReload(v => v + 1)} className="underline">重试读取</button></p>}
        <div className="overflow-x-auto"><table className="w-full text-left text-xs"><thead className="bg-slate-50 text-slate-500"><tr><th className="px-4 py-3"><input type="checkbox" aria-label="全选本页可用建议" checked={allSelected} disabled={busy || loading || !data?.canEdit || !selectable.length}
          onChange={() => { const targets = allSelected ? selectable : selectable.filter(item => !selected.includes(item.itemId)).slice(0, Math.max(0, 100 - selected.length)); targets.forEach(item => onToggle(item.itemId)) }} className="h-4 w-4 accent-blue-600" /></th><th>问题与修复建议</th><th className="px-4">来源</th><th className="px-4">处理提示</th></tr></thead>
          <tbody>{rows.map(item => { const checked = selected.includes(item.itemId); const unavailable = exclusion(item); return <tr key={item.itemId} className={`border-t border-slate-100 ${checked ? 'bg-blue-50/40' : ''}`}>
            <td className="px-4 py-4 align-top"><input type="checkbox" aria-label={`选择 ${itemTitle(item)}`} checked={checked} disabled={busy || loading || !data?.canEdit || !!unavailable || !checked && selected.length >= 100} onChange={() => onToggle(item.itemId)} className="h-4 w-4 accent-blue-600" /></td>
            <td className="min-w-64 py-4 pr-4"><p className="font-semibold leading-5 text-slate-800">{itemTitle(item)}</p><p className="mt-1 font-mono text-[10px] text-slate-500">{String(item.context?.nodeId ?? '')}</p><p className="mt-1 leading-5 text-slate-500">{changeSummary(item)}</p><button onClick={() => onDetail(item)} className="mt-1 text-blue-700 hover:underline">查看问题与证据 ↗</button></td>
            <td className="px-4 py-4 align-top text-slate-500">{sourceRunCount(item) ?? '未知'} 个运行</td><td className="px-4 py-4 align-top text-slate-500">{unavailable || states[item.state]}</td>
          </tr> })}</tbody></table></div>
        {!loading && !readError && !rows.length && <p className="p-5 text-xs text-slate-500">当前范围没有匹配建议。</p>}
        {data && <div className="flex justify-between border-t border-slate-200 px-5 py-3 text-xs text-slate-500"><button disabled={loading || page <= 1} onClick={() => setPage(v => v - 1)}>上一页</button><span>建议第 {data.page.page}/{data.page.totalPages || 1} 页 · {data.page.total} 条</span><button disabled={loading || page >= data.page.totalPages} onClick={() => setPage(v => v + 1)}>下一页</button></div>}
      </section>
      <aside className="space-y-4 xl:sticky xl:top-4">
        <section className="rounded-lg border border-slate-200 bg-white p-5"><p className="text-xs text-slate-500">本次修复范围</p><p className="mt-3 text-4xl font-semibold text-slate-900">{selected.length}<span className="ml-2 text-xs font-normal text-slate-500">条独立建议</span></p>
          <p className="mt-3 text-xs text-slate-500">涉及节点：{new Set(selectedItems.map(item => item.context?.nodeId ?? '未知节点')).size} 个</p>
          <p className="mt-4 border-t border-slate-100 pt-3 text-xs text-slate-600">生成方式：AIS · 完整 Pack</p><p className="mt-2 text-xs text-slate-500">基线由服务端在首次生成时冻结。</p>
          <details className="mt-4 text-xs"><summary className="cursor-pointer text-blue-700">查看全部已选（{selected.length}）</summary><ul className="mt-2 max-h-64 space-y-2 overflow-auto">{selectedItems.map(item => <li key={item.itemId} className="flex gap-2"><span className="flex-1 leading-5">{itemTitle(item)}</span><button disabled={busy} onClick={() => onToggle(item.itemId)} className="shrink-0 text-blue-700">移除</button></li>)}</ul></details>
          <p className="mt-4 border-l-2 border-amber-400 bg-amber-50 p-3 text-xs leading-5 text-amber-800">重复修改保留每条来源；冲突不会自动替你选择。</p>
        </section>
        <label className="block rounded-lg border border-slate-200 bg-white p-5 text-sm font-semibold text-slate-800">本次修复要求<textarea aria-label="本次修复要求" rows={5} maxLength={20_000} value={instructions} disabled={busy} onChange={e => onInstructions(e.target.value)} placeholder="例如：保持原业务分支，不放宽高风险规则。" className="mt-3 w-full rounded border border-slate-200 p-3 text-xs font-normal leading-5" /></label>
      </aside>
    </div>
    {conflicts.length > 0 && <p role="alert" className="rounded border border-amber-200 bg-amber-50 p-4 text-xs text-amber-800">所选方案存在 {conflicts.length} 处结构冲突。候选将保留逐项结论；冲突未解决不能发布。</p>}
    <footer className="sticky bottom-4 z-20 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-slate-200 bg-white px-5 py-4 shadow-sm"><div><p className="text-sm font-semibold">先改成草稿，不直接部署</p><p role={error ? 'alert' : 'status'} className={`mt-1 text-xs ${error ? 'text-red-700' : 'text-slate-500'}`}>{error || blocker || '来源、修改和检查结果一起进入任务详情。'}</p></div><button className={primary} disabled={busy || !!blocker || !selected.length} onClick={onGenerate}>{busy ? '提交中…' : '生成 Pack 草稿'}</button></footer>
  </RepairWorkspace>
}
