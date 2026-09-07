import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { formatDuration } from '../utils'
import type { IWorkflowHealthRow, IWorkflowReleaseStatRow } from '../../../types/dashboard'
import { FULL_SORT_LABELS, type FullSortKey } from './workflow-full-table-config'

export type { FullSortKey } from './workflow-full-table-config'

interface MergedRow {
  workflowId: string
  workflowTitle: string
  status: 'released' | 'testing' | 'unpublished'   // 已发布 / 测试(跑过未部署) / 未发布(没跑过也没部署)
  runCount: number
  completionSuccessRate: number | null
  selfHealSuccessRate: number | null
  selfHealTriggeredRuns: number
  machineDurationP50: number | null
  deployCount: number
  rollbackCount: number
  devCycleMs: number | null
  lastDeployAt: number | null
  firstRunSuccessRate: number | null
}

type StatusFilter = '' | 'released' | 'notReleased'

interface WorkflowFullTableProps {
  healthRows: IWorkflowHealthRow[]                 // 运行维度(近 N 天跑过的)
  releaseRows: IWorkflowReleaseStatRow[] | null    // 发布维度;null = 当前库不可算(SQLite)
  isLoading: boolean
  isError?: boolean
  initialSort?: FullSortKey
  initialStatus?: StatusFilter
  pageSize?: number
}

function Rate({ r }: { r: number | null }) {
  if (r === null) return <span className="text-gray-400">—</span>
  const pct = (r * 100).toFixed(1)
  const cls = r >= 0.9 ? 'text-emerald-600' : r >= 0.7 ? 'text-amber-600' : 'text-red-600'
  return <span className={`tabular-nums font-medium ${cls}`}>{pct}%</span>
}

