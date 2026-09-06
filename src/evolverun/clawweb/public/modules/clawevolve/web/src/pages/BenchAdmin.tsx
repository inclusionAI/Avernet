import { useMemo, useState, type ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  useAddBenchDomainTags,
  useBenchAdminDaily,
  useBenchAdminDomains,
  useBenchAdminSamples,
  useBenchAdminSummary,
  useBenchAdminTags,
  useCreateBenchAdminTag,
  useExportBenchTemplates,
  useRemoveBenchDomainTags,
  useUpdateBenchAdminTag,
} from '../api/hooks'
import { useClientUser } from '../hooks/useClientUser'
import type { BenchAdminDomain, BenchAdminFilters, BenchAdminSample } from '../types'

const DAY_MS = 24 * 60 * 60 * 1000
const DEFAULT_TAG_ID = '__default__'
const DEFAULT_TAG_NAME = 'default'
const inputClass = 'w-full rounded-md border border-gray-300 px-2.5 py-1.5 text-sm text-gray-900 shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500'

type ActiveTab = 'catalog' | 'runtime'
type CatalogSelection =
  | { type: 'all' }
  | { type: 'tag'; tagId: string }
  | { type: 'domain'; tagId: string; ownerUserId: string; domainId: string }

type CatalogDomain = {
  ownerUserId: string
  domainId: string
  count: number
  tags: BenchAdminDomain['tags']
}

type CatalogGroup = {
  tagId: string
  name: string
  count: number
  domains: CatalogDomain[]
}

function toDateInput(ms: number) {
  return new Date(ms).toISOString().slice(0, 10)
}

function dateInputToSeconds(value: string, endOfDay = false) {
  const date = new Date(`${value}T${endOfDay ? '23:59:59' : '00:00:00'}+08:00`)
  return Math.floor(date.getTime() / 1000)
}

function formatTime(seconds: number | null) {
  if (!seconds) return '-'
  return new Date(seconds * 1000).toLocaleString()
}

function formatPct(value: number | null) {
  if (value == null) return '-'
  return `${(value * 100).toFixed(1)}%`
}

function sampleKey(sample: BenchAdminSample) {
  return `${sample.ownerUserId}:${sample.domainId}:${sample.templateName}`
}

function domainKey(ownerUserId: string, domainId: string) {
  return `${ownerUserId}:${domainId}`
}

function compareText(a: string, b: string) {
  return a.localeCompare(b, 'zh-Hans-CN', { numeric: true, sensitivity: 'base' })
}

function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}

function buildCatalog(domains: BenchAdminDomain[]): CatalogGroup[] {
  const groupMap = new Map<string, { name: string; domains: Map<string, CatalogDomain> }>()

  for (const domain of domains) {
    const tags = domain.tags.length > 0 ? domain.tags : [{ tagId: DEFAULT_TAG_ID, name: DEFAULT_TAG_NAME, status: 'active' }]
    for (const tag of tags) {
      const group = groupMap.get(tag.tagId) ?? { name: tag.name, domains: new Map<string, CatalogDomain>() }
      const key = domainKey(domain.ownerUserId, domain.domainId)
      group.domains.set(key, {
        ownerUserId: domain.ownerUserId,
        domainId: domain.domainId,
        count: domain.templateCount,
        tags: domain.tags,
      })
      groupMap.set(tag.tagId, group)
    }
  }

  return Array.from(groupMap.entries())
    .map(([tagId, group]) => {
      const domains = Array.from(group.domains.values())
        .sort((a, b) => compareText(a.domainId, b.domainId) || compareText(a.ownerUserId, b.ownerUserId))
      return {
        tagId,
        name: group.name,
        count: domains.length,
        domains,
      }
    })
    .sort((a, b) => compareText(a.name, b.name))
}

