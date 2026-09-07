/* eslint-disable react-hooks/set-state-in-effect */
import { useClientUser } from '../../hooks/useClientUser'
import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, type EvolveVersion } from '../../api/client'
import type { TCLogBot } from '../../types'
import EvolveBotPicker from '../../components/EvolveBotPicker'
import { useEvolveAdminScope } from '../../features/evolve/admin-scope'
import { GitDiffView, Icon } from './common'
import { formatStepTime, primaryButton, secondaryButton } from './helpers'

type VersionFilter = 'all' | 'initial' | 'accepted' | 'rejected' | 'snapshot'

const versionFilters: Array<{ key: VersionFilter; label: string }> = [
  { key: 'all', label: '全部版本' },
  { key: 'initial', label: '初始版本' },
  { key: 'accepted', label: '已接受' },
  { key: 'rejected', label: '未接受' },
  { key: 'snapshot', label: '主动快照' },
]

function versionLabel(item: EvolveVersion): string {
  return item.kind === 'snapshot'
    ? '主动快照'
    : item.kind === 'round'
      ? `R${item.round ?? 0}`
      : '任务初始版本'
}

function acceptanceView(item: EvolveVersion): { label: string; tone: string } {
  if (item.acceptanceStatus === 'accepted') return { label: '已接受', tone: 'bg-emerald-50 text-emerald-700' }
  if (item.acceptanceStatus === 'accepted_unregistered') return { label: '已接受 · Pack 未登记', tone: 'bg-amber-50 text-amber-700' }
  if (item.acceptanceStatus === 'promotion_failed') return { label: '晋升失败', tone: 'bg-red-50 text-red-700' }
  if (item.acceptanceStatus === 'passed_not_promoted') return { label: '通过 Bench · 未晋升', tone: 'bg-amber-50 text-amber-700' }
  if (item.acceptanceStatus === 'unregistered') return { label: 'Pack 未登记', tone: 'bg-amber-50 text-amber-700' }
  if (item.acceptanceStatus === 'rejected') return { label: '未接受', tone: 'bg-red-50 text-red-700' }
  if (item.kind === 'initial') return { label: '初始版本', tone: 'bg-violet-50 text-violet-700' }
  if (item.kind === 'snapshot') return { label: '未评测快照', tone: 'bg-amber-50 text-amber-700' }
  return { label: '状态未知', tone: 'bg-gray-100 text-gray-600' }
}

