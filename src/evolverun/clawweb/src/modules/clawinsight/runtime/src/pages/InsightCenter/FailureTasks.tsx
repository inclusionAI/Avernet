import { useEffect, useMemo, useState } from 'react'
import { insightApi } from '../../api/insight'
import { useClientUser } from '../../hooks/useClientUser'
import type { InsightScopeParams, FailureTaskIndex } from '../../types/insight'
import CreateImprovementModal from './CreateImprovementModal'
import { InsightIcon, EmptyPanel, ErrorPanel, LoadingPanel } from './InsightUi'
import FailureTaskDrawer from './FailureTaskDrawer'
import { failureClassText, formatDateTime, formatDuration } from './utils'

type BotOption = { botId: string; botName: string }

type Props = {
  scope: InsightScopeParams
  failureClass?: string
  botOptions: BotOption[]
  externalAudienceScope?: boolean
  onScopeChange: (patch: InsightScopeParams & { failureClass?: string }) => void
  onImprovementCreated: (improvementId: number) => void
}

const failureOptions = ['TOOL_FAILURE', 'AGENT_FAILURE', 'REQUIREMENT_MISUNDERSTANDING', 'USER_INTERRUPTION', 'SYSTEM_FAILURE', 'TIMEOUT', 'UNKNOWN']

function taskKey(task: FailureTaskIndex): string { return `${task.ownerUserId}:${task.sessionId}:${task.taskIndex}` }

