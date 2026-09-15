import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useLocation, useNavigate, useSearchParams } from 'react-router-dom'
import { api, type EvolveSkillAsset, type EvolveSkillEvent } from '../api/client'

import SkillTaskLaunchDialog, { type SkillTaskAction } from '../components/SkillTaskLaunchDialog'
import TestBenchComparison from '../components/TestBenchComparison'
import { createUnifiedDiff, GitDiffView } from './evolve/common'
import { formatStepTime, timestampMs } from './evolve/helpers'

type Content = Awaited<ReturnType<typeof api.evolve.getSkillVersionContent>>
type Diff = Awaited<ReturnType<typeof api.evolve.getSkillVersionDiff>>
type Version = NonNullable<EvolveSkillAsset['versions']>[number]
type ViewerTab = 'task' | 'content' | 'diff'
type Selection = { kind: 'version'; version: Version } | { kind: 'event'; event: EvolveSkillEvent }

const eventNames = { registered: '登记', diagnosis: '诊断', hardening: '加固', optimization: '优化' } as const
const statusNames = {
  running: '运行中', waiting_user_input: '等待用户输入', waiting_acceptance: '等待版本确认',
  completed: '已完成', failed: '失败', canceled: '已取消',
} as const
const statusTones = {
  running: 'border-blue-200 bg-blue-50 text-blue-700',
  waiting_user_input: 'border-amber-300 bg-amber-50 text-amber-800',
  waiting_acceptance: 'border-violet-200 bg-violet-50 text-violet-700',
  completed: 'border-emerald-200 bg-emerald-50 text-emerald-700',
  failed: 'border-red-200 bg-red-50 text-red-700',
  canceled: 'border-gray-200 bg-gray-100 text-gray-600',
} as const
const eventTones = {
  registered: 'bg-blue-50 text-blue-700', diagnosis: 'bg-amber-50 text-amber-700', hardening: 'bg-violet-50 text-violet-700', optimization: 'bg-emerald-50 text-emerald-700',
} as const

function eventVersionId(event: EvolveSkillEvent, fallbackVersionId: string): string {
  return event.versionFrom?.versionId ?? event.versionTo?.versionId ?? fallbackVersionId
}

function taskDetailPath(taskId: string, returnTo: string): string {
  const query = new URLSearchParams({ returnTo })
  return `/evolve/runs/${encodeURIComponent(taskId)}?${query}`
}

function VersionDiff({ diff }: { diff: Diff | null }) {
  const [mode, setMode] = useState<'git' | 'full'>('git')
  if (!diff?.baseline) return <p className="py-12 text-center text-sm text-gray-400">这是登记时的初始版本，没有进化前后差异。</p>
  return <div className="space-y-4 p-5">
    <div className="flex justify-end"><div className="inline-flex rounded-lg border border-gray-200 bg-gray-50 p-1">
      <button type="button" aria-pressed={mode === 'git'} onClick={() => setMode('git')} className={`rounded-md px-3 py-1.5 text-xs font-medium ${mode === 'git' ? 'bg-white text-blue-700 shadow-sm' : 'text-gray-500 hover:text-gray-700'}`}>Git Diff</button>
      <button type="button" aria-pressed={mode === 'full'} onClick={() => setMode('full')} className={`rounded-md px-3 py-1.5 text-xs font-medium ${mode === 'full' ? 'bg-white text-blue-700 shadow-sm' : 'text-gray-500 hover:text-gray-700'}`}>完整对比</button>
    </div></div>
    {mode === 'git'
      ? <GitDiffView content={createUnifiedDiff(diff.files)} />
      : diff.files.map((file) => <div key={file.path} className="overflow-hidden rounded-xl border border-gray-200"><div className="flex items-center justify-between bg-gray-50 px-4 py-2"><span className="font-mono text-xs text-gray-700">{file.path}</span><span className="text-xs text-gray-500">{file.change === 'added' ? '新增' : file.change === 'deleted' ? '删除' : '修改'}</span></div><div className="grid md:grid-cols-2"><div className="min-w-0 border-r border-gray-100"><p className="border-b border-red-100 bg-red-50/50 px-3 py-2 text-[10px] font-medium text-red-700">进化前</p><pre className="max-h-72 overflow-auto bg-red-50/30 p-3 text-[11px] leading-5 text-gray-700">{file.before ?? '—'}</pre></div><div className="min-w-0"><p className="border-b border-emerald-100 bg-emerald-50/50 px-3 py-2 text-[10px] font-medium text-emerald-700">当前版本</p><pre className="max-h-72 overflow-auto bg-emerald-50/30 p-3 text-[11px] leading-5 text-gray-700">{file.after ?? '—'}</pre></div></div></div>)}
  </div>
}

