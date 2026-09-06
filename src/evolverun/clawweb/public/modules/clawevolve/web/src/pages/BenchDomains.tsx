import { useEffect, useMemo, useRef, useState } from 'react'
import { useLocation, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import {
  useArchiveBenchDomain,
  useArchiveBenchTemplate,
  useAdminBenchRuns,
  useBatchPublishBenchTemplates,
  useBenchDomain,
  useBenchDomainSummary,
  useBenchDomains,
  useBenchRuns,
  useBenchTemplates,
  useCreateBenchDomain,
  usePublishBenchTemplate,
  useScanBenchTemplateUpload,
  useUpdateBenchDomain,
} from '../api/hooks'
import type { BenchDomain, BenchRun, BenchUploadScanItem } from '../types'
import { benchStatusLabel, benchText } from '../bench/i18n'
import { BenchEmptyState, BenchErrorState, BenchLoadingState } from '../bench/ui-state'
import { formatTokenUsage } from '../bench/token'
import { useClientUser } from '../hooks/useClientUser'
import { useEvolveAdminScope } from '../features/evolve/admin-scope'

function domainPath(domain: Pick<BenchDomain, 'ownerUserId' | 'domainId'>, basePath: string): string {
  return `${basePath}/domains/${encodeURIComponent(domain.ownerUserId)}/${encodeURIComponent(domain.domainId)}`
}

function templatePath(domain: Pick<BenchDomain, 'ownerUserId' | 'domainId'>, templateName: string, basePath: string): string {
  return `${domainPath(domain, basePath)}/templates/${encodeURIComponent(templateName)}`
}

function formatTime(value: number | null): string {
  return value ? new Date(value * 1000).toLocaleString() : '-'
}

function formatDuration(run: BenchRun): string {
  if (!run.startedAt || !run.completedAt) return run.status === 'running' ? 'Running' : '-'
  const seconds = Math.max(0, run.completedAt - run.startedAt)
  if (seconds < 60) return `${seconds}s`
  const minutes = Math.floor(seconds / 60)
  const rest = seconds % 60
  if (minutes < 60) return `${minutes}m ${rest}s`
  const hours = Math.floor(minutes / 60)
  return `${hours}h ${minutes % 60}m`
}

function formatNumber(value: number | string): string {
  const n = typeof value === 'number' ? value : Number(value)
  if (!Number.isFinite(n)) return String(value)
  if (Number.isInteger(n)) return String(n)
  return Number(n.toFixed(4)).toString()
}

function formatScore(score: number | string | null, maxScore: number | string | null): string {
  if (score === null || maxScore === null) return '-'
  return `${formatNumber(score)} / ${formatNumber(maxScore)}`
}

function formatRunVersion(run: BenchRun): string {
  if (run.runScope !== 'domain') return run.templateVersion ? `v${run.templateVersion}` : '-'
  const templates = run.runConfig?.templates
  if (!Array.isArray(templates)) return '-'
  const versions = Array.from(new Set(
    templates
      .map((item) => {
        if (!item || typeof item !== 'object') return null
        const version = (item as Record<string, unknown>).templateVersion
        return version === null || version === undefined || version === '' ? null : Number(version)
      })
      .filter((version): version is number => Number.isFinite(version)),
  )).sort((a, b) => a - b)
  if (versions.length === 0) return '-'
  if (versions.length === 1) return `v${versions[0]}`
  return `多版本 ${versions.map((version) => `v${version}`).join('/')}`
}

export default function BenchDomains({ basePath = '/bench' }: { basePath?: string }) {
  const { ownerUserId: urlOwnerUserId, domainId: urlDomainId } = useParams<{ ownerUserId: string; domainId: string }>()
  const [searchParams] = useSearchParams()
  const location = useLocation()
  const navigate = useNavigate()
  const { user } = useClientUser()
  const { enabled: adminMode, ownerUserId: adminOwnerUserId } = useEvolveAdminScope()
  const { data: domains, isLoading: domainsLoading, isError: domainsError } = useBenchDomains({ admin: adminMode, ownerUserId: adminOwnerUserId })
  const isAll = !urlOwnerUserId || !urlDomainId
  const isRunList = location.pathname === `${basePath}/runs`
  const { data: domainDetail } = useBenchDomain(isAll ? '' : urlOwnerUserId ?? '', isAll ? '' : urlDomainId ?? '')
  const selectedDomain = useMemo(
    () => domainDetail ?? domains?.find((d) => d.ownerUserId === urlOwnerUserId && d.domainId === urlDomainId) ?? null,
    [domainDetail, domains, urlOwnerUserId, urlDomainId],
  )

  const { data: templates, isLoading: templatesLoading, isError: templatesError } = useBenchTemplates(
    isAll ? undefined : urlDomainId,
    isAll ? undefined : { ownerUserId: urlOwnerUserId },
  )
  const regularRunsQuery = useBenchRuns(isRunList && !adminMode
    ? { limit: 200 }
    : isAll
      ? { enabled: false }
      : { ownerUserId: urlOwnerUserId, domainId: urlDomainId, limit: 50 })
  const adminRunsQuery = useAdminBenchRuns(isRunList && adminMode
    ? { ownerUserId: adminOwnerUserId || undefined, limit: 200 }
    : { enabled: false })
  const runsData = adminMode && isRunList ? adminRunsQuery.data : regularRunsQuery.data
  const runsLoading = adminMode && isRunList ? adminRunsQuery.isLoading : regularRunsQuery.isLoading
  const runsError = adminMode && isRunList ? adminRunsQuery.isError : regularRunsQuery.isError
  const { data: domainSummary } = useBenchDomainSummary(isAll ? '' : urlOwnerUserId ?? '', isAll ? '' : urlDomainId ?? '')

  const publishMutation = usePublishBenchTemplate()
  const batchPublishMutation = useBatchPublishBenchTemplates()
  const scanMutation = useScanBenchTemplateUpload(urlOwnerUserId ?? '', urlDomainId ?? '')
  const createDomainMutation = useCreateBenchDomain()
  const updateDomainMutation = useUpdateBenchDomain()
  const archiveDomainMutation = useArchiveBenchDomain()
  const archiveTemplateMutation = useArchiveBenchTemplate()

  const [activeTab, setActiveTab] = useState<'templates' | 'runs'>(() => searchParams.get('tab') === 'runs' ? 'runs' : 'templates')
  const [showUpload, setShowUpload] = useState(false)
  const [showCreateDomain, setShowCreateDomain] = useState(false)
  const [isEditingDomain, setIsEditingDomain] = useState(false)
  const [createDomainForm, setCreateDomainForm] = useState({ domainId: '', name: '', description: '' })
  const [editDomainForm, setEditDomainForm] = useState({ name: '', description: '' })
  const [scanResult, setScanResult] = useState<BenchUploadScanItem[] | null>(null)
  const [scanSummary, setScanSummary] = useState<{ new: number; update: number; skip: number; conflict: number; imported?: number } | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [selectedTemplates, setSelectedTemplates] = useState<Set<string>>(new Set())
  const [domainQuery, setDomainQuery] = useState('')
  const [runQuery, setRunQuery] = useState('')
  const [runStatus, setRunStatus] = useState('all')
  const fileInputRef = useRef<HTMLInputElement>(null)

  const activeDomainKeys = useMemo(
    () => new Set((domains ?? []).map((d) => `${d.ownerUserId}:${d.domainId}`)),
    [domains],
  )

  useEffect(() => {
    if (searchParams.get('tab') === 'runs') {
      setActiveTab('runs')
    }
  }, [searchParams])
  const filteredTemplates = useMemo(
    () => (templates ?? []).filter((t) => !isAll || activeDomainKeys.has(`${t.ownerUserId}:${t.domainId}`)),
    [activeDomainKeys, isAll, templates],
  )
  const runs = useMemo(
    () => (runsData?.runs ?? []).filter((r) => isRunList || !isAll || activeDomainKeys.has(`${r.ownerUserId}:${r.domainId}`)),
    [activeDomainKeys, isAll, isRunList, runsData?.runs],
  )
  const canModifySelectedDomain = !!selectedDomain && user?.userId === selectedDomain.ownerUserId

  const resetUpload = () => {
    setShowUpload(false)
    setScanResult(null)
    setScanSummary(null)
    if (fileInputRef.current) fileInputRef.current.value = ''
  }

  const publishTemplates = async (templatesToPublish: Array<{ templateName: string; version?: number | null }>) => {
    if (!selectedDomain || templatesToPublish.length === 0) return
    try {
      setError(null)
      const result = await batchPublishMutation.mutateAsync({
        ownerUserId: selectedDomain.ownerUserId,
        domainId: selectedDomain.domainId,
        templates: templatesToPublish,
      })
      if (result.failed > 0) {
        setError(`批量发布完成：成功 ${result.published} 个，失败 ${result.failed} 个。${result.items.filter((item) => !item.success).slice(0, 3).map((item) => `${item.templateName}: ${item.reason}`).join('；')}`)
      } else {
        setSelectedTemplates(new Set())
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : '批量发布失败')
    }
  }

  const handleBatchPublishSelected = () => {
    void publishTemplates(Array.from(selectedTemplates).map((templateName) => ({ templateName })))
  }

  const handlePublishImported = () => {
    const imported = (scanResult ?? [])
      .filter((item) => item.imported && (item.action === 'new' || item.action === 'update'))
      .map((item) => ({ templateName: item.templateName }))
    void publishTemplates(imported)
  }

  const startDomainEdit = () => {
    if (!selectedDomain) return
    setEditDomainForm({
      name: selectedDomain.name,
      description: selectedDomain.description ?? '',
    })
    setError(null)
    setIsEditingDomain(true)
  }

  const handleSaveDomain = async () => {
    if (!selectedDomain) return
    if (!editDomainForm.name.trim()) {
      setError('Bench Domain 名称不能为空')
      return
    }
    try {
      setError(null)
      await updateDomainMutation.mutateAsync({
        ownerUserId: selectedDomain.ownerUserId,
        domainId: selectedDomain.domainId,
        input: {
          name: editDomainForm.name.trim(),
          description: editDomainForm.description.trim() || null,
        },
      })
      setIsEditingDomain(false)
    } catch (err) {
      setError(err instanceof Error ? err.message : '更新 Bench Domain 失败')
    }
  }

  const handleArchiveDomain = async () => {
    if (!selectedDomain) return
    if (!window.confirm(`确认归档 Bench Domain "${selectedDomain.name}"？历史运行记录会保留。`)) return
    try {
      setError(null)
      await archiveDomainMutation.mutateAsync({
        ownerUserId: selectedDomain.ownerUserId,
        domainId: selectedDomain.domainId,
      })
      navigate(`${basePath}/domains`)
    } catch (err) {
      setError(err instanceof Error ? err.message : '归档 Bench Domain 失败')
    }
  }

  const handleArchiveTemplate = async (templateName: string) => {
    if (!selectedDomain) return
    if (!window.confirm(`确认归档模板 "${templateName}"？后续 Bench Domain 运行不会包含它。`)) return
    try {
      setError(null)
      await archiveTemplateMutation.mutateAsync({
        ownerUserId: selectedDomain.ownerUserId,
        domainId: selectedDomain.domainId,
        templateName,
      })
    } catch (err) {
      setError(err instanceof Error ? err.message : '归档模板失败')
    }
  }

  const handleFileSelect = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files
    if (!files || files.length === 0) return
    if (!selectedDomain) {
      setError('请先选择 Bench Domain')
      return
    }
    try {
      setError(null)
      const result = await scanMutation.mutateAsync(Array.from(files))
      setScanResult(result.items as BenchUploadScanItem[])
      setScanSummary(result.summary)
    } catch (err) {
      setError(err instanceof Error ? err.message : '上传失败')
    }
    if (fileInputRef.current) fileInputRef.current.value = ''
  }

  const handleCreateDomain = async () => {
    if (!createDomainForm.domainId || !createDomainForm.name) {
      setError('Bench Domain ID 和名称不能为空')
      return
    }
    try {
      setError(null)
      const created = await createDomainMutation.mutateAsync({
        domainId: createDomainForm.domainId.trim(),
        name: createDomainForm.name.trim(),
        description: createDomainForm.description.trim() || null,
      })
      setShowCreateDomain(false)
      setCreateDomainForm({ domainId: '', name: '', description: '' })
      navigate(domainPath(created, basePath))
    } catch (err) {
      setError(err instanceof Error ? err.message : '创建 Bench Domain 失败')
    }
  }

  const statusBadge = (status: string) => {
    const map: Record<string, string> = {
      draft: 'bg-gray-100 text-gray-700',
      published: 'bg-green-100 text-green-700',
      archived: 'bg-orange-100 text-orange-700',
    }
    return <span className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${map[status] ?? 'bg-gray-100 text-gray-700'}`}>{benchStatusLabel(status)}</span>
  }

  const runStatusBadge = (status: string) => {
    const map: Record<string, string> = {
      pending: 'bg-gray-100 text-gray-700',
      running: 'bg-blue-100 text-blue-700',
      succeeded: 'bg-green-100 text-green-700',
      failed: 'bg-red-100 text-red-700',
      cancelled: 'bg-orange-100 text-orange-700',
    }
    return <span className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${map[status] ?? 'bg-gray-100 text-gray-700'}`}>{benchStatusLabel(status)}</span>
  }

  if (isRunList) {
    const query = runQuery.trim().toLowerCase()
    const visibleRuns = runs.filter((run) => {
      if (runStatus !== 'all' && run.status !== runStatus) return false
      return !query || [run.benchRunId, run.ownerUserId, run.domainId, run.templateName, run.model, run.suite, run.scene]
        .some((value) => String(value ?? '').toLowerCase().includes(query))
    })
    return <div className="w-full px-3 py-6 sm:px-4 lg:px-5">
      <div className="mb-6">
        <h1 className="text-2xl font-semibold tracking-tight text-gray-950">评估任务</h1>
        <p className="mt-1.5 text-sm text-gray-500">查看 Bench 运行状态、评估指标与详细报告。</p>
      </div>
      {adminMode && <div className="mb-3 rounded-lg border border-amber-200 bg-amber-50 px-4 py-2 text-sm text-amber-800">管理员视图：{adminOwnerUserId ? `工号 ${adminOwnerUserId}` : '全部工号'}。仅改变评估任务读取范围。</div>}
      <section className="overflow-hidden rounded-2xl border border-gray-200 bg-white shadow-sm">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-gray-100 px-5 py-4">
          <div className="flex flex-wrap gap-2">
            {([['all', '全部'], ['running', '运行中'], ['succeeded', '已成功'], ['failed', '失败'], ['cancelled', '已取消']] as const).map(([value, label]) => <button key={value} onClick={() => setRunStatus(value)} className={`rounded-full border px-3 py-1.5 text-xs font-medium ${runStatus === value ? 'border-blue-200 bg-blue-50 text-blue-700' : 'border-gray-200 text-gray-500 hover:text-gray-700'}`}>{label}</button>)}
          </div>
          <input value={runQuery} onChange={(event) => setRunQuery(event.target.value)} placeholder="搜索 Run ID、Domain、模板或模型" className="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm outline-none focus:border-blue-500 sm:w-80" />
        </div>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[1100px] text-left text-sm">
            <thead className="bg-gray-50/80 text-xs font-medium text-gray-500"><tr><th className="px-5 py-3">评估任务</th><th className="px-4 py-3">Domain / 模板</th><th className="px-4 py-3">状态</th><th className="px-4 py-3">得分</th><th className="px-4 py-3">通过率</th><th className="px-4 py-3">模型</th><th className="px-4 py-3">开始时间</th><th className="px-4 py-3">耗时</th><th className="px-4 py-3 text-right">操作</th></tr></thead>
            <tbody className="divide-y divide-gray-100">
              {visibleRuns.map((run) => <tr key={run.benchRunId} className="hover:bg-gray-50/70">
                <td className="px-5 py-4"><p className="font-mono text-xs font-medium text-gray-900">{run.benchRunId}</p><p className="mt-1 font-mono text-[10px] text-gray-400">{run.ownerUserId}</p></td>
                <td className="px-4 py-4"><p className="text-xs font-medium text-gray-800">{run.domainId}</p><p className="mt-1 max-w-56 truncate font-mono text-[10px] text-gray-400">{run.runScope === 'domain' || run.templateName === '__domain__' ? `Domain Run · ${run.templateCount ?? '?'} 个模板` : run.templateName}</p></td>
                <td className="px-4 py-4">{runStatusBadge(run.status)}</td><td className="px-4 py-4 text-xs text-gray-700">{formatScore(run.score, run.maxScore)}</td><td className="px-4 py-4 text-xs text-gray-600">{run.passRate !== null ? `${(run.passRate * 100).toFixed(1)}%` : '-'}</td><td className="px-4 py-4 text-xs text-gray-600">{run.model ?? '-'}</td><td className="px-4 py-4 whitespace-nowrap text-xs text-gray-500">{formatTime(run.startedAt)}</td><td className="px-4 py-4 whitespace-nowrap text-xs text-gray-500">{formatDuration(run)}</td>
                <td className="px-4 py-4 text-right"><button onClick={() => navigate(`${basePath}/runs/${run.benchRunId}`)} className="rounded-md border border-blue-100 bg-blue-50 px-3 py-1.5 text-xs font-medium text-blue-700 hover:bg-blue-100">查看</button></td>
              </tr>)}
            </tbody>
          </table>
        </div>
        {runsLoading && <div className="p-12"><BenchLoadingState message="正在加载评估任务..." /></div>}
        {runsError && <div className="p-12"><BenchErrorState message="评估任务加载失败" /></div>}
        {!runsLoading && !runsError && visibleRuns.length === 0 && <div className="p-12"><BenchEmptyState message="暂无符合条件的评估任务" /></div>}
        {!runsLoading && !runsError && visibleRuns.length > 0 && <div className="border-t border-gray-100 px-5 py-3 text-xs text-gray-400">显示最近 {visibleRuns.length} 条评估任务</div>}
      </section>
    </div>
  }

  if (isAll) {
    const query = domainQuery.trim().toLowerCase()
    const visibleDomains = (domains ?? []).filter((domain) => !query || [domain.name, domain.domainId, domain.ownerUserId, domain.description]
      .some((value) => String(value ?? '').toLowerCase().includes(query)))
    return <div className="w-full px-3 py-6 sm:px-4 lg:px-5">
      <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-gray-950">评估模板</h1>
          <p className="mt-1.5 text-sm text-gray-500">按 Bench Domain 管理评估模板，并查看对应运行结果。</p>
        </div>
        {!adminMode && <button onClick={() => setShowCreateDomain((value) => !value)} className="rounded-lg bg-blue-600 px-4 py-2.5 text-sm font-medium text-white shadow-sm hover:bg-blue-700">{showCreateDomain ? '取消创建' : '新建 Domain'}</button>}
      </div>
      {adminMode && <div className="mb-3 rounded-lg border border-amber-200 bg-amber-50 px-4 py-2 text-sm text-amber-800">管理员视图：{adminOwnerUserId ? `工号 ${adminOwnerUserId}` : '全部工号'}。仅改变评估列表读取范围。</div>}
      {error && <div className="mb-4 rounded-lg bg-red-50 p-3 text-sm text-red-700">{error}</div>}
      {showCreateDomain && <section className="mb-4 rounded-2xl border border-gray-200 bg-white p-5 shadow-sm">
        <h2 className="text-base font-semibold text-gray-900">创建 Bench Domain</h2>
        <div className="mt-4 grid gap-3 md:grid-cols-2">
          <label className="text-xs font-medium text-gray-700">Domain ID<input className="mt-1 block w-full rounded-lg border border-gray-200 px-3 py-2.5 text-sm" value={createDomainForm.domainId} onChange={(event) => setCreateDomainForm({ ...createDomainForm, domainId: event.target.value })} placeholder="yuque_bench" /></label>
          <label className="text-xs font-medium text-gray-700">名称<input className="mt-1 block w-full rounded-lg border border-gray-200 px-3 py-2.5 text-sm" value={createDomainForm.name} onChange={(event) => setCreateDomainForm({ ...createDomainForm, name: event.target.value })} placeholder="Yuque Bench" /></label>
        </div>
        <label className="mt-3 block text-xs font-medium text-gray-700">描述<input className="mt-1 block w-full rounded-lg border border-gray-200 px-3 py-2.5 text-sm" value={createDomainForm.description} onChange={(event) => setCreateDomainForm({ ...createDomainForm, description: event.target.value })} /></label>
        <div className="mt-4 flex justify-end"><button onClick={handleCreateDomain} disabled={createDomainMutation.isPending} className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50">{createDomainMutation.isPending ? '创建中…' : '创建 Domain'}</button></div>
      </section>}
      <section className="overflow-hidden rounded-2xl border border-gray-200 bg-white shadow-sm">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-gray-100 px-5 py-4">
          <div><p className="text-sm font-semibold text-gray-900">Bench Domain</p><p className="mt-0.5 text-xs text-gray-400">{visibleDomains.length} 个有效 Domain</p></div>
          <input value={domainQuery} onChange={(event) => setDomainQuery(event.target.value)} placeholder="搜索名称、Domain ID 或工号" className="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm outline-none focus:border-blue-500 sm:w-80" />
        </div>
        {domainsLoading && <div className="p-10"><BenchLoadingState message="正在加载 Bench Domain..." /></div>}
        {domainsError && <div className="p-10"><BenchErrorState message="Bench Domain 加载失败" /></div>}
        {!domainsLoading && !domainsError && visibleDomains.length === 0 && <div className="p-12"><BenchEmptyState message="暂无符合条件的 Bench Domain" /></div>}
        {visibleDomains.length > 0 && <div className="grid gap-3 p-4 md:grid-cols-2 xl:grid-cols-3">
          {visibleDomains.map((domain) => <button key={`${domain.ownerUserId}:${domain.domainId}`} onClick={() => navigate(domainPath(domain, basePath))} className="group rounded-xl border border-gray-200 p-4 text-left transition hover:border-blue-200 hover:bg-blue-50/40 hover:shadow-sm">
            <div className="flex items-start justify-between gap-3"><p className="min-w-0 truncate text-sm font-semibold text-gray-900 group-hover:text-blue-700">{domain.name}</p><span className="shrink-0 rounded-full bg-gray-100 px-2 py-1 text-[10px] text-gray-500">{domain.templateCount} 个模板</span></div>
            <p className="mt-1 truncate font-mono text-[11px] text-gray-400">{domain.ownerUserId} / {domain.domainId}</p>
            <p className="mt-3 line-clamp-2 min-h-10 text-xs leading-5 text-gray-500">{domain.description || '暂无描述'}</p>
            <span className="mt-4 inline-flex text-xs font-medium text-blue-600">查看模板与运行 →</span>
          </button>)}
        </div>}
      </section>
    </div>
  }

  return (
    <div className="w-full px-3 py-6 sm:px-4 lg:px-5">
      <main className="min-w-0 overflow-hidden rounded-2xl border border-gray-200 bg-white shadow-sm">
        <div className="border-b border-gray-200 bg-white px-6 py-4">
          <button onClick={() => navigate(`${basePath}/domains`)} className="mb-4 text-sm text-gray-500 hover:text-blue-600">← 返回进化评估</button>
          <div className="flex items-start justify-between gap-4">
            <div className="min-w-0">
              <h1 className="text-xl font-semibold text-gray-900">
                {selectedDomain ? selectedDomain.name : benchText.workspace}
              </h1>
              <p className="mt-1 text-sm text-gray-500">
                {selectedDomain
                  ? `${selectedDomain.ownerUserId}/${selectedDomain.domainId}${selectedDomain.description ? ` · ${selectedDomain.description}` : ''}`
                  : '管理 Bench Domain、Bench Template 和 ClawBench 运行记录。'}
              </p>
              {domainSummary && selectedDomain && (
                <p className="mt-2 text-xs text-gray-400">
                  {domainSummary.templateCount} 个模板 · {domainSummary.runCount} 次运行
                  {domainSummary.latestPassRate !== null && ` · 最近通过率 ${(domainSummary.latestPassRate * 100).toFixed(1)}%`}
                </p>
              )}
            </div>
            <div className="flex shrink-0 items-center gap-2">
              <div className="flex rounded-md border border-gray-300">
                <button
                  onClick={() => setActiveTab('templates')}
                  className={`px-3 py-1.5 text-sm transition-colors ${activeTab === 'templates' ? 'bg-blue-600 text-white' : 'bg-white text-gray-600 hover:bg-gray-50'}`}
                >
                  {benchText.templates}
                </button>
                <button
                  onClick={() => setActiveTab('runs')}
                  className={`border-l border-gray-300 px-3 py-1.5 text-sm transition-colors ${activeTab === 'runs' ? 'bg-blue-600 text-white' : 'bg-white text-gray-600 hover:bg-gray-50'}`}
                >
                  {benchText.runs}
                </button>
              </div>
              {selectedDomain && canModifySelectedDomain && (
                <>
                  <button onClick={startDomainEdit} className="rounded-md border border-gray-300 px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-50">
                    {benchText.edit}
                  </button>
                  <button
                    onClick={() => {
                      if (showUpload) resetUpload()
                      else setShowUpload(true)
                    }}
                    className="rounded-md bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700"
                  >
                    {showUpload ? benchText.cancelUpload : benchText.uploadTemplates}
                  </button>
                  <button onClick={handleArchiveDomain} className="rounded-md border border-red-300 px-3 py-1.5 text-sm text-red-700 hover:bg-red-50">
                    归档 Bench Domain
                  </button>
                </>
              )}
            </div>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto px-6 py-4">
          {error && <div className="mb-4 rounded-md bg-red-50 p-3 text-sm text-red-700">{error}</div>}

          {showCreateDomain && (
            <section className="mb-4 rounded-lg border border-gray-200 bg-white p-4">
              <h3 className="text-sm font-semibold text-gray-900">创建 Bench Domain</h3>
              <div className="mt-3 grid grid-cols-2 gap-3">
                <label className="text-xs font-medium text-gray-700">
                  Bench Domain ID
                  <input className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm" value={createDomainForm.domainId} onChange={(e) => setCreateDomainForm({ ...createDomainForm, domainId: e.target.value })} placeholder="yuque_bench" />
                </label>
                <label className="text-xs font-medium text-gray-700">
                  名称
                  <input className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm" value={createDomainForm.name} onChange={(e) => setCreateDomainForm({ ...createDomainForm, name: e.target.value })} placeholder="Yuque Bench" />
                </label>
              </div>
              <label className="mt-3 block text-xs font-medium text-gray-700">
                描述
                <input className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm" value={createDomainForm.description} onChange={(e) => setCreateDomainForm({ ...createDomainForm, description: e.target.value })} />
              </label>
              <div className="mt-3 flex justify-end gap-2">
                <button onClick={() => setShowCreateDomain(false)} className="rounded-md border border-gray-300 px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-50">{benchText.cancel}</button>
                <button onClick={handleCreateDomain} disabled={createDomainMutation.isPending} className="rounded-md bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50">
                  {createDomainMutation.isPending ? '创建中...' : benchText.create}
                </button>
              </div>
            </section>
          )}

          {isEditingDomain && selectedDomain && (
            <section className="mb-4 rounded-lg border border-gray-200 bg-white p-4">
              <h3 className="text-sm font-semibold text-gray-900">编辑 Bench Domain</h3>
              <div className="mt-3 grid grid-cols-2 gap-3">
                <label className="text-xs font-medium text-gray-700">
                  名称
                  <input className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm" value={editDomainForm.name} onChange={(e) => setEditDomainForm({ ...editDomainForm, name: e.target.value })} />
                </label>
                <label className="text-xs font-medium text-gray-700">
                  Bench Domain ID
                  <input className="mt-1 block w-full rounded-md border border-gray-200 bg-gray-50 px-3 py-2 text-sm text-gray-500" value={`${selectedDomain.ownerUserId}/${selectedDomain.domainId}`} disabled />
                </label>
              </div>
              <label className="mt-3 block text-xs font-medium text-gray-700">
                描述
                <input className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm" value={editDomainForm.description} onChange={(e) => setEditDomainForm({ ...editDomainForm, description: e.target.value })} />
              </label>
              <div className="mt-3 flex justify-end gap-2">
                <button onClick={() => setIsEditingDomain(false)} className="rounded-md border border-gray-300 px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-50">{benchText.cancel}</button>
                <button onClick={handleSaveDomain} disabled={updateDomainMutation.isPending} className="rounded-md bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50">
                  {updateDomainMutation.isPending ? '保存中...' : benchText.save}
                </button>
              </div>
            </section>
          )}

          {showUpload && selectedDomain && (
            <section className="mb-4 rounded-lg border border-gray-200 bg-white p-4">
              <h3 className="text-sm font-semibold text-gray-900">{benchText.uploadTemplates}</h3>
              <p className="mt-1 text-xs text-gray-500">选择的 .md 文件或 .zip 压缩包会立即导入为草稿。README.md 和 TASK_TEMPLATE.md 会被排除。</p>
              <input
                ref={fileInputRef}
                type="file"
                multiple
                accept=".md,.zip"
                onChange={handleFileSelect}
                className="mt-3 block w-full text-sm text-gray-700 file:mr-4 file:rounded-md file:border-0 file:bg-blue-50 file:px-3 file:py-1.5 file:text-sm file:font-medium file:text-blue-700 hover:file:bg-blue-100"
              />

              {scanResult && scanSummary && (
                <div className="mt-3">
                  <div className="mb-2 text-xs text-gray-500">
                    已导入: {scanSummary.imported ?? 0} · 新增: {scanSummary.new} · 更新: {scanSummary.update} · 跳过: {scanSummary.skip} · 冲突: {scanSummary.conflict}
                  </div>
                  <div className="overflow-hidden rounded-md border border-gray-200">
                    <table className="min-w-full text-xs">
                      <thead className="bg-gray-50">
                        <tr>
                          <th className="px-3 py-2 text-left font-medium text-gray-700">动作</th>
                          <th className="px-3 py-2 text-left font-medium text-gray-700">已导入</th>
                          <th className="px-3 py-2 text-left font-medium text-gray-700">模板</th>
                          <th className="px-3 py-2 text-left font-medium text-gray-700">展示名称</th>
                          <th className="px-3 py-2 text-left font-medium text-gray-700">版本</th>
                          <th className="px-3 py-2 text-left font-medium text-gray-700">路径</th>
                          <th className="px-3 py-2 text-left font-medium text-gray-700">原因</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-gray-200">
                        {scanResult.map((item, idx) => (
                          <tr key={idx} className={item.action === 'conflict' ? 'bg-red-50' : item.action === 'skip' ? 'bg-gray-50' : ''}>
                            <td className="px-3 py-2">{statusBadge(item.action)}</td>
                            <td className="px-3 py-2">{item.imported ? '是' : '否'}</td>
                            <td className="px-3 py-2 font-mono">{item.templateName}</td>
                            <td className="px-3 py-2">{item.displayName}</td>
                            <td className="px-3 py-2">{item.currentVersion ?? '-'} {'->'} {item.nextVersion ?? '-'}</td>
                            <td className="px-3 py-2 text-gray-500">{item.entryPath}</td>
                            <td className="px-3 py-2 text-gray-500">{item.reason}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <div className="mt-3 flex justify-end gap-2">
                    <button onClick={handlePublishImported} disabled={batchPublishMutation.isPending || !scanResult.some((item) => item.imported)} className="rounded-md bg-green-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-green-700 disabled:opacity-50">{batchPublishMutation.isPending ? '发布中...' : benchText.publishImported}</button>
                    <button onClick={resetUpload} className="rounded-md border border-gray-300 px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-50">{benchText.close}</button>
                  </div>
                </div>
              )}
            </section>
          )}

          {activeTab === 'templates' && (
            <section className="overflow-hidden rounded-lg border border-gray-200 bg-white">
              {!isAll && (
                <div className="flex items-center justify-between border-b border-gray-200 px-4 py-3 text-sm">
                  <span className="text-gray-500">{benchText.selected} {selectedTemplates.size} 个草稿模板</span>
                  <button
                    onClick={handleBatchPublishSelected}
                    disabled={selectedTemplates.size === 0 || batchPublishMutation.isPending}
                    className="rounded-md bg-green-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-green-700 disabled:opacity-50"
                  >
                    {batchPublishMutation.isPending ? '发布中...' : benchText.batchPublish}
                  </button>
                </div>
              )}
              <table className="min-w-full text-sm">
                <thead className="bg-gray-50">
                  <tr>
                    {!isAll && <th className="px-4 py-2 text-left font-medium text-gray-700"><input type="checkbox" checked={filteredTemplates.length > 0 && selectedTemplates.size === filteredTemplates.filter((t) => t.status === 'draft').length} onChange={(e) => setSelectedTemplates(e.target.checked ? new Set(filteredTemplates.filter((t) => t.status === 'draft').map((t) => t.templateName)) : new Set())} /></th>}
                    {isAll && <th className="px-4 py-2 text-left font-medium text-gray-700">{benchText.domain}</th>}
                    <th className="px-4 py-2 text-left font-medium text-gray-700">模板名</th>
                    <th className="px-4 py-2 text-left font-medium text-gray-700">展示名称</th>
                    <th className="px-4 py-2 text-left font-medium text-gray-700">最新版本</th>
                    <th className="px-4 py-2 text-left font-medium text-gray-700">发布版本</th>
                    <th className="px-4 py-2 text-left font-medium text-gray-700">{benchText.status}</th>
                    <th className="px-4 py-2 text-right font-medium text-gray-700">{benchText.actions}</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-200">
                  {templatesLoading ? (
                    <tr><td colSpan={isAll ? 7 : 8} className="px-4 py-6"><BenchLoadingState message="正在加载任务模板..." /></td></tr>
                  ) : templatesError ? (
                    <tr><td colSpan={isAll ? 7 : 8} className="px-4 py-6"><BenchErrorState message="模板加载失败" /></td></tr>
                  ) : filteredTemplates.length === 0 ? (
                    <tr><td colSpan={isAll ? 7 : 8} className="px-4 py-6"><BenchEmptyState message={benchText.noTemplates} /></td></tr>
                  ) : filteredTemplates.map((t) => {
                    const ownerDomain = { ownerUserId: t.ownerUserId, domainId: t.domainId }
                    return (
                      <tr key={`${t.ownerUserId}-${t.domainId}-${t.templateName}`} className="cursor-pointer hover:bg-gray-50" onClick={() => navigate(templatePath(ownerDomain, t.templateName, basePath))}>
                        {!isAll && <td className="px-4 py-2" onClick={(e) => e.stopPropagation()}>{canModifySelectedDomain && <input type="checkbox" disabled={t.status !== 'draft'} checked={selectedTemplates.has(t.templateName)} onChange={(e) => setSelectedTemplates((prev) => { const next = new Set(prev); if (e.target.checked) next.add(t.templateName); else next.delete(t.templateName); return next })} />}</td>}
                        {isAll && <td className="px-4 py-2 text-xs text-gray-500">{t.ownerUserId}/{t.domainId}</td>}
                        <td className="px-4 py-2 font-mono text-xs text-gray-600">{t.templateName}</td>
                        <td className="px-4 py-2 font-medium text-gray-900">{t.displayName ?? t.templateName}</td>
                        <td className="px-4 py-2 text-gray-600">v{t.latestVersion}</td>
                        <td className="px-4 py-2 text-gray-600">{t.publishedVersion ? `v${t.publishedVersion}` : '-'}</td>
                        <td className="px-4 py-2">{statusBadge(t.status)}</td>
                        <td className="px-4 py-2 text-right">
                          <div className="flex items-center justify-end gap-2" onClick={(e) => e.stopPropagation()}>
                            {canModifySelectedDomain && <button onClick={() => navigate(`${templatePath(ownerDomain, t.templateName, basePath)}?edit=1`)} className="rounded-md border border-gray-300 px-2 py-1 text-xs text-gray-700 hover:bg-gray-50">{benchText.edit}</button>}
                            {canModifySelectedDomain && t.status === 'draft' && (
                              <button onClick={() => publishMutation.mutate({ ownerUserId: t.ownerUserId, domainId: t.domainId, templateName: t.templateName, version: t.latestVersion })} disabled={publishMutation.isPending} className="rounded-md border border-green-300 px-2 py-1 text-xs text-green-700 hover:bg-green-50 disabled:opacity-50">{benchText.publish}</button>
                            )}
                            {canModifySelectedDomain && !isAll && (
                              <button onClick={() => handleArchiveTemplate(t.templateName)} disabled={archiveTemplateMutation.isPending} className="rounded-md border border-red-300 px-2 py-1 text-xs text-red-700 hover:bg-red-50 disabled:opacity-50">{benchText.archive}</button>
                            )}
                          </div>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </section>
          )}

          {activeTab === 'runs' && (
            <section className="overflow-hidden rounded-lg border border-gray-200 bg-white">
              <div className="overflow-x-auto">
                <table className="min-w-full text-sm">
                  <thead className="bg-gray-50">
                    <tr>
                      <th className="px-4 py-2 text-left font-medium text-gray-700">{benchText.runId}</th>
                      {isAll && <th className="px-4 py-2 text-left font-medium text-gray-700">{benchText.domain}</th>}
                      <th className="px-4 py-2 text-left font-medium text-gray-700">范围</th>
                      <th className="px-4 py-2 text-left font-medium text-gray-700">{benchText.template}</th>
                      <th className="px-4 py-2 text-left font-medium text-gray-700">{benchText.version}</th>
                      <th className="px-4 py-2 text-left font-medium text-gray-700">{benchText.status}</th>
                      <th className="px-4 py-2 text-left font-medium text-gray-700">{benchText.score}</th>
                      <th className="px-4 py-2 text-left font-medium text-gray-700">{benchText.passRate}</th>
                      <th className="px-4 py-2 text-left font-medium text-gray-700">{benchText.token}</th>
                      <th className="px-4 py-2 text-left font-medium text-gray-700">{benchText.model}</th>
                      <th className="px-4 py-2 text-left font-medium text-gray-700">{benchText.suite}</th>
                      <th className="px-4 py-2 text-left font-medium text-gray-700">{benchText.scene}</th>
                      <th className="px-4 py-2 text-left font-medium text-gray-700">{benchText.started}</th>
                      <th className="px-4 py-2 text-left font-medium text-gray-700">{benchText.completed}</th>
                      <th className="px-4 py-2 text-left font-medium text-gray-700">{benchText.duration}</th>
                      <th className="px-4 py-2 text-left font-medium text-gray-700">ClawMind</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-200">
                    {runsLoading ? (
                      <tr><td colSpan={isAll ? 16 : 15} className="px-4 py-6"><BenchLoadingState message="正在加载 Bench Run..." /></td></tr>
                    ) : runsError ? (
                      <tr><td colSpan={isAll ? 16 : 15} className="px-4 py-6"><BenchErrorState message="运行记录加载失败" /></td></tr>
                    ) : runs.length === 0 ? (
                      <tr><td colSpan={isAll ? 16 : 15} className="px-4 py-6"><BenchEmptyState message={benchText.noRuns} /></td></tr>
                    ) : runs.map((run) => (
                      <tr key={run.benchRunId} className="cursor-pointer hover:bg-gray-50" onClick={() => navigate(`${basePath}/runs/${run.benchRunId}`)}>
                        <td className="px-4 py-2 font-mono text-xs text-gray-600">{run.benchRunId}</td>
                        {isAll && <td className="px-4 py-2 text-xs text-gray-500">{run.ownerUserId}/{run.domainId}</td>}
                        <td className="px-4 py-2"><span className={`inline-flex rounded-full px-1.5 py-0.5 text-xs font-medium ${run.runScope === 'domain' ? 'bg-purple-100 text-purple-700' : 'bg-gray-100 text-gray-700'}`}>{run.runScope ?? 'template'}</span></td>
                        <td className="px-4 py-2 text-gray-900">
                          {run.runScope === 'domain' || run.templateName === '__domain__' ? (
                            <span>{`Domain Run（${run.templateCount ?? '?'} 个模板）`}</span>
                          ) : (
                            <button
                              onClick={(e) => {
                                e.stopPropagation()
                                navigate(templatePath({ ownerUserId: run.ownerUserId, domainId: run.domainId }, run.templateName, basePath))
                              }}
                              className="font-mono text-xs text-blue-600 hover:underline"
                            >
                              {run.templateName}
                            </button>
                          )}
                        </td>
                        <td className="px-4 py-2 text-gray-600">{formatRunVersion(run)}</td>
                        <td className="px-4 py-2">{runStatusBadge(run.status)}</td>
                        <td className="px-4 py-2 text-gray-900">{formatScore(run.score, run.maxScore)}</td>
                        <td className="px-4 py-2 text-gray-600">{run.passRate !== null ? `${(run.passRate * 100).toFixed(1)}%` : '-'}</td>
                        <td className="px-4 py-2 text-gray-600">{formatTokenUsage(run.tokenUsage)}</td>
                        <td className="px-4 py-2 text-gray-600">{run.model ?? '-'}</td>
                        <td className="px-4 py-2 text-gray-600">{run.suite ?? '-'}</td>
                        <td className="px-4 py-2 text-gray-600">{run.scene ?? '-'}</td>
                        <td className="px-4 py-2 text-xs text-gray-500">{formatTime(run.startedAt)}</td>
                        <td className="px-4 py-2 text-xs text-gray-500">{formatTime(run.completedAt)}</td>
                        <td className="px-4 py-2 text-xs text-gray-500">{formatDuration(run)}</td>
                        <td className="px-4 py-2">
                          {run.clawmindFlowId ? (
                            <button
                              onClick={(e) => {
                                e.stopPropagation()
                                navigate(`/runs/${run.clawmindFlowId}`)
                              }}
                              className="text-xs text-blue-600 hover:underline"
                            >
                              Flow
                            </button>
                          ) : <span className="text-xs text-gray-400">-</span>}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          )}
        </div>
      </main>
    </div>
  )
}
