import { useEffect, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { api, type EvolveSkillAsset } from '../api/client'

import SkillTaskLaunchDialog, { type SkillTaskAction } from '../components/SkillTaskLaunchDialog'
import { createUnifiedDiff, GitDiffView } from './evolve/common'

type Content = Awaited<ReturnType<typeof api.evolve.getSkillVersionContent>>
type Diff = Awaited<ReturnType<typeof api.evolve.getSkillVersionDiff>>

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

export default function SkillDetail() {
  const navigate = useNavigate()
  const location = useLocation()
  const assetId = decodeURIComponent(location.pathname.split('/').filter(Boolean).at(-1) ?? '')
  const [launchAction, setLaunchAction] = useState<SkillTaskAction | null>(null)
  const [asset, setAsset] = useState<EvolveSkillAsset | null>(null)
  const [versionId, setVersionId] = useState('')
  const [path, setPath] = useState('')
  const [content, setContent] = useState<Content | null>(null)
  const [diff, setDiff] = useState<Diff | null>(null)
  const [tab, setTab] = useState<'content' | 'diff'>('content')
  const [error, setError] = useState('')

  useEffect(() => {
    void api.evolve.getSkillAsset(assetId).then((value) => {
      setAsset(value)
      setVersionId(value.versions?.[0]?.versionId ?? '')
    }).catch((reason) => setError(reason instanceof Error ? reason.message : 'Skill 加载失败'))
  }, [assetId])

  useEffect(() => {
    if (!versionId) return
    void Promise.all([
      api.evolve.getSkillVersionContent(assetId, versionId),
      api.evolve.getSkillVersionDiff(assetId, versionId),
    ]).then(([nextContent, nextDiff]) => {
      setContent(nextContent); setDiff(nextDiff); setPath(nextContent.selected?.path ?? nextContent.files[0]?.path ?? '')
    }).catch((reason) => setError(reason instanceof Error ? reason.message : '版本内容加载失败'))
  }, [assetId, versionId])

  useEffect(() => {
    if (!versionId || !path) return
    void api.evolve.getSkillVersionContent(assetId, versionId, path).then(setContent)
      .catch((reason) => setError(reason instanceof Error ? reason.message : '文件加载失败'))
  }, [assetId, versionId, path])

  const version = asset?.versions?.find((item) => item.versionId === versionId)
  return <div className="mx-auto max-w-6xl px-4 py-7 sm:px-6 lg:px-8">
    <button className="mb-5 text-sm text-gray-500" onClick={() => navigate('/evolve/skills')}>← 返回技能中心</button>
    <div className="flex flex-wrap items-start justify-between gap-4"><div><p className="text-sm font-medium text-blue-600">Skill 详情</p><h1 className="mt-1 text-2xl font-semibold text-gray-950">{asset?.name ?? '加载中…'}</h1>{asset && <p className="mt-1 font-mono text-xs text-gray-400">{asset.botId} / {asset.skillId}</p>}</div>{asset && <div className="flex gap-2"><button onClick={() => setLaunchAction('diagnose')} className="rounded-lg border border-blue-200 px-4 py-2.5 text-sm font-medium text-blue-600">诊断</button><button onClick={() => setLaunchAction('optimize')} className="rounded-lg bg-blue-600 px-4 py-2.5 text-sm font-medium text-white">优化</button></div>}</div>
    {asset && <section className="mt-5 rounded-2xl border border-gray-200 bg-white shadow-sm"><div className="flex flex-wrap items-center gap-4 border-b border-gray-100 px-5 py-4"><label className="text-xs font-medium text-gray-500">版本 <select value={versionId} onChange={(event) => setVersionId(event.target.value)} className="ml-2 rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm text-gray-800">{asset.versions?.map((item) => <option key={item.versionId} value={item.versionId}>{item.version}</option>)}</select></label>{version?.sourceTaskId && <button className="text-xs font-medium text-blue-600" onClick={() => navigate(`/evolve/runs/${version.sourceTaskId}`)}>查看来源任务 ↗</button>}</div><div className="flex border-b border-gray-100 px-5"><button onClick={() => setTab('content')} className={`border-b-2 px-1 py-3 text-sm font-medium ${tab === 'content' ? 'border-blue-600 text-blue-600' : 'border-transparent text-gray-500'}`}>版本内容</button><button onClick={() => setTab('diff')} className={`ml-6 border-b-2 px-1 py-3 text-sm font-medium ${tab === 'diff' ? 'border-blue-600 text-blue-600' : 'border-transparent text-gray-500'}`}>与本次进化前对比</button></div>{tab === 'content' ? <div className="grid min-h-[440px] md:grid-cols-[260px_minmax(0,1fr)]"><aside className="border-r border-gray-100 p-3">{content?.files.map((file) => <button key={file.path} disabled={!file.text} onClick={() => setPath(file.path)} className={`block w-full truncate rounded-lg px-3 py-2 text-left font-mono text-xs ${path === file.path ? 'bg-blue-50 text-blue-700' : 'text-gray-600 hover:bg-gray-50'} disabled:text-gray-300`}>{file.path}</button>)}</aside><pre className="min-w-0 overflow-auto bg-[#0b1020] p-5 text-xs leading-6 text-gray-100">{content?.selected?.content ?? '请选择可预览的文本文件'}</pre></div> : <VersionDiff key={versionId} diff={diff} />}</section>}
    {asset && launchAction && <SkillTaskLaunchDialog key={`${asset.assetId}:${launchAction}`} asset={asset} action={launchAction} onClose={() => setLaunchAction(null)} />}
    {error && <p className="mt-4 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>}
  </div>
}