function VersionContent({ content, path, onPathChange }: { content: Content | null; path: string; onPathChange: (path: string) => void }) {
  return <div className="grid min-h-[520px] lg:grid-cols-[220px_minmax(0,1fr)]">
    <aside className="border-b border-gray-100 p-3 lg:border-b-0 lg:border-r">
      <p className="px-3 pb-2 text-[10px] font-semibold uppercase tracking-wider text-gray-400">文件</p>
      {content?.files.map((file) => <button key={file.path} disabled={!file.text} onClick={() => onPathChange(file.path)} className={`block w-full truncate rounded-lg px-3 py-2 text-left font-mono text-xs ${path === file.path ? 'bg-blue-50 text-blue-700' : 'text-gray-600 hover:bg-gray-50'} disabled:text-gray-300`}>{file.path}</button>)}
    </aside>
    <pre className="min-w-0 overflow-auto bg-[#0b1020] p-5 text-xs leading-6 text-gray-100">{content?.selected?.content ?? '请选择可预览的文本文件'}</pre>
  </div>
}

function SkillHistoryTimeline({ asset, events, selection, onSelect }: {
  asset: EvolveSkillAsset
  events: EvolveSkillEvent[]
  selection: Selection
  onSelect: (value: string, tab: ViewerTab) => void
}) {
  const versions = [...(asset.versions ?? [])].sort((left, right) => (timestampMs(right.createdAt) ?? 0) - (timestampMs(left.createdAt) ?? 0))
  const fallbackVersionId = versions[0]?.versionId ?? ''
  const byVersion = new Map<string, EvolveSkillEvent[]>()
  const unassigned: EvolveSkillEvent[] = []
  for (const event of events.filter((item) => item.type !== 'registered')) {
    const versionId = eventVersionId(event, fallbackVersionId)
    if (!versions.some((version) => version.versionId === versionId)) { unassigned.push(event); continue }
    const current = byVersion.get(versionId) ?? []
    current.push(event)
    byVersion.set(versionId, current)
  }
  const sortedEvents = (items: EvolveSkillEvent[]) => [...items].sort((left, right) => (timestampMs(right.startedAt) ?? 0) - (timestampMs(left.startedAt) ?? 0))
  const EventButton = ({ event }: { event: EvolveSkillEvent }) => {
    const active = selection.kind === 'event' && selection.event.eventId === event.eventId
    return <button type="button" onClick={() => onSelect(event.taskId ? `task:${event.taskId}` : `event:${event.eventId}`, 'task')} className={`relative ml-4 block w-[calc(100%_-_1rem)] rounded-xl border px-3 py-3 text-left transition ${active ? 'border-blue-300 bg-blue-50 shadow-sm' : event.status === 'waiting_user_input' ? 'border-amber-200 bg-amber-50/70 hover:border-amber-300' : 'border-transparent hover:border-gray-200 hover:bg-gray-50'}`}>
      <span className={`absolute -left-[1.35rem] top-4 h-2.5 w-2.5 rounded-full ring-4 ring-white ${event.type === 'diagnosis' ? 'bg-amber-400' : event.type === 'hardening' ? 'bg-violet-500' : 'bg-emerald-500'}`} />
      <span className="flex items-center justify-between gap-2"><span className={`rounded-md px-2 py-1 text-[11px] font-medium ${eventTones[event.type]}`}>{eventNames[event.type]}</span><span className="text-[10px] text-gray-400">{formatStepTime(event.startedAt)}</span></span>
      <span className={`mt-2 inline-flex rounded-full border px-2 py-0.5 text-[10px] font-medium ${statusTones[event.status]}`}>{statusNames[event.status]}</span>
      {event.summary && <span className="mt-1.5 block truncate text-xs text-gray-500" title={event.summary}>{event.summary}</span>}
      {(event.versionFrom || event.versionTo) && <span className="mt-1 block font-mono text-[10px] text-gray-400">{event.versionFrom?.version ?? '—'}{event.versionTo ? ` → ${event.versionTo.version}` : ''}</span>}
    </button>
  }
  return <aside aria-label="Skill 迭代时间线" className="border-b border-gray-100 bg-gray-50/50 p-4 lg:border-b-0 lg:border-r">
    <div className="mb-4"><h2 className="text-sm font-semibold text-gray-900">迭代时间线</h2><p className="mt-1 text-xs text-gray-400">{versions.length} 个版本 · {events.filter((item) => item.type === 'diagnosis').length} 次诊断 · {events.filter((item) => item.type === 'hardening').length} 次加固 · {events.filter((item) => item.type === 'optimization').length} 次优化</p></div>
    <div className="max-h-[760px] space-y-3 overflow-auto pr-1">
      {unassigned.length > 0 && <div className="border-l border-gray-200 pb-2">{sortedEvents(unassigned).map((event) => <EventButton key={event.eventId} event={event} />)}</div>}
      {versions.map((version) => {
        const active = selection.kind === 'version' && selection.version.versionId === version.versionId
        const related = sortedEvents(byVersion.get(version.versionId) ?? [])
        const current = version.version === asset.currentVersion
        return <section key={version.versionId} className="border-l border-gray-200 pb-2">
          <button type="button" onClick={() => onSelect(`version:${version.versionId}`, 'content')} className={`relative ml-4 block w-[calc(100%_-_1rem)] rounded-xl border px-3 py-3 text-left transition ${active ? 'border-blue-300 bg-white shadow-sm' : 'border-gray-200 bg-white hover:border-blue-200'}`}>
            <span className={`absolute -left-[1.48rem] top-4 h-3.5 w-3.5 rounded-full ring-4 ring-gray-50 ${current ? 'bg-blue-600' : 'bg-gray-400'}`} />
            <span className="flex items-center justify-between gap-2"><strong className="text-sm text-gray-950">{version.version}</strong>{current && <span className="rounded-full bg-emerald-50 px-2 py-0.5 text-[10px] font-medium text-emerald-700">当前版本</span>}</span>
            <span className="mt-1 block text-[10px] text-gray-400">创建于 {formatStepTime(version.createdAt)}</span>
          </button>
          <div className="mt-1 space-y-1">{related.map((event) => <EventButton key={event.eventId} event={event} />)}</div>
        </section>
      })}
      {versions.length === 0 && events.length === 0 && <p className="py-10 text-center text-sm text-gray-400">暂无迭代记录</p>}
    </div>
  </aside>
}