export function PackManagement() {
  const navigate = useNavigate()
  const { user } = useClientUser()
  const userId = user?.userId ?? ''
  const { enabled: adminMode, ownerUserId } = useEvolveAdminScope()

  const [bots, setBots] = useState<TCLogBot[]>([])
  const [botId, setBotId] = useState('')
  const [botSelectionKey, setBotSelectionKey] = useState('')
  const [versionFilter, setVersionFilter] = useState<VersionFilter>('all')
  const [query, setQuery] = useState('')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [items, setItems] = useState<EvolveVersion[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [reloadKey, setReloadKey] = useState(0)

  const [reviewVersion, setReviewVersion] = useState<EvolveVersion | null>(null)
  const [reviewDiff, setReviewDiff] = useState<string | null>(null)
  const [reviewLoading, setReviewLoading] = useState(false)
  const [reviewError, setReviewError] = useState('')

  const botOwnerId = adminMode ? ownerUserId : userId

  useEffect(() => {
    setBots([]); setBotId(''); setBotSelectionKey('')
    if (botOwnerId) {
      void api.tclog.bots({ ownerId: botOwnerId, status: 'all' })
        .then((result) => setBots(result.bots))
        .catch((e) => setError(e instanceof Error ? e.message : String(e)))
    }
  }, [botOwnerId])

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError('')
    void api.evolve.listVersions(botId || undefined, {
      scope: adminMode ? 'all' : 'mine',
      ownerUserId: adminMode ? ownerUserId : undefined,
    })
      .then((result) => { if (!cancelled) setItems(result.items) })
      .catch((e) => { if (!cancelled) setError(e instanceof Error ? e.message : String(e)) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [botId, reloadKey, adminMode, ownerUserId])

  const botNames = useMemo(() => Object.fromEntries(bots.map((bot) => [bot.botId, bot.botName || bot.displayBotId])), [bots])

  const filteredItems = useMemo(() => {
    const keyword = query.trim().toLowerCase()
    return items.filter((item) => {
      if (versionFilter === 'initial' && item.kind !== 'initial') return false
      if (versionFilter === 'snapshot' && item.kind !== 'snapshot') return false
      if (versionFilter === 'accepted' && item.acceptanceStatus !== 'accepted') return false
      if (versionFilter === 'rejected' && !['rejected', 'promotion_failed', 'passed_not_promoted', 'accepted_unregistered', 'unregistered'].includes(item.acceptanceStatus ?? '')) return false
      if (!keyword) return true
      return [item.versionId, item.pack?.packId, item.botId, botNames[item.botId], item.taskId, item.taskName, item.pack?.artifact.sha256]
        .some((value) => String(value ?? '').toLowerCase().includes(keyword))
    })
  }, [botNames, items, query, versionFilter])

  const totalPages = Math.max(1, Math.ceil(filteredItems.length / pageSize))
  const pageItems = filteredItems.slice((page - 1) * pageSize, page * pageSize)

  useEffect(() => { setPage(1) }, [botId, versionFilter, query, pageSize])
  useEffect(() => { if (page > totalPages) setPage(totalPages) }, [page, totalPages])

  const openDiffReview = async (item: EvolveVersion) => {
    setReviewVersion(item)
    setReviewDiff(null)
    setReviewError('')
    if (!item.diff?.artifactAvailable) return
    setReviewLoading(true)
    try {
      setReviewDiff(await api.evolve.getStepDiff(item.taskId, item.stepId))
    } catch (e) {
      setReviewError(e instanceof Error ? e.message : String(e))
    } finally {
      setReviewLoading(false)
    }
  }

  return (
    <div className="w-full px-3 py-6 sm:px-4 lg:px-5">
      <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-gray-950">进化版本</h1>
          <p className="mt-1.5 text-sm text-gray-500">统一 Review 初始版本、每轮进化结果与主动快照；只有具备 Pack 的版本可以应用。</p>
        </div>
        <button className={primaryButton} onClick={() => navigate('/evolve/new?type=pack')}>
          <Icon name="plus" />创建 Pack
        </button>
      </div>

      {adminMode && (
        <div className="mb-3 rounded-lg border border-amber-200 bg-amber-50 px-4 py-2 text-sm text-amber-800">
          管理员视图：{ownerUserId ? `工号 ${ownerUserId}` : '全部工号'}。仅改变版本列表读取范围。
        </div>
      )}

      <section className="overflow-hidden rounded-2xl border border-gray-200 bg-white shadow-sm">
        {error && (
          <div className="flex items-center justify-between border-b border-red-100 bg-red-50 px-5 py-3 text-sm text-red-700">
            <span>{error}</span>
            <button className="font-medium" onClick={() => setReloadKey((value) => value + 1)}>重试</button>
          </div>
        )}

        <div className="space-y-3 border-b border-gray-100 px-5 py-4">
          <div className="flex flex-wrap items-center gap-2">
            <span className="mr-1 text-xs font-medium text-gray-400">Review 状态</span>
            {versionFilters.map((item) => (
              <button
                key={item.key}
                onClick={() => setVersionFilter(item.key)}
                className={`rounded-full border px-3 py-1.5 text-xs font-medium transition ${versionFilter === item.key ? 'border-blue-200 bg-blue-50 text-blue-700' : 'border-gray-200 bg-white text-gray-500 hover:border-gray-300 hover:text-gray-700'}`}
              >
                {item.label}
              </button>
            ))}
          </div>

          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="w-full sm:w-96">
              {adminMode && !ownerUserId ? (
                <div className="rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 text-xs text-gray-500">选择工号后可按 Bot 筛选</div>
              ) : (
                <EvolveBotPicker
                  compact
                  bots={bots}
                  value={botSelectionKey}
                  ariaLabel="筛选 Pack Bot"
                  disableUnsupported={false}
                  emptyOption={{ label: '全部 Bot' }}
                  onClear={() => { setBotSelectionKey(''); setBotId('') }}
                  onChange={(key, bot) => { setBotSelectionKey(key); setBotId(bot.botId) }}
                />
              )}
            </div>
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="搜索版本、Bot、来源任务或 SHA-256"
              className="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm outline-none focus:border-blue-500 sm:w-80"
            />
          </div>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full min-w-[1420px] table-fixed text-left text-sm">
            <thead className="bg-gray-50/80 text-xs font-medium text-gray-500">
              <tr>
                <th className="w-[150px] px-5 py-3">版本</th>
                <th className="w-[235px] px-5 py-3">Bot</th>
                <th className="w-[245px] px-4 py-3">来源任务</th>
                <th className="w-[125px] px-4 py-3">Review 状态</th>
                <th className="w-[260px] px-4 py-3">Test Bench</th>
                <th className="w-[140px] px-4 py-3">Review</th>
                <th className="w-[130px] px-4 py-3">Pack</th>
                <th className="w-[155px] px-4 py-3">生成时间</th>
                <th className="sticky right-0 z-10 w-[220px] border-l border-gray-100 bg-gray-50 px-4 py-3 text-center shadow-[-8px_0_12px_-12px_rgba(15,23,42,0.35)]">操作</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {pageItems.map((item) => {
                const status = acceptanceView(item)
                const comparison = item.scoreComparison
                const canApply = !adminMode && item.userId === userId && Boolean(item.pack?.artifact.ref)
                return (
                  <tr key={item.versionId} className="group transition hover:bg-gray-50/70">
                    <td className="px-5 py-4">
                      <p className="text-sm font-semibold text-gray-900">{versionLabel(item)}</p>
                      <p className="mt-1 truncate font-mono text-[10px] text-gray-400" title={item.pack?.packId ?? item.versionId}>{item.pack?.packId ?? item.versionId}</p>
                    </td>
                    <td className="px-5 py-4">
                      <div className="flex items-center gap-3">
                        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-blue-50 text-blue-600"><Icon name="bot" /></span>
                        <div className="min-w-0"><p className="truncate font-medium text-gray-900">{botNames[item.botId] || '未命名 Bot'}</p><p className="mt-0.5 truncate font-mono text-[11px] text-gray-400">{item.botId}</p></div>
                      </div>
                    </td>
                    <td className="px-4 py-4">
                      <button className="block max-w-full text-left text-xs text-blue-600 hover:underline" title={item.taskId} onClick={() => navigate(`/evolve/runs/${item.taskId}`)}>
                        <span className="block truncate font-medium">{item.taskName || item.taskId}</span>
                        <span className="mt-1 block truncate font-mono text-[10px] text-gray-400">{item.taskId}</span>
                      </button>
                    </td>
                    <td className="px-4 py-4"><span className={`inline-flex rounded-md px-2 py-1 text-xs font-medium ${status.tone}`}>{status.label}</span></td>
                    <td className="px-4 py-4">
                      {comparison ? <div className="text-xs"><div className="flex items-center gap-2 text-gray-600"><span>{comparison.baseline ?? '—'}</span><span className="text-gray-300">→</span><span className="font-semibold text-gray-900">{comparison.candidate ?? '—'}</span><span className={`font-medium ${(comparison.delta ?? 0) > 0 ? 'text-emerald-600' : (comparison.delta ?? 0) < 0 ? 'text-red-600' : 'text-gray-400'}`}>{typeof comparison.delta === 'number' ? `${comparison.delta >= 0 ? '+' : ''}${comparison.delta.toFixed(4)}` : '—'}</span></div><p className="mt-1 text-[10px] text-gray-400">{comparison.name || 'test_score'}</p></div> : <span className="text-xs text-gray-400">未评测</span>}
                    </td>
                    <td className="px-4 py-4"><p className="text-xs font-medium text-gray-700">{item.reviewStatus || '—'}</p>{item.specVersion && <p className="mt-1 text-[10px] text-gray-400">Spec {item.specVersion}</p>}</td>
                    <td className="px-4 py-4">{item.pack?.artifact.ref ? <span className="rounded-md bg-amber-50 px-2 py-1 text-xs font-medium text-amber-700">可恢复</span> : item.reportedPack?.artifact?.ref ? <span className="text-xs font-medium text-amber-700">Skill 已上报 · 未登记</span> : <span className="text-xs text-gray-400">仅 Review</span>}</td>
                    <td className="whitespace-nowrap px-4 py-4 text-xs text-gray-500">{formatStepTime(item.createdAt)}</td>
                    <td className="sticky right-0 border-l border-gray-100 bg-white px-3 py-4 shadow-[-8px_0_12px_-12px_rgba(15,23,42,0.35)] group-hover:bg-gray-50">
                      <div className="flex items-center justify-center gap-2">
                        <button disabled={!item.diff?.available} className="rounded-md border border-blue-100 bg-blue-50 px-3 py-1.5 text-xs font-medium text-blue-700 hover:bg-blue-100 disabled:cursor-not-allowed disabled:opacity-40" onClick={() => void openDiffReview(item)}>Diff</button>
                        <button disabled={!item.pack?.artifact.ref} className="rounded-md border border-gray-200 bg-white px-3 py-1.5 text-xs font-medium text-gray-600 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-40" onClick={() => void handleDownload(item, setError)}>下载</button>
                        <button disabled={!canApply} title={adminMode ? '管理员视图仅支持查看其他用户的版本' : !item.pack?.artifact.ref ? '该版本没有可应用的 Pack' : undefined} className="rounded-md border border-gray-200 bg-white px-3 py-1.5 text-xs font-medium text-gray-600 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-40" onClick={() => navigate(`/evolve/new?type=pack_restore&packId=${encodeURIComponent(item.pack?.packId ?? '')}&sourceTaskId=${encodeURIComponent(item.taskId)}&sourceKind=${item.kind === 'initial' ? 'baseline' : item.kind}&sourceRound=${encodeURIComponent(String(item.round ?? ''))}`)}>应用</button>
                      </div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>

        {loading && <div className="py-16 text-center text-sm text-gray-400">正在加载进化版本…</div>}
        {!loading && pageItems.length === 0 && <div className="py-16 text-center"><span className="mx-auto flex h-11 w-11 items-center justify-center rounded-xl bg-gray-100 text-gray-500"><Icon name="package" className="h-5 w-5" /></span><p className="mt-4 text-sm font-medium text-gray-700">{items.length === 0 ? '暂无进化版本' : '没有符合条件的版本'}</p><p className="mt-1 text-xs text-gray-400">{items.length === 0 ? '创建 Pack 或完成进化轮次后，版本会按时间显示在这里。' : '请调整 Review 状态、Bot 或搜索条件。'}</p></div>}

        {!loading && filteredItems.length > 0 && (
          <div className="flex flex-wrap items-center justify-between gap-3 border-t border-gray-100 px-5 py-4 text-sm text-gray-500">
            <span>共 {filteredItems.length} 个版本，第 {page} / {totalPages} 页</span>
            <div className="flex items-center gap-2">
              <select
                value={pageSize}
                onChange={(event) => setPageSize(Number(event.target.value))}
                className="rounded-lg border border-gray-200 bg-white px-2 py-1.5 text-sm"
              >
                <option value={10}>10 条/页</option>
                <option value={20}>20 条/页</option>
                <option value={50}>50 条/页</option>
              </select>
              <button
                disabled={page <= 1}
                onClick={() => setPage((value) => Math.max(1, value - 1))}
                className="rounded-lg border border-gray-200 px-3 py-1.5 disabled:cursor-not-allowed disabled:opacity-40"
              >
                上一页
              </button>
              <button
                disabled={page >= totalPages}
                onClick={() => setPage((value) => Math.min(totalPages, value + 1))}
                className="rounded-lg border border-gray-200 px-3 py-1.5 disabled:cursor-not-allowed disabled:opacity-40"
              >
                下一页
              </button>
            </div>
          </div>
        )}
      </section>

      {reviewVersion && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-gray-950/45 p-4" role="dialog" aria-modal="true" aria-label="版本 Diff Review">
          <div className="flex max-h-[90vh] w-full max-w-6xl flex-col overflow-hidden rounded-2xl border border-gray-200 bg-white shadow-2xl">
            <div className="flex items-start justify-between gap-4 border-b border-gray-100 px-5 py-4">
              <div>
                <p className="text-xs font-medium text-blue-600">{versionLabel(reviewVersion)} · {acceptanceView(reviewVersion).label}</p>
                <h2 className="mt-1 text-lg font-semibold text-gray-950">版本 Diff Review</h2>
                <p className="mt-1 font-mono text-[10px] text-gray-400">{reviewVersion.taskId} / {reviewVersion.stepId}</p>
              </div>
              <div className="flex items-center gap-2">
                <button className={secondaryButton} onClick={() => navigate(`/evolve/runs/${reviewVersion.taskId}`)}>查看完整任务</button>
                <button className="rounded-lg border border-gray-200 px-3 py-2 text-sm text-gray-500 hover:bg-gray-50" onClick={() => setReviewVersion(null)}>关闭</button>
              </div>
            </div>
            <div className="overflow-auto p-5">
              {reviewVersion.diff?.summary && <p className="mb-3 rounded-lg bg-blue-50 px-3 py-2 text-xs leading-5 text-blue-800">{reviewVersion.diff.summary}</p>}
              {reviewVersion.diff?.files && reviewVersion.diff.files.length > 0 && (
                <div className="mb-3 divide-y divide-gray-100 overflow-hidden rounded-lg border border-gray-200 bg-white">
                  {reviewVersion.diff.files.map((file, index) => {
                    const path = String(file.path ?? file.name ?? file.file ?? `File ${index + 1}`)
                    const change = String(file.change ?? file.status ?? '')
                    return (
                      <div key={`${path}-${index}`} className="px-3 py-2.5">
                        <p className="break-all font-mono text-xs text-gray-800">{path}</p>
                        {change && <p className="mt-1 text-[11px] text-gray-500">{change}</p>}
                      </div>
                    )
                  })}
                </div>
              )}
              {reviewLoading && <p className="py-16 text-center text-sm text-gray-400">正在加载 Diff…</p>}
              {reviewError && <p className="rounded-lg bg-red-50 p-3 text-xs text-red-700">{reviewError}</p>}
              {!reviewLoading && !reviewError && reviewDiff != null && <GitDiffView content={reviewDiff} />}
              {!reviewLoading && !reviewError && reviewDiff == null && !reviewVersion.diff?.summary && !reviewVersion.diff?.files?.length && <p className="py-16 text-center text-sm text-gray-400">该版本没有可读取的 Diff。</p>}
              {!reviewLoading && !reviewError && reviewDiff == null && !reviewVersion.diff?.artifactAvailable && Boolean(reviewVersion.diff?.summary || reviewVersion.diff?.files?.length) && <p className="mt-3 text-xs text-gray-400">该历史版本未登记完整 Diff Artifact，以上为 Step 上报的摘要和文件列表。</p>}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

async function handleDownload(item: EvolveVersion, setError: (msg: string) => void) {
  if (!item.pack?.artifact.ref) return
  try {
    const sourceKind = item.kind === 'initial' ? 'baseline' : item.kind
    const download = await api.evolve.getPackDownloadUrl(item.taskId, item.stepId, sourceKind as 'baseline' | 'snapshot' | 'round')
    const anchor = document.createElement('a')
    anchor.href = download.url
    anchor.download = download.filename
    document.body.appendChild(anchor)
    anchor.click()
    anchor.remove()
  } catch (e) {
    setError(e instanceof Error ? e.message : String(e))
  }
}
