import { useParams, Link, useNavigate, useSearchParams } from 'react-router-dom'
import { useArchiveBenchTemplate, useBenchTemplate, useBenchRunsByTemplate, usePublishBenchTemplate, useUpdateBenchTemplate } from '../api/hooks'
import { useState, useMemo, useEffect } from 'react'
import { benchStatusLabel, benchText } from '../bench/i18n'
import { BenchEmptyState, BenchErrorState, BenchLoadingState } from '../bench/ui-state'
import { formatTokenUsage } from '../bench/token'

function formatBenchTime(value: number | string | null | undefined): string {
  if (value == null || value === '') return '-'
  const date = typeof value === 'number'
    ? new Date(value > 1_000_000_000_000 ? value : value * 1000)
    : new Date(value)
  return Number.isNaN(date.getTime()) ? '-' : date.toLocaleString()
}

export default function BenchTemplateDetail({ basePath = '/bench' }: { basePath?: string }) {
  const { ownerUserId, domainId, templateName: encodedTemplateName } = useParams<{ ownerUserId: string; domainId: string; templateName: string }>()
  const templateName = decodeURIComponent(encodedTemplateName ?? '')
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const { data: template, isLoading, isError } = useBenchTemplate(ownerUserId ?? '', domainId ?? '', templateName)
  const { data: runsData, isLoading: runsLoading, isError: runsError } = useBenchRunsByTemplate(ownerUserId ?? '', domainId ?? '', templateName)
  const publishMutation = usePublishBenchTemplate()
  const updateMutation = useUpdateBenchTemplate(ownerUserId ?? '', domainId ?? '', templateName)
  const archiveMutation = useArchiveBenchTemplate()
  const [error, setError] = useState<string | null>(null)
  const [isEditing, setIsEditing] = useState(false)
  const [editForm, setEditForm] = useState({
    displayName: '',
    description: '',
    category: '',
    gradingType: 'automated',
    contentMd: '',
  })

  const runs = runsData ?? []

  const draftContentMd = useMemo(() => {
    if (!template) return ''
    const draft = template.versions.find((v) => v.status === 'draft')
    return draft?.contentMd ?? template.versions[0]?.contentMd ?? ''
  }, [template])

  useEffect(() => {
    if (template && searchParams.get('edit') === '1' && !isEditing) {
      setEditForm({
        displayName: template.displayName ?? template.templateName,
        description: template.description ?? '',
        category: template.category ?? '',
        gradingType: template.gradingType,
        contentMd: draftContentMd,
      })
      setError(null)
      setIsEditing(true)
    }
  }, [template, draftContentMd, searchParams, isEditing])

  const startEdit = () => {
    if (!template) return
    setEditForm({
      displayName: template.displayName ?? template.templateName,
      description: template.description ?? '',
      category: template.category ?? '',
      gradingType: template.gradingType,
      contentMd: draftContentMd,
    })
    setError(null)
    setIsEditing(true)
  }

  const cancelEdit = () => {
    setIsEditing(false)
    setError(null)
  }

  const handleSave = async () => {
    if (!template) return
    if (!editForm.contentMd.trim()) {
      setError('Markdown 内容不能为空')
      return
    }
    try {
      setError(null)
      await updateMutation.mutateAsync({
        displayName: editForm.displayName || null,
        description: editForm.description || null,
        category: editForm.category || null,
        gradingType: editForm.gradingType,
        contentMd: editForm.contentMd,
      })
      setIsEditing(false)
    } catch (err) {
      setError(err instanceof Error ? err.message : '保存模板失败')
    }
  }

  const handlePublish = async () => {
    try {
      setError(null)
      await publishMutation.mutateAsync({ ownerUserId: ownerUserId ?? '', domainId: domainId ?? '', templateName })
    } catch (err) {
      setError(err instanceof Error ? err.message : '发布失败')
    }
  }

  const handleArchive = async () => {
    if (!template) return
    if (!window.confirm(`确认归档模板 "${template.templateName}"？后续 Bench Domain 运行不会包含它。`)) return
    try {
      setError(null)
      await archiveMutation.mutateAsync({
        ownerUserId: template.ownerUserId,
        domainId: template.domainId,
        templateName: template.templateName,
      })
      navigate(`${basePath}/domains/${encodeURIComponent(template.ownerUserId)}/${encodeURIComponent(template.domainId)}`)
    } catch (err) {
      setError(err instanceof Error ? err.message : '归档模板失败')
    }
  }

  if (isLoading) return <div className="p-6"><BenchLoadingState message="正在加载任务模板..." /></div>
  if (isError) return <div className="p-6"><BenchErrorState message="模板加载失败" /></div>
  if (!template) return <div className="p-6"><BenchErrorState message="模板不存在" /></div>

  const parsedMeta = template.parsedMeta ?? {}
  const prompt = parsedMeta.prompt as string | undefined
  const expectedBehavior = parsedMeta.expectedBehavior as string | undefined
  const gradingCriteria = parsedMeta.gradingCriteria as string | undefined

  return (
    <div className="flex h-[calc(100vh-49px)] flex-col">
      <div className="border-b border-gray-200 bg-white px-6 py-4">
        <div className="flex items-center gap-2 text-sm text-gray-500 mb-2">
          <Link to={`${basePath}/domains`} className="hover:text-blue-600">← {benchText.domains}</Link>
          <span>/</span>
          <Link to={`${basePath}/domains/${encodeURIComponent(template.ownerUserId)}/${encodeURIComponent(template.domainId)}`} className="hover:text-blue-600">{template.ownerUserId}/{template.domainId}</Link>
          <span>/</span>
          <span className="text-gray-900 font-medium">{template.templateName}</span>
        </div>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <h1 className="text-xl font-semibold text-gray-900">{template.displayName ?? template.templateName}</h1>
            <span className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${template.status === 'published' ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-700'}`}>{benchStatusLabel(template.status)}</span>
          </div>
          <div className="flex items-center gap-2">
            {!isEditing && (
              <button onClick={startEdit} className="rounded-md border border-gray-300 px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50">
                {benchText.edit}
              </button>
            )}
            {template.status === 'draft' && !isEditing && (
              <button onClick={handlePublish} disabled={publishMutation.isPending} className="rounded-md bg-green-600 px-4 py-2 text-sm font-medium text-white hover:bg-green-700 disabled:opacity-50">
                {publishMutation.isPending ? '发布中...' : benchText.publish}
              </button>
            )}
            {!isEditing && (
              <button onClick={handleArchive} disabled={archiveMutation.isPending} className="rounded-md border border-red-300 px-4 py-2 text-sm font-medium text-red-700 hover:bg-red-50 disabled:opacity-50">
                {archiveMutation.isPending ? '归档中...' : benchText.archive}
              </button>
            )}
          </div>
        </div>
        {error && <div className="mt-2 rounded-md bg-red-50 p-2 text-sm text-red-700">{error}</div>}
      </div>

      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto max-w-5xl px-6 py-4 space-y-6">
          {isEditing ? (
            <section className="rounded-lg border border-gray-200 bg-white p-4 space-y-4">
              <h2 className="text-sm font-semibold text-gray-900">编辑模板</h2>
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-xs font-medium text-gray-700 mb-1">展示名称</label>
                  <input
                    type="text"
                    className="block w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:ring-1 focus:ring-blue-500"
                    value={editForm.displayName}
                    onChange={(e) => setEditForm({ ...editForm, displayName: e.target.value })}
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-gray-700 mb-1">评分方式</label>
                  <select
                    className="block w-full rounded-md border border-gray-300 px-3 py-2 text-sm"
                    value={editForm.gradingType}
                    onChange={(e) => setEditForm({ ...editForm, gradingType: e.target.value })}
                  >
                    <option value="automated">automated</option>
                    <option value="llm_judge">llm_judge</option>
                    <option value="hybrid">hybrid</option>
                  </select>
                </div>
              </div>
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-xs font-medium text-gray-700 mb-1">分类</label>
                  <input
                    type="text"
                    className="block w-full rounded-md border border-gray-300 px-3 py-2 text-sm"
                    value={editForm.category}
                    onChange={(e) => setEditForm({ ...editForm, category: e.target.value })}
                    placeholder="mcp"
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-gray-700 mb-1">描述</label>
                  <input
                    type="text"
                    className="block w-full rounded-md border border-gray-300 px-3 py-2 text-sm"
                    value={editForm.description}
                    onChange={(e) => setEditForm({ ...editForm, description: e.target.value })}
                  />
                </div>
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-700 mb-1">Markdown 内容 <span className="text-red-500">*</span></label>
                <textarea
                  className="block w-full rounded-md border border-gray-300 px-3 py-2 text-sm font-mono focus:border-blue-500 focus:ring-1 focus:ring-blue-500"
                  rows={16}
                  value={editForm.contentMd}
                  onChange={(e) => setEditForm({ ...editForm, contentMd: e.target.value })}
                />
              </div>
              <div className="flex justify-end gap-2">
                <button onClick={cancelEdit} className="rounded-md border border-gray-300 px-4 py-2 text-sm text-gray-700 hover:bg-gray-50">{benchText.cancel}</button>
                <button onClick={handleSave} disabled={updateMutation.isPending} className="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50">
                  {updateMutation.isPending ? '保存中...' : benchText.saveDraft}
                </button>
              </div>
            </section>
          ) : (
            <>
              <section className="rounded-lg border border-gray-200 bg-white p-4">
                <h2 className="text-sm font-semibold text-gray-900 mb-3">基础信息</h2>
                <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
                  <div><span className="text-gray-500">{benchText.domain}</span><div className="text-gray-900">{template.domainId}</div></div>
                  <div><span className="text-gray-500">模板名</span><div className="font-mono text-gray-900">{template.templateName}</div></div>
                  <div><span className="text-gray-500">最新版本</span><div className="text-gray-900">v{template.latestVersion}</div></div>
                  <div><span className="text-gray-500">发布版本</span><div className="text-gray-900">{template.publishedVersion ? `v${template.publishedVersion}` : '-'}</div></div>
                  <div><span className="text-gray-500">评分方式</span><div className="text-gray-900">{template.gradingType}</div></div>
                  <div><span className="text-gray-500">分类</span><div className="text-gray-900">{template.category ?? '-'}</div></div>
                  <div><span className="text-gray-500">来源路径</span><div className="text-gray-900 truncate">{template.sourcePath ?? '-'}</div></div>
                  <div><span className="text-gray-500">来源 Hash</span><div className="font-mono text-xs text-gray-600">{template.sourceHash ?? '-'}</div></div>
                </div>
              </section>

              {(prompt || expectedBehavior || gradingCriteria) && (
                <section className="rounded-lg border border-gray-200 bg-white p-4 space-y-3">
                  <h2 className="text-sm font-semibold text-gray-900">解析内容</h2>
                  {prompt && <div><h3 className="text-xs font-medium text-gray-500 uppercase mb-1">Prompt</h3><div className="rounded-md bg-gray-50 p-3 text-sm text-gray-800 whitespace-pre-wrap">{prompt}</div></div>}
                  {expectedBehavior && <div><h3 className="text-xs font-medium text-gray-500 uppercase mb-1">预期行为</h3><div className="rounded-md bg-gray-50 p-3 text-sm text-gray-800 whitespace-pre-wrap">{expectedBehavior}</div></div>}
                  {gradingCriteria && <div><h3 className="text-xs font-medium text-gray-500 uppercase mb-1">评分标准</h3><div className="rounded-md bg-gray-50 p-3 text-sm text-gray-800 whitespace-pre-wrap">{gradingCriteria}</div></div>}
                </section>
              )}

              {draftContentMd && (
                <section className="rounded-lg border border-gray-200 bg-white p-4">
                  <h2 className="text-sm font-semibold text-gray-900 mb-3">Markdown 源码</h2>
                  <pre className="rounded-md bg-gray-50 p-3 text-xs text-gray-800 overflow-auto max-h-96 whitespace-pre-wrap">{draftContentMd}</pre>
                </section>
              )}

              <section className="rounded-lg border border-gray-200 bg-white p-4">
                <h2 className="text-sm font-semibold text-gray-900 mb-3">版本历史</h2>
                {template.versions?.length === 0 ? <BenchEmptyState message="暂无版本" /> : (
                  <table className="min-w-full text-sm">
                    <thead className="bg-gray-50"><tr><th className="px-3 py-2 text-left font-medium text-gray-700">{benchText.version}</th><th className="px-3 py-2 text-left font-medium text-gray-700">{benchText.status}</th><th className="px-3 py-2 text-left font-medium text-gray-700">创建时间</th></tr></thead>
                    <tbody className="divide-y divide-gray-200">
                      {template.versions.map((v) => (
                        <tr key={v.version} className={v.version === template.latestVersion ? 'bg-blue-50' : ''}>
                          <td className="px-3 py-2">v{v.version}</td>
                          <td className="px-3 py-2"><span className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${v.status === 'published' ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-700'}`}>{benchStatusLabel(v.status)}</span></td>
                          <td className="px-3 py-2 text-gray-500 text-xs">{formatBenchTime(v.gmtCreate)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </section>

              <section className="rounded-lg border border-gray-200 bg-white p-4">
                <h2 className="text-sm font-semibold text-gray-900 mb-3">最近运行</h2>
                {runsLoading ? <BenchLoadingState message="正在加载 Bench Run..." /> : runsError ? <BenchErrorState message="运行记录加载失败" /> : runs.length === 0 ? <BenchEmptyState message={benchText.noRuns} /> : (
                  <table className="min-w-full text-sm">
                    <thead className="bg-gray-50"><tr><th className="px-3 py-2 text-left font-medium text-gray-700">{benchText.runId}</th><th className="px-3 py-2 text-left font-medium text-gray-700">{benchText.status}</th><th className="px-3 py-2 text-left font-medium text-gray-700">{benchText.score}</th><th className="px-3 py-2 text-left font-medium text-gray-700">{benchText.token}</th><th className="px-3 py-2 text-left font-medium text-gray-700">{benchText.model}</th><th className="px-3 py-2 text-left font-medium text-gray-700">创建时间</th></tr></thead>
                    <tbody className="divide-y divide-gray-200">
                      {runs.slice(0, 10).map((r) => (
                        <tr key={r.benchRunId} className="hover:bg-gray-50 cursor-pointer" onClick={() => navigate(`${basePath}/runs/${r.benchRunId}`)}>
                          <td className="px-3 py-2 font-mono text-xs">{r.benchRunId}</td>
                          <td className="px-3 py-2"><span className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${r.status === 'succeeded' ? 'bg-green-100 text-green-700' : r.status === 'failed' ? 'bg-red-100 text-red-700' : 'bg-gray-100 text-gray-700'}`}>{benchStatusLabel(r.status)}</span></td>
                          <td className="px-3 py-2">{r.score !== null && r.maxScore ? `${((r.score / r.maxScore) * 100).toFixed(1)}%` : '-'}</td>
                          <td className="px-3 py-2 text-gray-600">{formatTokenUsage(r.tokenUsage)}</td>
                          <td className="px-3 py-2 text-gray-600">{r.model ?? '-'}</td>
                          <td className="px-3 py-2 text-gray-500 text-xs">{formatBenchTime(r.gmtCreate)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </section>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