function EventSummary({ event, returnTo }: { event: EvolveSkillEvent; returnTo: string }) {
  const versionText = event.versionTo
    ? `${event.versionFrom?.version ?? '—'} → ${event.versionTo.version}`
    : event.versionFrom?.version ? `基于 ${event.versionFrom.version}` : '未关联版本'
  return <div className="space-y-5 p-5">
    {event.status === 'waiting_user_input' && <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3"><div><p className="text-sm font-semibold text-amber-900">等待用户输入</p><p className="mt-1 text-xs text-amber-700">任务需要完成交互后才能继续。</p></div>{event.taskId && <Link className="rounded-lg bg-amber-600 px-3 py-2 text-xs font-medium text-white hover:bg-amber-700" to={taskDetailPath(event.taskId, returnTo)}>去处理</Link>}</div>}
    <div className="rounded-xl border border-gray-200 p-4">
      <div className="flex flex-wrap items-start justify-between gap-3"><div><span className={`inline-flex rounded-md px-2 py-1 text-xs font-medium ${eventTones[event.type]}`}>{eventNames[event.type]}</span><h3 className="mt-3 text-lg font-semibold text-gray-950">{event.summary || `${eventNames[event.type]}任务`}</h3><p className="mt-1 font-mono text-xs text-gray-400">{event.taskId ?? event.eventId}</p></div><span className={`inline-flex rounded-full border px-2.5 py-1 text-xs font-medium ${statusTones[event.status]}`}>{statusNames[event.status]}</span></div>
      {event.outcome && <p className="mt-4 whitespace-pre-wrap rounded-lg bg-gray-50 px-3 py-2 text-sm leading-6 text-gray-700">{event.outcome}</p>}
      <dl className="mt-4 grid gap-3 text-xs text-gray-500 sm:grid-cols-2"><div><dt className="text-gray-400">版本</dt><dd className="mt-1 font-mono text-gray-700">{versionText}</dd></div><div><dt className="text-gray-400">发起时间</dt><dd className="mt-1 text-gray-700">{formatStepTime(event.startedAt)}</dd></div><div><dt className="text-gray-400">完成时间</dt><dd className="mt-1 text-gray-700">{formatStepTime(event.completedAt)}</dd></div><div><dt className="text-gray-400">发起人</dt><dd className="mt-1 font-mono text-gray-700">{event.actorType === 'system' ? '系统' : event.actorId ?? '—'}</dd></div></dl>
    </div>
    <div className="rounded-xl border border-gray-200 p-4"><h3 className="text-sm font-semibold text-gray-900">Test Bench</h3><div className="mt-3"><TestBenchComparison comparison={event.testBench?.scoreComparison} emptyLabel={event.testBench ? '未评测' : '未记录评测关联'} /></div></div>
    {event.taskId && <div className="flex justify-end"><Link className="inline-flex rounded-lg border border-blue-200 bg-blue-50 px-3 py-2 text-xs font-medium text-blue-700 hover:bg-blue-100" to={taskDetailPath(event.taskId, returnTo)}>查看完整执行记录 ↗</Link></div>}
  </div>
}

export default function SkillDetail() {
  const navigate = useNavigate()
  const location = useLocation()
  const [searchParams, setSearchParams] = useSearchParams()
  const assetId = decodeURIComponent(location.pathname.split('/').filter(Boolean).at(-1) ?? '')
  const [launchAction, setLaunchAction] = useState<SkillTaskAction | null>(null)
  const [asset, setAsset] = useState<EvolveSkillAsset | null>(null)
  const [events, setEvents] = useState<EvolveSkillEvent[]>([])
  const [path, setPath] = useState('')
  const [content, setContent] = useState<Content | null>(null)
  const [diff, setDiff] = useState<Diff | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const contentRequest = useRef(0)

  useEffect(() => {
    let active = true
    setLoading(true)
    void Promise.all([api.evolve.getSkillAsset(assetId), api.evolve.getSkillAssetHistory(assetId)])
      .then(([nextAsset, history]) => { if (active) { setAsset(nextAsset); setEvents(history.events) } })
      .catch((reason) => { if (active) setError(reason instanceof Error ? reason.message : 'Skill 加载失败') })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [assetId])

  const selection = useMemo<Selection | null>(() => {
    if (!asset) return null
    const requested = searchParams.get('selected') ?? ''
    if (requested.startsWith('task:')) {
      const event = events.find((item) => item.taskId === requested.slice(5))
      if (event) return { kind: 'event', event }
    }
    if (requested.startsWith('event:')) {
      const event = events.find((item) => item.eventId === requested.slice(6))
      if (event) return { kind: 'event', event }
    }
    if (requested.startsWith('version:')) {
      const version = asset.versions?.find((item) => item.versionId === requested.slice(8))
      if (version) return { kind: 'version', version }
    }
    const current = asset.versions?.find((item) => item.version === asset.currentVersion) ?? asset.versions?.[0]
    return current ? { kind: 'version', version: current } : null
  }, [asset, events, searchParams])

  const activeVersionId = selection?.kind === 'version'
    ? selection.version.versionId
    : selection?.event.versionTo?.versionId ?? selection?.event.versionFrom?.versionId ?? asset?.versions?.[0]?.versionId ?? ''
  const requestedTab = searchParams.get('view') as ViewerTab | null
  const tab: ViewerTab = selection?.kind === 'event'
    ? (requestedTab && ['task', 'content', 'diff'].includes(requestedTab) ? requestedTab : 'task')
    : (requestedTab === 'diff' ? 'diff' : 'content')
  const selectedVersion = asset?.versions?.find((item) => item.versionId === activeVersionId) ?? null
  const returnTo = `${location.pathname}${location.search}`
  const requestedBackTo = searchParams.get('backTo')
  const backTo = requestedBackTo?.startsWith('/evolve/skills') && !requestedBackTo.startsWith('//') ? requestedBackTo : '/evolve/skills'
  const backLabel = backTo === '/evolve/skills/events' ? '返回技能事件日志' : '返回技能中心'

  useEffect(() => {
    if (!activeVersionId) { setContent(null); setDiff(null); setPath(''); return }
    const request = ++contentRequest.current
    let active = true
    setContent(null); setDiff(null); setPath('')
    void Promise.all([
      api.evolve.getSkillVersionContent(assetId, activeVersionId),
      api.evolve.getSkillVersionDiff(assetId, activeVersionId),
    ]).then(([nextContent, nextDiff]) => {
      if (!active || request !== contentRequest.current) return
      setContent(nextContent); setDiff(nextDiff); setPath(nextContent.selected?.path ?? nextContent.files[0]?.path ?? '')
    }).catch((reason) => { if (active && request === contentRequest.current) setError(reason instanceof Error ? reason.message : '版本内容加载失败') })
    return () => { active = false }
  }, [assetId, activeVersionId])

  const loadPath = (nextPath: string) => {
    if (!activeVersionId || nextPath === path) return
    const request = ++contentRequest.current
    setPath(nextPath)
    void api.evolve.getSkillVersionContent(assetId, activeVersionId, nextPath)
      .then((value) => { if (request === contentRequest.current) setContent(value) })
      .catch((reason) => { if (request === contentRequest.current) setError(reason instanceof Error ? reason.message : '文件加载失败') })
  }

  const select = (value: string, nextTab: ViewerTab) => {
    const next = new URLSearchParams(searchParams)
    next.set('selected', value)
    next.set('view', nextTab)
    setSearchParams(next)
  }
  const setTab = (nextTab: ViewerTab) => {
    const next = new URLSearchParams(searchParams)
    next.set('view', nextTab)
    setSearchParams(next, { replace: true })
  }

  if (loading) return <div className="mx-auto max-w-7xl px-4 py-20 text-center text-sm text-gray-500">正在加载 Skill 详情…</div>
  return <div className="mx-auto max-w-[1480px] px-4 py-7 sm:px-6 lg:px-8">
    <button className="mb-5 text-sm text-gray-500 hover:text-gray-800" onClick={() => navigate(backTo)}>← {backLabel}</button>
    <div className="flex flex-wrap items-start justify-between gap-4"><div><p className="text-sm font-medium text-blue-600">Skill 详情</p><h1 className="mt-1 text-2xl font-semibold text-gray-950">{asset?.name ?? 'Skill 不存在'}</h1>{asset && <><p className="mt-1 font-mono text-xs text-gray-400">{asset.botId} / {asset.skillId}</p>{asset.description && <p className="mt-2 max-w-2xl text-sm leading-6 text-gray-500">{asset.description}</p>}</>}</div>{asset && <div className="flex gap-2"><button onClick={() => setLaunchAction('diagnose')} className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-2.5 text-sm font-medium text-amber-700 hover:bg-amber-100">诊断</button><button onClick={() => setLaunchAction('hardening')} className="rounded-lg border border-violet-200 bg-violet-50 px-4 py-2.5 text-sm font-medium text-violet-700 hover:bg-violet-100">加固</button><button onClick={() => setLaunchAction('optimize')} className="rounded-lg bg-emerald-600 px-4 py-2.5 text-sm font-medium text-white hover:bg-emerald-700">优化</button></div>}</div>
    {asset && selection && <section className="mt-5 overflow-hidden rounded-2xl border border-gray-200 bg-white shadow-sm lg:grid lg:grid-cols-[340px_minmax(0,1fr)]">
      <SkillHistoryTimeline asset={asset} events={events} selection={selection} onSelect={select} />
      <div className="min-w-0">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-gray-100 px-5 py-4"><div><p className="text-xs font-medium text-gray-400">当前查看</p><h2 className="mt-1 text-base font-semibold text-gray-950">{selection.kind === 'version' ? `${selection.version.version} 版本` : `${eventNames[selection.event.type]}记录`}</h2></div><div className="flex flex-wrap items-center gap-2">{selection.kind === 'event' && <button type="button" onClick={() => setTab('task')} className={`rounded-lg px-3 py-2 text-xs font-medium ${tab === 'task' ? 'bg-blue-50 text-blue-700' : 'text-gray-500 hover:bg-gray-50'}`}>任务结果</button>}<button type="button" onClick={() => setTab('content')} className={`rounded-lg px-3 py-2 text-xs font-medium ${tab === 'content' ? 'bg-blue-50 text-blue-700' : 'text-gray-500 hover:bg-gray-50'}`}>Skill 内容</button><button type="button" onClick={() => setTab('diff')} className={`rounded-lg px-3 py-2 text-xs font-medium ${tab === 'diff' ? 'bg-blue-50 text-blue-700' : 'text-gray-500 hover:bg-gray-50'}`}>版本 Diff</button></div></div>
        {tab === 'task' && selection.kind === 'event' && <EventSummary event={selection.event} returnTo={returnTo} />}
        {tab === 'content' && <><div className="flex flex-wrap items-center justify-between gap-3 border-b border-gray-100 px-5 py-3"><p className="text-xs text-gray-500">{selectedVersion?.version ?? '未关联版本'} 的冻结内容</p>{selectedVersion?.sourceTaskId && <Link className="text-xs font-medium text-blue-600" to={taskDetailPath(selectedVersion.sourceTaskId, returnTo)}>查看来源任务 ↗</Link>}</div><VersionContent content={content} path={path} onPathChange={loadPath} /></>}
        {tab === 'diff' && <><div className="border-b border-gray-100 px-5 py-3 text-xs text-gray-500">{selectedVersion?.version ?? '未关联版本'} 与其进化前版本的差异</div><VersionDiff key={activeVersionId} diff={diff} /></>}
      </div>
    </section>}
    {asset && !selection && <section className="mt-5 rounded-2xl border border-gray-200 bg-white py-16 text-center text-sm text-gray-400">该 Skill 暂无可查看的版本。</section>}
    {asset && launchAction && <SkillTaskLaunchDialog key={`${asset.assetId}:${launchAction}`} asset={asset} action={launchAction} returnTo={returnTo} onClose={() => setLaunchAction(null)} />}
    {error && <p role="alert" className="mt-4 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>}
  </div>
}