function fmtDate(ts: number | null): string {
  if (ts == null) return '—'
  const d = new Date(ts * 1000)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

const STATUS_BADGE: Record<MergedRow['status'], { text: string; cls: string }> = {
  released: { text: '已发布', cls: 'bg-emerald-50 text-emerald-700 ring-emerald-200' },
  testing: { text: '测试', cls: 'bg-gray-50 text-gray-500 ring-gray-200' },
  unpublished: { text: '未发布', cls: 'bg-amber-50 text-amber-700 ring-amber-200' },
}

/**
 * 工作流全景表:运行维度 + 发布维度按 workflowId 合并,一行一个工作流。
 * 两维度的窗口列都跟上游 from/to;研发周期/最近部署为全周期口径(后端保证)。
 * 研发效能/守护效果/L2 独立页共用。分页 + 搜索 + 排序,点行 → L3。
 * SQLite 库 releaseRows 为 null → 发布列显 —,状态只有 测试/未发布 粗分。
 */
export function WorkflowFullTable({ healthRows, releaseRows, isLoading, isError = false, initialSort = 'completionAsc', initialStatus = '', pageSize = 10 }: WorkflowFullTableProps) {
  const navigate = useNavigate()
  const [sort, setSort] = useState<FullSortKey>(initialSort)
  const [status, setStatus] = useState<StatusFilter>(initialStatus)
  const [kw, setKw] = useState('')
  const [page, setPage] = useState(1)

  const merged = useMemo(() => {
    const map = new Map<string, MergedRow>()
    for (const h of healthRows) {
      map.set(h.workflowId, {
        workflowId: h.workflowId,
        workflowTitle: h.workflowTitle,
        status: h.released ? 'released' : 'testing',
        runCount: h.runCount,
        completionSuccessRate: h.completionSuccessRate,
        selfHealSuccessRate: h.selfHealSuccessRate,
        selfHealTriggeredRuns: h.selfHealTriggeredRuns,
        machineDurationP50: h.machineDurationP50,
        deployCount: 0,
        rollbackCount: 0,
        devCycleMs: null,
        lastDeployAt: null,
        firstRunSuccessRate: null,
      })
    }
    for (const r of releaseRows ?? []) {
      const m = map.get(r.workflowId)
      if (m) {
        m.deployCount = r.deployCount
        m.rollbackCount = r.rollbackCount
        m.devCycleMs = r.devCycleMs
        m.lastDeployAt = r.lastDeployAt
        m.firstRunSuccessRate = r.firstRunSuccessRate
        m.status = r.released ? 'released' : m.status
        if (m.workflowTitle === m.workflowId && r.workflowTitle) m.workflowTitle = r.workflowTitle
      } else {
        map.set(r.workflowId, {
          workflowId: r.workflowId,
          workflowTitle: r.workflowTitle,
          status: r.released ? 'released' : 'unpublished',
          runCount: 0,
          completionSuccessRate: null,
          selfHealSuccessRate: null,
          selfHealTriggeredRuns: 0,
          machineDurationP50: null,
          deployCount: r.deployCount,
          rollbackCount: r.rollbackCount,
          devCycleMs: r.devCycleMs,
          lastDeployAt: r.lastDeployAt,
          firstRunSuccessRate: r.firstRunSuccessRate,
        })
      }
    }
    return [...map.values()]
  }, [healthRows, releaseRows])

  const filtered = useMemo(() => {
    let list = merged
    if (status === 'released') list = list.filter((w) => w.status === 'released')
    if (status === 'notReleased') list = list.filter((w) => w.status !== 'released')
    const k = kw.trim().toLowerCase()
    if (k) list = list.filter((w) => w.workflowTitle.toLowerCase().includes(k) || w.workflowId.toLowerCase().includes(k))
    const sorted = list.slice()
    switch (sort) {
      case 'completionAsc': sorted.sort((a, b) => {
        if (a.completionSuccessRate == null) return b.completionSuccessRate == null ? 0 : 1
        if (b.completionSuccessRate == null) return -1
        return a.completionSuccessRate - b.completionSuccessRate
      }); break
      case 'runDesc': sorted.sort((a, b) => b.runCount - a.runCount); break
      case 'machineAsc': sorted.sort((a, b) => (a.machineDurationP50 ?? Infinity) - (b.machineDurationP50 ?? Infinity)); break
      case 'healDesc': sorted.sort((a, b) => b.selfHealTriggeredRuns - a.selfHealTriggeredRuns); break
      case 'deployDesc': sorted.sort((a, b) => b.deployCount - a.deployCount); break
      case 'recentDeployDesc': sorted.sort((a, b) => (b.lastDeployAt ?? 0) - (a.lastDeployAt ?? 0)); break
    }
    return sorted
  }, [merged, status, kw, sort])

  const totalPages = Math.max(1, Math.ceil(filtered.length / pageSize))
  const curPage = Math.min(page, totalPages)
  const pageRows = filtered.slice((curPage - 1) * pageSize, curPage * pageSize)

  const goL3 = (id: string) => navigate(`/workflow/${encodeURIComponent(id)}/metrics`)

  return (
    <div>
      {/* 工具条:状态 + 排序 + 搜索 */}
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <select
          aria-label="工作流状态"
          value={status}
          onChange={(e) => { setStatus(e.target.value as StatusFilter); setPage(1) }}
          className="h-8 rounded-md border border-gray-200 bg-white px-2 text-xs"
        >
          <option value="">全部状态</option>
          <option value="released">已发布</option>
          <option value="notReleased">未发布</option>
        </select>
        <select
          aria-label="工作流排序"
          value={sort}
          onChange={(e) => { setSort(e.target.value as FullSortKey); setPage(1) }}
          className="h-8 min-w-48 rounded-md border border-gray-200 bg-white px-2 text-xs"
        >
          {Object.entries(FULL_SORT_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
        <input
          aria-label="搜索工作流"
          value={kw}
          onChange={(e) => { setKw(e.target.value); setPage(1) }}
          placeholder="搜索工作流名/ID"
          className="h-8 min-w-48 rounded-md border border-gray-200 bg-white px-2 text-xs"
        />
        <span className="ml-auto text-xs text-gray-400">{filtered.length} 个工作流</span>
      </div>

      {isLoading ? (
        <div className="space-y-3">{Array.from({ length: 8 }).map((_, i) => <div key={i} className="h-10 animate-pulse rounded-lg bg-gray-50" />)}</div>
      ) : isError ? (
        <div className="py-10 text-center text-sm text-rose-500">工作流指标加载失败，请稍后重试</div>
      ) : pageRows.length === 0 ? (
        <div className="py-10 text-center text-sm text-gray-400">无匹配工作流</div>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-gray-100">
          <table className="min-w-[1120px] table-fixed divide-y divide-gray-100">
            <thead>
              <tr className="text-xs text-gray-500">
                <th className="w-[28%] px-3 py-2 text-left font-medium">工作流</th>
                <th className="w-20 whitespace-nowrap px-3 py-2 text-center font-medium">状态</th>
                <th className="w-20 whitespace-nowrap px-3 py-2 text-right font-medium">运行数</th>
                <th className="w-24 whitespace-nowrap px-3 py-2 text-right font-medium">运行成功率</th>
                <th className="w-24 whitespace-nowrap px-3 py-2 text-right font-medium">自愈成功率</th>
                <th className="w-24 whitespace-nowrap px-3 py-2 text-right font-medium">耗时 P50</th>
                <th className="w-24 whitespace-nowrap px-3 py-2 text-right font-medium">部署次数</th>
                <th className="w-24 whitespace-nowrap px-3 py-2 text-right font-medium">研发周期</th>
                <th className="w-28 whitespace-nowrap px-3 py-2 text-right font-medium">最近部署</th>
                <th className="w-24 whitespace-nowrap px-3 py-2 text-right font-medium">批均成功率</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-50">
              {pageRows.map((w) => {
                const badge = STATUS_BADGE[w.status]
                return (
                  <tr key={w.workflowId} role="button" tabIndex={0}
                    onClick={() => goL3(w.workflowId)}
                    onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') goL3(w.workflowId) }}
                    className="cursor-pointer transition-colors hover:bg-blue-50">
                    <td className="min-w-0 px-3 py-2.5">
                      <div className="truncate text-sm font-medium text-gray-900" title={w.workflowTitle}>{w.workflowTitle}</div>
                      <div className="truncate font-mono text-xs text-gray-400" title={w.workflowId}>{w.workflowId}</div>
                    </td>
                    <td className="whitespace-nowrap px-3 py-2.5 text-center">
                      <span className={`inline-flex whitespace-nowrap rounded-full px-2 py-0.5 text-[11px] font-medium ring-1 ${badge.cls}`}>{badge.text}</span>
                    </td>
                    <td className="whitespace-nowrap px-3 py-2.5 text-right text-sm tabular-nums text-gray-600">{w.runCount > 0 ? w.runCount.toLocaleString() : '—'}</td>
                    <td className="px-3 py-2.5 text-right text-sm"><Rate r={w.completionSuccessRate} /></td>
                    <td className="px-3 py-2.5 text-right text-sm"><Rate r={w.selfHealSuccessRate} /></td>
                    <td className="px-3 py-2.5 text-right text-sm tabular-nums text-gray-600">{w.machineDurationP50 != null ? formatDuration(w.machineDurationP50) : '—'}</td>
                    <td className="px-3 py-2.5 text-right text-sm tabular-nums text-gray-600">{w.deployCount > 0 ? w.deployCount : '—'}</td>
                    <td className="px-3 py-2.5 text-right text-sm tabular-nums text-gray-600">{w.devCycleMs != null ? formatDuration(w.devCycleMs) : '—'}</td>
                    <td className="px-3 py-2.5 text-right text-sm tabular-nums text-gray-600">{fmtDate(w.lastDeployAt)}</td>
                    <td className="px-3 py-2.5 text-right text-sm tabular-nums text-gray-600">
                      {w.firstRunSuccessRate != null ? `${(w.firstRunSuccessRate * 100).toFixed(0)}%` : '—'}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* 分页 */}
      {!isLoading && filtered.length > pageSize && (
        <div className="mt-3 flex items-center justify-end gap-2 text-xs text-gray-500">
          <span>第 {curPage} / {totalPages} 页</span>
          <button
            onClick={() => setPage((p) => Math.max(1, p - 1))}
            disabled={curPage <= 1}
            className="rounded border border-gray-200 px-2 py-1 transition hover:bg-gray-50 disabled:opacity-40"
          >上一页</button>
          <button
            onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
            disabled={curPage >= totalPages}
            className="rounded border border-gray-200 px-2 py-1 transition hover:bg-gray-50 disabled:opacity-40"
          >下一页</button>
        </div>
      )}
    </div>
  )
}