export default function BenchAdmin() {
  const { user } = useClientUser()
  if (!user?.isAdmin && !user?.isBenchAdmin) {
    return (
      <div className="mx-auto max-w-3xl px-4 py-10">
        <section className="rounded-lg border border-gray-200 bg-white p-6 shadow-sm">
          <h1 className="text-lg font-semibold text-gray-900">无 Bench 管理权限</h1>
          <p className="mt-2 text-sm text-gray-600">当前账号不能访问 Bench 总览。</p>
        </section>
      </div>
    )
  }
  return <BenchAdminContent />
}

function BenchAdminContent() {
  const navigate = useNavigate()
  const [activeTab, setActiveTab] = useState<ActiveTab>('catalog')
  const [ownerUserId, setOwnerUserId] = useState('')
  const [domainId, setDomainId] = useState('')
  const [templateName, setTemplateName] = useState('')
  const [status, setStatus] = useState('all')
  const [fromDate, setFromDate] = useState(() => toDateInput(Date.now() - 14 * DAY_MS))
  const [toDate, setToDate] = useState(() => toDateInput(Date.now()))
  const [newTagId, setNewTagId] = useState('')
  const [newTagName, setNewTagName] = useState('')
  const [domainTagTarget, setDomainTagTarget] = useState<{ ownerUserId: string; domainId: string } | null>(null)
  const [domainTagId, setDomainTagId] = useState('')
  const [editingTagId, setEditingTagId] = useState<string | null>(null)
  const [editingTagName, setEditingTagName] = useState('')
  const [expanded, setExpanded] = useState<Record<string, boolean>>({})
  const [catalogSelection, setCatalogSelection] = useState<CatalogSelection>({ type: 'all' })

  const domainFilters: Pick<BenchAdminFilters, 'ownerUserId' | 'domainId'> = useMemo(() => ({
    ownerUserId: ownerUserId.trim() || undefined,
    domainId: domainId.trim() || undefined,
  }), [domainId, ownerUserId])

  const selectedDomain = catalogSelection.type === 'domain' ? catalogSelection : null
  const sampleFilters: BenchAdminFilters & { enabled?: boolean } = useMemo(() => selectedDomain ? ({
    ownerUserId: selectedDomain.ownerUserId,
    domainId: selectedDomain.domainId,
    templateName: templateName.trim() || undefined,
    limit: 200,
    offset: 0,
  }) : { enabled: false }, [selectedDomain, templateName])

  const runtimeFilters: BenchAdminFilters = useMemo(() => ({
    ownerUserId: ownerUserId.trim() || undefined,
    domainId: domainId.trim() || undefined,
    templateName: templateName.trim() || undefined,
    status: status === 'all' ? undefined : status,
    from: dateInputToSeconds(fromDate),
    to: dateInputToSeconds(toDate, true),
    limit: 200,
    offset: 0,
  }), [domainId, fromDate, ownerUserId, status, templateName, toDate])

  const summary = useBenchAdminSummary(runtimeFilters)
  const daily = useBenchAdminDaily(runtimeFilters)
  const domains = useBenchAdminDomains(domainFilters)
  const samples = useBenchAdminSamples(sampleFilters)
  const tags = useBenchAdminTags()
  const createTag = useCreateBenchAdminTag()
  const updateTag = useUpdateBenchAdminTag()
  const addTags = useAddBenchDomainTags()
  const removeTags = useRemoveBenchDomainTags()
  const exportTemplates = useExportBenchTemplates()

  const allDomains = domains.data?.domains ?? []
  const allSamples = samples.data?.samples ?? []
  const catalog = useMemo(() => buildCatalog(allDomains), [allDomains])

  function resetCatalogSelection() {
    setCatalogSelection({ type: 'all' })
  }

  function toggleGroup(tagId: string) {
    setExpanded((current) => ({ ...current, [tagId]: !(current[tagId] ?? true) }))
  }

  async function handleCreateTag() {
    if (!newTagId.trim() || !newTagName.trim()) return
    const tagId = newTagId.trim()
    const name = newTagName.trim()
    const existing = (tags.data ?? []).find((tag) => tag.tagId === tagId)
    if (existing) {
      await updateTag.mutateAsync({ tagId, input: { name } })
    } else {
      await createTag.mutateAsync({ tagId, name })
    }
    setNewTagId('')
    setNewTagName('')
  }

  async function handleRenameTag(tagId: string) {
    if (!editingTagName.trim()) return
    await updateTag.mutateAsync({
      tagId,
      input: { name: editingTagName.trim() },
    })
    setEditingTagId(null)
    setEditingTagName('')
  }

  async function handleArchiveTag(tagId: string) {
    await updateTag.mutateAsync({
      tagId,
      input: { status: 'archived' },
    })
    if (domainTagId === tagId) setDomainTagId('')
    if (catalogSelection.type !== 'all' && catalogSelection.tagId === tagId) {
      setCatalogSelection({ type: 'all' })
    }
  }

  async function handleApplyDomainTag() {
    if (!domainTagTarget || !domainTagId) return
    await addTags.mutateAsync({
      tagIds: [domainTagId],
      domains: [domainTagTarget],
    })
    setDomainTagTarget(null)
    setDomainTagId('')
  }

  async function handleRemoveDomainTag(domain: { ownerUserId: string; domainId: string }, tagId: string) {
    await removeTags.mutateAsync({
      tagIds: [tagId],
      domains: [domain],
    })
    if (catalogSelection.type !== 'all' && catalogSelection.tagId === tagId) {
      setCatalogSelection({ type: 'all' })
    }
  }

  async function handleExport(versionMode: 'published' | 'latest' | 'all_versions', domain?: { ownerUserId: string; domainId: string }) {
    const exportOwnerUserId = domain?.ownerUserId ?? ownerUserId.trim()
    const exportDomainId = domain?.domainId ?? domainId.trim()
    if (!exportOwnerUserId || !exportDomainId) return
    const blob = await exportTemplates.mutateAsync({
      ownerUserId: exportOwnerUserId,
      domainId: exportDomainId,
      versionMode,
      limit: versionMode === 'all_versions' ? 2000 : 500,
    })
    downloadBlob(blob, `clawbench-${exportOwnerUserId}-${exportDomainId}-templates-${versionMode}.zip`)
  }

  function openTemplate(sample: BenchAdminSample) {
    navigate(`/bench/domains/${encodeURIComponent(sample.ownerUserId)}/${encodeURIComponent(sample.domainId)}/templates/${encodeURIComponent(sample.templateName)}`)
  }

  function openDomainRuns(domain: { ownerUserId: string; domainId: string }) {
    navigate(`/bench/domains/${encodeURIComponent(domain.ownerUserId)}/${encodeURIComponent(domain.domainId)}?tab=runs`)
  }

  return (
    <div className="mx-auto max-w-screen-2xl px-4 py-4 sm:px-6 lg:px-8">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-gray-900">Bench 管理</h1>
          <p className="mt-1 text-sm text-gray-500">按样本目录管理 Bench，并单独查看运行态。</p>
        </div>
      </div>

      <div className="mb-4 flex border-b border-gray-200">
        <TabButton active={activeTab === 'catalog'} onClick={() => setActiveTab('catalog')}>样本目录</TabButton>
        <TabButton active={activeTab === 'runtime'} onClick={() => setActiveTab('runtime')}>运行看板</TabButton>
      </div>

      <section className="mb-4 rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
        <div className="grid gap-3 md:grid-cols-3 xl:grid-cols-6">
          <Field label="Owner"><input value={ownerUserId} onChange={(e) => { setOwnerUserId(e.target.value); resetCatalogSelection() }} className={inputClass} placeholder="工号" /></Field>
          <Field label="Domain"><input value={domainId} onChange={(e) => { setDomainId(e.target.value); resetCatalogSelection() }} className={inputClass} placeholder="domain" /></Field>
          <Field label="模板"><input value={templateName} onChange={(e) => setTemplateName(e.target.value)} className={inputClass} placeholder="选中 Domain 后过滤模板" /></Field>
          {activeTab === 'runtime' && (
            <>
              <Field label="运行状态">
                <select value={status} onChange={(e) => setStatus(e.target.value)} className={inputClass}>
                  <option value="all">全部</option>
                  <option value="pending">pending</option>
                  <option value="running">running</option>
                  <option value="succeeded">succeeded</option>
                  <option value="failed">failed</option>
                  <option value="cancelled">cancelled</option>
                </select>
              </Field>
              <Field label="开始"><input type="date" value={fromDate} onChange={(e) => setFromDate(e.target.value)} className={inputClass} /></Field>
              <Field label="结束"><input type="date" value={toDate} onChange={(e) => setToDate(e.target.value)} className={inputClass} /></Field>
            </>
          )}
        </div>
      </section>

      {activeTab === 'catalog' ? (
        <div className="grid gap-4 xl:grid-cols-[300px_minmax(0,1fr)_320px]">
          <CatalogTree
            catalog={catalog}
            expanded={expanded}
            selection={catalogSelection}
            total={allDomains.length}
            tags={tags.data ?? []}
            tagTarget={domainTagTarget}
            selectedTagId={domainTagId}
            applyPending={addTags.isPending || removeTags.isPending}
            onToggleGroup={toggleGroup}
            onSelect={setCatalogSelection}
            onOpenRuns={openDomainRuns}
            onExportDomain={(domain) => void handleExport('latest', domain)}
            onStartTag={(domain) => {
              setDomainTagTarget(domain)
              setDomainTagId('')
            }}
            onTagChange={setDomainTagId}
            onApplyTag={() => void handleApplyDomainTag()}
            onRemoveTag={(domain, tagId) => void handleRemoveDomainTag(domain, tagId)}
            onCancelTag={() => {
              setDomainTagTarget(null)
              setDomainTagId('')
            }}
          />
          <SampleList
            selectedDomain={selectedDomain}
            samples={allSamples}
            loading={samples.isLoading}
            error={samples.isError}
            onOpenTemplate={openTemplate}
          />
          <TagPanel
            tags={tags.data ?? []}
            newTagId={newTagId}
            newTagName={newTagName}
            editingTagId={editingTagId}
            editingTagName={editingTagName}
            createPending={createTag.isPending}
            updatePending={updateTag.isPending}
            createMode={newTagId.trim() && (tags.data ?? []).some((tag) => tag.tagId === newTagId.trim()) ? 'update' : 'create'}
            onTagIdChange={setNewTagId}
            onTagNameChange={setNewTagName}
            onEditingTagNameChange={setEditingTagName}
            onCreate={() => void handleCreateTag()}
            onStartEdit={(tag) => {
              setEditingTagId(tag.tagId)
              setEditingTagName(tag.name)
            }}
            onCancelEdit={() => {
              setEditingTagId(null)
              setEditingTagName('')
            }}
            onRename={(tagId) => void handleRenameTag(tagId)}
            onArchive={(tagId) => void handleArchiveTag(tagId)}
          />
        </div>
      ) : (
        <RuntimeBoard summary={summary.data} days={daily.data?.days ?? []} />
      )}
    </div>
  )
}