export default function FailureTasks({ scope, failureClass, botOptions, externalAudienceScope = false, onScopeChange, onImprovementCreated }: Props) {
  const { user } = useClientUser()
  const { ownerUserId, botId, from, to, isCron } = scope
  const canUseAdminMode = user?.isAdmin === true
  const [localAdminMode, setLocalAdminMode] = useState(() => canUseAdminMode && Boolean(ownerUserId))
  const adminMode = externalAudienceScope
    ? canUseAdminMode && Boolean(ownerUserId)
    : localAdminMode
  const [ownerInput, setOwnerInput] = useState(ownerUserId || '*')
  const [adminBotOptions, setAdminBotOptions] = useState<BotOption[]>([])
  const [items, setItems] = useState<FailureTaskIndex[]>([])
  const [dataAsOf, setDataAsOf] = useState<string | null>(null)
  const [nextCursor, setNextCursor] = useState<string | null>(null)
  const [pageCursors, setPageCursors] = useState<Array<string | null>>([null])
  const [pageIndex, setPageIndex] = useState(0)
  const [loading, setLoading] = useState(true)
  const [loadingMore, setLoadingMore] = useState(false)
  const [error, setError] = useState('')
  const [reloadKey, setReloadKey] = useState(0)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [drawerTask, setDrawerTask] = useState<FailureTaskIndex | null>(null)
  const [creating, setCreating] = useState(false)
  const [creationMessage, setCreationMessage] = useState('')

  useEffect(() => {
    let active = true
    queueMicrotask(() => {
      if (!active) return
      setLoading(true); setError(''); setItems([]); setNextCursor(null); setPageCursors([null]); setPageIndex(0); setSelected(new Set())
      insightApi.failureTasks({ ownerUserId, botId, from, to, isCron, failureClass, completionStates: [0, 2, 3], pageSize: 20 })
        .then((result) => { if (active) { setItems(result.items); setNextCursor(result.nextCursor); setDataAsOf(result.dataAsOf) } })
        .catch((reason) => { if (active) setError(reason instanceof Error ? reason.message : '失败任务加载失败') })
        .finally(() => { if (active) setLoading(false) })
    })
    return () => { active = false }
  }, [ownerUserId, botId, from, to, isCron, failureClass, reloadKey])

  useEffect(() => {
    if (externalAudienceScope || !adminMode || !ownerUserId || ownerUserId === '*') return
    let active = true
    insightApi.overview({ ownerUserId }).then((result) => {
      if (active) setAdminBotOptions(result.botComparison.map((bot) => ({ botId: bot.botId, botName: bot.botName })))
    }).catch(() => { if (active) setAdminBotOptions([]) })
    return () => { active = false }
  }, [adminMode, externalAudienceScope, ownerUserId])

  const visibleBotOptions = externalAudienceScope
    ? botOptions
    : adminMode
      ? adminBotOptions
      : botOptions

  const selectedTasks = useMemo(() => items.filter((item) => selected.has(taskKey(item))), [items, selected])
  const selectedBotCount = new Set(selectedTasks.map((task) => `${task.ownerUserId}:${task.botId}`)).size
  const allSelected = items.length > 0 && items.every((item) => selected.has(taskKey(item)))

  const toggle = (item: FailureTaskIndex) => {
    setSelected((current) => {
      const next = new Set(current)
      const key = taskKey(item)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  return (
    <div className="space-y-5">
      <section className="rounded-2xl border border-gray-200 bg-white p-4 shadow-sm">
        {canUseAdminMode && !externalAudienceScope && <div className="mb-4 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-amber-200 bg-amber-50/70 px-4 py-3">
          <div><div className="flex items-center gap-2 text-sm font-semibold text-amber-900"><InsightIcon name="users" />数据视角</div><p className="mt-1 text-xs text-amber-700">管理视角可跨用户、跨 Bot 选择失败任务，系统按 Bot 拆分后分发给对应 Owner 或指定用户。</p></div>
          <div className="inline-flex rounded-lg border border-amber-200 bg-white p-1 text-xs font-medium">
            <button type="button" onClick={() => { setLocalAdminMode(false); setOwnerInput('*'); onScopeChange({ ownerUserId: undefined, botId: undefined }) }} className={`rounded-md px-3 py-1.5 ${!adminMode ? 'bg-gray-900 text-white' : 'text-gray-600 hover:bg-gray-50'}`}>我的 Bot</button>
            <button type="button" onClick={() => { setLocalAdminMode(true); const nextOwner = ownerInput || '*'; setOwnerInput(nextOwner); onScopeChange({ ownerUserId: nextOwner, botId: undefined }) }} className={`rounded-md px-3 py-1.5 ${adminMode ? 'bg-amber-600 text-white' : 'text-gray-600 hover:bg-gray-50'}`}>管理视角</button>
          </div>
        </div>}
        {adminMode && !externalAudienceScope && <div className="mb-4 grid gap-3 rounded-xl border border-gray-200 bg-gray-50/70 p-4 md:grid-cols-[minmax(260px,1fr)_auto]">
          <label><span className="mb-1.5 block text-xs font-medium text-gray-600">查看范围 user_id</span><input value={ownerInput} onChange={(event) => setOwnerInput(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter' && ownerInput.trim()) onScopeChange({ ownerUserId: ownerInput.trim(), botId: undefined }) }} placeholder="输入用户工号/账号，* 表示全部用户" className="w-full rounded-lg border border-gray-200 bg-white px-3 py-2.5 text-sm outline-none focus:border-amber-500" /></label>
          <button disabled={!ownerInput.trim() || ownerInput.trim() === ownerUserId} onClick={() => onScopeChange({ ownerUserId: ownerInput.trim(), botId: undefined })} className="self-end rounded-lg bg-gray-900 px-4 py-2.5 text-sm font-medium text-white hover:bg-gray-800 disabled:cursor-not-allowed disabled:opacity-40">应用用户范围</button>
          <p className="text-[11px] text-gray-500 md:col-span-2">当前查看：<span className="font-mono font-medium text-amber-700">{ownerUserId === '*' ? '全部用户' : ownerUserId || '尚未选择用户'}</span>。创建时可按 Bot Owner 分发，也可改派给指定用户。</p>
        </div>}
        <div className="flex flex-wrap items-end gap-4">
          <label className="min-w-64 flex-1"><span className="mb-1.5 block text-xs font-medium text-gray-500">Bot</span>{adminMode ? <><input value={scope.botId ?? ''} list="insight-admin-bots" onChange={(event) => onScopeChange({ botId: event.target.value || undefined })} placeholder="留空查看当前用户范围内全部 Bot；也可直接输入 bot_id" className="w-full rounded-lg border border-gray-200 bg-white px-3 py-2.5 text-sm outline-none focus:border-blue-500" /><datalist id="insight-admin-bots">{visibleBotOptions.map((bot) => <option key={bot.botId} value={bot.botId}>{bot.botName}</option>)}</datalist></> : <select value={scope.botId ?? ''} onChange={(event) => onScopeChange({ botId: event.target.value || undefined })} className="w-full rounded-lg border border-gray-200 bg-white px-3 py-2.5 text-sm outline-none focus:border-blue-500"><option value="">我的全部 Bot</option>{visibleBotOptions.map((bot) => <option key={bot.botId} value={bot.botId}>{bot.botName} · {bot.botId}</option>)}</select>}</label>
          <label><span className="mb-1.5 block text-xs font-medium text-gray-500">失败分类</span><select value={failureClass ?? ''} onChange={(event) => onScopeChange({ failureClass: event.target.value || undefined })} className="min-w-48 rounded-lg border border-gray-200 bg-white px-3 py-2.5 text-sm outline-none focus:border-blue-500"><option value="">全部分类</option>{failureOptions.map((value) => <option key={value} value={value}>{failureClassText[value] ?? value}</option>)}</select></label>
          <label><span className="mb-1.5 block text-xs font-medium text-gray-500">定时任务</span><select value={scope.isCron == null ? '' : String(scope.isCron)} onChange={(event) => onScopeChange({ isCron: event.target.value === '' ? undefined : event.target.value === 'true' })} className="rounded-lg border border-gray-200 bg-white px-3 py-2.5 text-sm outline-none focus:border-blue-500"><option value="">全部</option><option value="true">Y</option><option value="false">N</option></select></label>
          <button onClick={() => setReloadKey((value) => value + 1)} className="inline-flex items-center gap-2 rounded-lg border border-gray-200 px-3 py-2.5 text-sm font-medium text-gray-600 hover:bg-gray-50"><InsightIcon name="refresh" />刷新</button>
        </div>
      </section>

      {creationMessage && <div className="flex items-center justify-between rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-xs text-emerald-800"><span>{creationMessage}</span><button onClick={() => setCreationMessage('')} className="font-medium">关闭</button></div>}

      <section className="overflow-hidden rounded-2xl border border-gray-200 bg-white shadow-sm">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-gray-100 px-5 py-4">
          <div><h2 className="text-sm font-semibold text-gray-900">失败任务</h2><p className="mt-1 text-xs text-gray-400">可跨 Bot 多选；创建时按 Bot 自动拆成独立改进项{dataAsOf ? ` · 数据更新至 ${formatDateTime(dataAsOf)}` : ''}</p></div>
          <div className="flex items-center gap-3"><span className="text-xs text-gray-500">已选择 {selected.size} 个 · {selectedBotCount} 个 Bot</span><button disabled={!selected.size} onClick={() => setCreating(true)} className={`inline-flex items-center gap-2 rounded-lg px-3.5 py-2 text-xs font-medium text-white disabled:cursor-not-allowed disabled:opacity-40 ${adminMode ? 'bg-amber-600 hover:bg-amber-700' : 'bg-blue-600 hover:bg-blue-700'}`}><InsightIcon name="plus" />{adminMode ? '创建并发布给用户' : '创建改进项'}</button></div>
        </div>

        {loading ? <LoadingPanel text="正在读取失败任务索引…" /> : error ? <ErrorPanel message={error} onRetry={() => setReloadKey((value) => value + 1)} /> : items.length === 0 ? <EmptyPanel title="当前范围没有失败任务" description="可以调整 Bot、分类或日期范围。" /> : <div className="overflow-x-auto">
          <table className="w-full min-w-[980px] table-fixed text-left text-sm">
            <colgroup><col className="w-12" /><col className="w-[28%]" /><col className="w-[18%]" /><col className="w-[30%]" /><col className="w-24" /><col className="w-[14%]" /><col className="w-20" /></colgroup>
            <thead className="bg-gray-50/80 text-xs font-medium text-gray-500"><tr><th className="px-4 py-3"><input type="checkbox" checked={allSelected} disabled={!items.length} onChange={() => setSelected(allSelected ? new Set() : new Set(items.map(taskKey)))} title="全选当前页面的可见失败任务" className="h-4 w-4 rounded border-gray-300" /></th><th className="px-3 py-3">失败任务</th><th className="px-4 py-3">Bot</th><th className="px-4 py-3">失败原因</th><th className="px-4 py-3">定时任务</th><th className="px-4 py-3">时间</th><th className="px-4 py-3 text-right">操作</th></tr></thead>
            <tbody className="divide-y divide-gray-100">{items.map((item) => {
              const checked = selected.has(taskKey(item))
              const exceptionalState = item.isComplete === 2 ? '无法判断' : item.isComplete === 3 ? '任务中止' : null
              return <tr key={taskKey(item)} className={`transition hover:bg-gray-50/70 ${checked ? 'bg-blue-50/40' : ''}`}><td className="px-4 py-4 align-top"><input type="checkbox" checked={checked} onChange={() => toggle(item)} title="选择该失败任务" className="mt-1 h-4 w-4 rounded border-gray-300" /></td><td className="px-3 py-4 align-top"><button onClick={() => setDrawerTask(item)} className="block w-full text-left"><p className="line-clamp-2 font-medium leading-5 text-gray-900 hover:text-blue-700">{item.taskDescription}</p></button></td><td className="px-4 py-4 align-top"><p className="truncate text-xs font-medium text-gray-700" title={item.botName}>{item.botName}</p><p className="mt-1 truncate font-mono text-[10px] text-gray-400" title={item.botId}>{item.botId}</p>{adminMode && <p className="mt-2 truncate text-[10px] text-amber-700" title={item.ownerUserId}>归属 {item.ownerUserId}</p>}</td><td className="px-4 py-4 align-top"><div className="flex flex-wrap items-center gap-2"><span className="inline-flex rounded-full bg-orange-50 px-2.5 py-1 text-[11px] font-medium text-orange-700">{failureClassText[item.failureClass] ?? item.failureClass}</span>{exceptionalState && <span className="inline-flex rounded-full bg-gray-100 px-2.5 py-1 text-[11px] font-medium text-gray-600">{exceptionalState}</span>}</div><p className="mt-2 line-clamp-2 text-xs leading-5 text-gray-500">{item.judgeReasonSummary || '暂无失败原因摘要'}</p></td><td className="px-4 py-4 align-top text-xs font-medium text-gray-700">{item.isCron ? 'Y' : 'N'}</td><td className="px-4 py-4 align-top text-xs leading-5 text-gray-500"><p>{formatDateTime(item.sessionStartTime)}</p>{item.sessionDurationSeconds != null && <p className="mt-1 text-[10px] text-gray-400">耗时 {formatDuration(item.sessionDurationSeconds)}</p>}</td><td className="px-4 py-4 text-right align-top"><button onClick={() => setDrawerTask(item)} className="font-medium text-blue-600 hover:text-blue-700">查看</button></td></tr>
            })}</tbody>
          </table>
        </div>}
        {(pageIndex > 0 || nextCursor) && !loading && !error && <div className="flex items-center justify-center gap-3 border-t border-gray-100 p-4">
          <button disabled={loadingMore || pageIndex === 0} onClick={async () => {
            const targetIndex = pageIndex - 1
            const cursor = pageCursors[targetIndex]
            setLoadingMore(true)
            try {
              const result = await insightApi.failureTasks({ ownerUserId, botId, from, to, isCron, failureClass, completionStates: [0, 2, 3], cursor: cursor ?? undefined, pageSize: 20 })
              setItems(result.items); setNextCursor(result.nextCursor); setDataAsOf(result.dataAsOf); setPageIndex(targetIndex); setSelected(new Set())
            } catch (reason) { setError(reason instanceof Error ? reason.message : '上一页失败任务加载失败') } finally { setLoadingMore(false) }
          }} className="rounded-lg border border-gray-200 px-4 py-2 text-xs font-medium text-gray-600 hover:bg-gray-50 disabled:opacity-40">上一页</button>
          <span className="text-xs text-gray-400">第 {pageIndex + 1} 页 · 每页最多 20 条</span>
          <button disabled={loadingMore || !nextCursor} onClick={async () => {
            if (!nextCursor) return
            setLoadingMore(true)
            try {
              const result = await insightApi.failureTasks({ ownerUserId, botId, from, to, isCron, failureClass, completionStates: [0, 2, 3], cursor: nextCursor, pageSize: 20 })
              const targetIndex = pageIndex + 1
              setPageCursors((current) => [...current.slice(0, targetIndex), nextCursor]); setItems(result.items); setNextCursor(result.nextCursor); setDataAsOf(result.dataAsOf); setPageIndex(targetIndex); setSelected(new Set())
            } catch (reason) { setError(reason instanceof Error ? reason.message : '下一页失败任务加载失败') } finally { setLoadingMore(false) }
          }} className="rounded-lg border border-gray-200 px-4 py-2 text-xs font-medium text-gray-600 hover:bg-gray-50 disabled:opacity-40">下一页</button>
        </div>}
      </section>

      {drawerTask && <FailureTaskDrawer task={drawerTask} onClose={() => setDrawerTask(null)} />}
      {creating && <CreateImprovementModal tasks={selectedTasks} adminMode={adminMode} onClose={() => setCreating(false)} onCreated={(improvements) => { setCreating(false); setSelected(new Set()); if (adminMode) setCreationMessage(`已按 ${improvements.length} 个 Bot 创建改进项，被指派用户可在“待我处理”中查看。`); else if (improvements[0]) onImprovementCreated(improvements[0].improvementId) }} />}
    </div>
  )
}