function CatalogTree({
  catalog,
  expanded,
  selection,
  total,
  tags,
  tagTarget,
  selectedTagId,
  applyPending,
  onToggleGroup,
  onSelect,
  onOpenRuns,
  onExportDomain,
  onStartTag,
  onTagChange,
  onApplyTag,
  onRemoveTag,
  onCancelTag,
}: {
  catalog: CatalogGroup[]
  expanded: Record<string, boolean>
  selection: CatalogSelection
  total: number
  tags: Array<{ tagId: string; name: string }>
  tagTarget: { ownerUserId: string; domainId: string } | null
  selectedTagId: string
  applyPending: boolean
  onToggleGroup: (tagId: string) => void
  onSelect: (selection: CatalogSelection) => void
  onOpenRuns: (domain: { ownerUserId: string; domainId: string }) => void
  onExportDomain: (domain: { ownerUserId: string; domainId: string }) => void
  onStartTag: (domain: { ownerUserId: string; domainId: string }) => void
  onTagChange: (tagId: string) => void
  onApplyTag: () => void
  onRemoveTag: (domain: { ownerUserId: string; domainId: string }, tagId: string) => void
  onCancelTag: () => void
}) {
  const allActive = selection.type === 'all'
  return (
    <section className="overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm">
      <div className="border-b border-gray-100 px-4 py-3">
        <div className="text-sm font-semibold text-gray-900">样本目录</div>
      </div>
      <div className="max-h-[680px] overflow-auto p-2 text-sm">
        <button
          onClick={() => onSelect({ type: 'all' })}
          className={`mb-1 flex w-full items-center justify-between rounded px-2 py-1.5 text-left ${allActive ? 'bg-blue-50 text-blue-700' : 'text-gray-700 hover:bg-gray-50'}`}
        >
          <span>全部 Domain</span>
          <span className="text-xs text-gray-400">{total}</span>
        </button>
        {catalog.map((group) => {
          const isExpanded = expanded[group.tagId] ?? true
          const tagActive = selection.type === 'tag' && selection.tagId === group.tagId
          return (
            <div key={group.tagId} className="mb-1">
              <div className="flex items-center gap-1">
                <button onClick={() => onToggleGroup(group.tagId)} className="h-7 w-7 rounded text-xs text-gray-500 hover:bg-gray-100">
                  {isExpanded ? '▾' : '▸'}
                </button>
                <button
                  onClick={() => onSelect({ type: 'tag', tagId: group.tagId })}
                  className={`flex min-w-0 flex-1 items-center justify-between rounded px-2 py-1.5 text-left ${tagActive ? 'bg-blue-50 text-blue-700' : 'text-gray-700 hover:bg-gray-50'}`}
                >
                  <span className="truncate">{group.name}</span>
                  <span className="ml-2 text-xs text-gray-400">{group.count}</span>
                </button>
              </div>
              {isExpanded && (
                <div className="ml-8 mt-1 space-y-1">
                  {group.domains.map((domain) => {
                    const active = selection.type === 'domain' && selection.ownerUserId === domain.ownerUserId && selection.domainId === domain.domainId && selection.tagId === group.tagId
                    const isTagging = tagTarget?.ownerUserId === domain.ownerUserId && tagTarget.domainId === domain.domainId
                    return (
                      <div key={domainKey(domain.ownerUserId, domain.domainId)} className="rounded">
                        <div className="flex items-center gap-1">
                          <button
                            onClick={() => onSelect({ type: 'domain', tagId: group.tagId, ownerUserId: domain.ownerUserId, domainId: domain.domainId })}
                            className={`flex min-w-0 flex-1 items-center justify-between rounded px-2 py-1.5 text-left text-xs ${active ? 'bg-blue-50 text-blue-700' : 'text-gray-600 hover:bg-gray-50'}`}
                          >
                            <span className="min-w-0">
                              <span className="block truncate">{domain.domainId}</span>
                              <span className="block truncate text-[11px] text-gray-400">{domain.ownerUserId || '-'}</span>
                            </span>
                            <span className="ml-2 text-gray-400">{domain.count}</span>
                          </button>
                          <button
                            onClick={() => onOpenRuns({ ownerUserId: domain.ownerUserId, domainId: domain.domainId })}
                            className="shrink-0 rounded border border-gray-200 px-1.5 py-1 text-[11px] text-gray-600 hover:bg-gray-50"
                          >
                            运行
                          </button>
                          <button
                            onClick={() => onStartTag({ ownerUserId: domain.ownerUserId, domainId: domain.domainId })}
                            className="shrink-0 rounded border border-gray-200 px-1.5 py-1 text-[11px] text-gray-600 hover:bg-gray-50"
                          >
                            标签
                          </button>
                          <button
                            onClick={() => onExportDomain({ ownerUserId: domain.ownerUserId, domainId: domain.domainId })}
                            className="shrink-0 rounded border border-gray-200 px-1.5 py-1 text-[11px] text-gray-600 hover:bg-gray-50"
                          >
                            导出
                          </button>
                        </div>
                        {isTagging && (
                          <div className="mt-1 rounded border border-blue-100 bg-blue-50 p-2">
                            <div className="mb-1 truncate text-[11px] text-blue-700">{domain.ownerUserId || '-'} / {domain.domainId}</div>
                            {domain.tags.length > 0 && (
                              <div className="mb-2 flex flex-wrap gap-1">
                                {domain.tags.map((tag) => (
                                  <span key={tag.tagId} className="inline-flex items-center gap-1 rounded bg-white px-1.5 py-0.5 text-[11px] text-gray-700 ring-1 ring-blue-100">
                                    {tag.name}
                                    <button
                                      onClick={() => onRemoveTag({ ownerUserId: domain.ownerUserId, domainId: domain.domainId }, tag.tagId)}
                                      disabled={applyPending}
                                      className="text-gray-400 hover:text-red-600 disabled:opacity-50"
                                      title="移除标签"
                                    >
                                      ×
                                    </button>
                                  </span>
                                ))}
                              </div>
                            )}
                            <select value={selectedTagId} onChange={(event) => onTagChange(event.target.value)} className="mb-2 w-full rounded border border-blue-200 bg-white px-2 py-1 text-xs">
                              <option value="">选择标签</option>
                              {tags.map((tag) => <option key={tag.tagId} value={tag.tagId}>{tag.name}</option>)}
                            </select>
                            <div className="flex gap-1">
                              <button onClick={onApplyTag} disabled={!selectedTagId || applyPending} className="rounded bg-blue-600 px-2 py-1 text-xs font-medium text-white disabled:opacity-50">应用</button>
                              <button onClick={onCancelTag} disabled={applyPending} className="rounded border border-blue-200 px-2 py-1 text-xs text-blue-700 disabled:opacity-50">取消</button>
                            </div>
                          </div>
                        )}
                      </div>
                    )
                  })}
                </div>
              )}
            </div>
          )
        })}
      </div>
    </section>
  )
}

function SampleList({
  selectedDomain,
  samples,
  loading,
  error,
  onOpenTemplate,
}: {
  selectedDomain: Extract<CatalogSelection, { type: 'domain' }> | null
  samples: BenchAdminSample[]
  loading: boolean
  error: boolean
  onOpenTemplate: (sample: BenchAdminSample) => void
}) {
  return (
    <section className="overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-gray-100 px-4 py-3">
          <div>
            <div className="text-sm font-semibold text-gray-900">模板</div>
            <div className="text-xs text-gray-500">
              {selectedDomain ? `${selectedDomain.ownerUserId || '-'} / ${selectedDomain.domainId}，共 ${samples.length} 个模板。点击模板查看详情。` : '先在左侧选择一个 Domain。'}
            </div>
          </div>
        </div>
        <div className="overflow-auto">
          <table className="min-w-full divide-y divide-gray-100 text-sm">
            <thead className="bg-gray-50 text-xs font-medium text-gray-500">
              <tr>
                <th className="px-3 py-2 text-left">模板</th>
                <th className="px-3 py-2 text-left">标签</th>
                <th className="px-3 py-2 text-left">最新状态</th>
                <th className="px-3 py-2 text-left">最近运行</th>
                <th className="px-3 py-2 text-left">Run</th>
                <th className="px-3 py-2 text-left">通过率</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {!selectedDomain ? (
                <tr><td colSpan={6} className="px-3 py-10 text-center text-sm text-gray-400">请选择左侧 Domain 后查看模板</td></tr>
              ) : loading ? (
                <tr><td colSpan={6} className="px-3 py-10 text-center text-sm text-gray-400">正在加载模板...</td></tr>
              ) : error ? (
                <tr><td colSpan={6} className="px-3 py-10 text-center text-sm text-red-500">模板加载失败</td></tr>
              ) : samples.length === 0 ? (
                <tr><td colSpan={6} className="px-3 py-10 text-center text-sm text-gray-400">当前 Domain 下没有模板</td></tr>
              ) : samples.map((sample) => {
                const key = sampleKey(sample)
                return (
                  <tr key={key} className="cursor-pointer hover:bg-gray-50" onClick={() => onOpenTemplate(sample)}>
                    <td className="px-3 py-2">
                      <div className="font-medium text-blue-600 hover:underline">{sample.templateName}</div>
                      <div className="text-xs text-gray-500">{sample.ownerUserId || '-'} / {sample.domainId}</div>
                    </td>
                    <td className="px-3 py-2">
                      <div className="flex flex-wrap gap-1">
                        {sample.tags.length === 0 ? <span className="rounded bg-gray-100 px-1.5 py-0.5 text-xs text-gray-500">default</span> : sample.tags.map((tag) => (
                          <span key={tag.tagId} className="rounded bg-gray-100 px-1.5 py-0.5 text-xs text-gray-700">{tag.name}</span>
                        ))}
                      </div>
                    </td>
                    <td className="px-3 py-2 text-gray-700">{sample.latestStatus ?? '-'}</td>
                    <td className="px-3 py-2 text-gray-500">{formatTime(sample.latestRunAt)}</td>
                    <td className="px-3 py-2 text-gray-700">{sample.runCount}</td>
                    <td className="px-3 py-2 text-gray-700">{formatPct(sample.avgPassRate)}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
    </section>
  )
}

function TagPanel({
  tags,
  newTagId,
  newTagName,
  editingTagId,
  editingTagName,
  createPending,
  updatePending,
  createMode,
  onTagIdChange,
  onTagNameChange,
  onEditingTagNameChange,
  onCreate,
  onStartEdit,
  onCancelEdit,
  onRename,
  onArchive,
}: {
  tags: Array<{ tagId: string; name: string }>
  newTagId: string
  newTagName: string
  editingTagId: string | null
  editingTagName: string
  createPending: boolean
  updatePending: boolean
  createMode: 'create' | 'update'
  onTagIdChange: (value: string) => void
  onTagNameChange: (value: string) => void
  onEditingTagNameChange: (value: string) => void
  onCreate: () => void
  onStartEdit: (tag: { tagId: string; name: string }) => void
  onCancelEdit: () => void
  onRename: (tagId: string) => void
  onArchive: (tagId: string) => void
}) {
  return (
    <section className="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
      <div className="mb-3 text-sm font-semibold text-gray-900">标签管理</div>
      <div className="max-h-80 space-y-2 overflow-auto">
        {tags.map((tag) => (
          <div key={tag.tagId} className="rounded border border-gray-100 px-2 py-1.5 text-sm">
            {editingTagId === tag.tagId ? (
              <div className="space-y-2">
                <input value={editingTagName} onChange={(e) => onEditingTagNameChange(e.target.value)} className={inputClass} />
                <div className="flex gap-2">
                  <button onClick={() => onRename(tag.tagId)} disabled={!editingTagName.trim() || updatePending} className="rounded bg-blue-600 px-2 py-1 text-xs font-medium text-white disabled:opacity-50">保存</button>
                  <button onClick={onCancelEdit} disabled={updatePending} className="rounded border border-gray-200 px-2 py-1 text-xs text-gray-600 disabled:opacity-50">取消</button>
                </div>
              </div>
            ) : (
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="truncate font-medium text-gray-800">{tag.name}</div>
                  <div className="truncate text-xs text-gray-500">{tag.tagId}</div>
                </div>
                <div className="flex shrink-0 gap-1">
                  <button onClick={() => onStartEdit(tag)} className="rounded border border-gray-200 px-1.5 py-0.5 text-xs text-gray-600 hover:bg-gray-50">改名</button>
                  <button onClick={() => onArchive(tag.tagId)} disabled={updatePending} className="rounded border border-red-200 px-1.5 py-0.5 text-xs text-red-600 hover:bg-red-50 disabled:opacity-50">删除</button>
                </div>
              </div>
            )}
          </div>
        ))}
      </div>
      <div className="mt-3 grid gap-2">
        <input value={newTagId} onChange={(e) => onTagIdChange(e.target.value)} className={inputClass} placeholder="tag_id" />
        <input value={newTagName} onChange={(e) => onTagNameChange(e.target.value)} className={inputClass} placeholder="标签名" />
        <button onClick={onCreate} disabled={!newTagId.trim() || !newTagName.trim() || createPending || updatePending} className="rounded-md bg-gray-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-black disabled:opacity-50">
          {createMode === 'update' ? '更新标签' : '新建标签'}
        </button>
      </div>
    </section>
  )
}

function RuntimeBoard({
  summary,
  days,
}: {
  summary: {
    totalRunCount: number
    succeededCount: number
    failedCount: number
    runningCount: number
    avgPassRate: number | null
    avgScore: number | null
    ownerCount: number
    domainCount: number
    templateCount: number
  } | undefined
  days: Array<{ date: string; runCount: number }>
}) {
  return (
    <div className="space-y-4">
      <div className="grid gap-3 md:grid-cols-4 xl:grid-cols-9">
        <Metric label="总 Run" value={summary?.totalRunCount ?? '-'} />
        <Metric label="成功" value={summary?.succeededCount ?? '-'} />
        <Metric label="失败" value={summary?.failedCount ?? '-'} />
        <Metric label="运行中" value={summary?.runningCount ?? '-'} />
        <Metric label="平均通过率" value={formatPct(summary?.avgPassRate ?? null)} />
        <Metric label="平均分" value={summary?.avgScore?.toFixed(2) ?? '-'} />
        <Metric label="Owner" value={summary?.ownerCount ?? '-'} />
        <Metric label="Domain" value={summary?.domainCount ?? '-'} />
        <Metric label="样本" value={summary?.templateCount ?? '-'} />
      </div>
      <section className="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
        <div className="mb-3 text-sm font-semibold text-gray-900">每日运行数量</div>
        <div className="space-y-2">
          {days.map((day) => (
            <div key={day.date} className="grid grid-cols-[110px_minmax(0,1fr)_56px] items-center gap-3 text-sm">
              <span className="text-gray-500">{day.date}</span>
              <div className="h-3 overflow-hidden rounded bg-gray-100">
                <div className="h-full bg-blue-500" style={{ width: `${Math.min(100, day.runCount * 4)}%` }} />
              </div>
              <span className="text-right text-gray-700">{day.runCount}</span>
            </div>
          ))}
          {days.length === 0 && <div className="py-8 text-center text-sm text-gray-400">当前筛选范围内没有运行记录</div>}
        </div>
      </section>
    </div>
  )
}

function TabButton({ active, onClick, children }: { active: boolean; onClick: () => void; children: ReactNode }) {
  return (
    <button
      onClick={onClick}
      className={`border-b-2 px-4 py-2 text-sm font-medium ${active ? 'border-blue-600 text-blue-600' : 'border-transparent text-gray-500 hover:text-gray-700'}`}
    >
      {children}
    </button>
  )
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs font-medium text-gray-600">{label}</span>
      {children}
    </label>
  )
}

function Metric({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded-lg border border-gray-200 bg-white px-3 py-2 shadow-sm">
      <div className="text-xs text-gray-500">{label}</div>
      <div className="mt-1 text-lg font-semibold text-gray-900">{value}</div>
    </div>
  )
}
