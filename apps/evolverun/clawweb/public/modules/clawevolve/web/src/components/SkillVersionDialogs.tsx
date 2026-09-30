import { useEffect, useState } from 'react'
import { api, type EvolveSkillAsset } from '../api/client'

type Version = NonNullable<EvolveSkillAsset['versions']>[number]
type Content = Awaited<ReturnType<typeof api.evolve.getSkillVersionContent>>

export function SkillVersionEditDialog({ assetId, version, onClose, onCreated }: {
  assetId: string
  version: Version
  onClose: () => void
  onCreated: (version: Version) => Promise<void> | void
}) {
  const [mode, setMode] = useState<'edit' | 'upload'>('edit')
  const [loadedContent, setLoadedContent] = useState<Content | null>(null)
  const [selectedPath, setSelectedPath] = useState('')
  const [originals, setOriginals] = useState<Record<string, string>>({})
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const [file, setFile] = useState<File | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    if (loadedContent) return
    let active = true
    void api.evolve.getSkillVersionContent(assetId, version.versionId)
      .then((loaded) => {
        if (!active) return
        setLoadedContent(loaded)
        setSelectedPath(loaded.selected?.path ?? '')
        if (loaded.selected) setOriginals({ [loaded.selected.path]: loaded.selected.content })
      })
      .catch((reason) => { if (active) setError(reason instanceof Error ? reason.message : '当前版本加载失败') })
    return () => { active = false }
  }, [assetId, loadedContent, version.versionId])

  const selectPath = async (path: string) => {
    if (path === selectedPath) return
    setSelectedPath(path)
    if (originals[path] != null) return
    try {
      const loaded = await api.evolve.getSkillVersionContent(assetId, version.versionId, path)
      if (loaded.selected) setOriginals((current) => ({ ...current, [path]: loaded.selected!.content }))
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '文件加载失败')
    }
  }

  const submit = async () => {
    setSubmitting(true); setError('')
    try {
      const created = mode === 'edit'
        ? await api.evolve.createSkillVersion(assetId, {
          mode: 'edit', baseVersionId: version.versionId,
          edits: Object.entries(drafts)
            .filter(([path, value]) => originals[path] !== value)
            .map(([path, value]) => ({ path, content: value })),
        })
        : file
          ? await api.evolve.uploadSkillVersion(assetId, version.versionId, file)
          : (() => { throw new Error('请选择 Skill ZIP 包') })()
      await onCreated(created)
      onClose()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '创建版本失败')
    } finally {
      setSubmitting(false)
    }
  }

  const value = drafts[selectedPath] ?? originals[selectedPath] ?? ''
  const hasChanges = Object.entries(drafts).some(([path, next]) => originals[path] !== next)
  return <div className="fixed inset-0 z-50 flex items-center justify-center bg-gray-950/45 p-4">
    <div role="dialog" aria-modal="true" aria-label="编辑 Skill" className="flex max-h-[90vh] w-full max-w-5xl flex-col overflow-hidden rounded-2xl bg-white shadow-2xl">
      <div className="flex items-start justify-between border-b border-gray-100 px-6 py-5"><div><h2 className="text-xl font-semibold text-gray-950">编辑 Skill</h2><p className="mt-1 text-sm text-gray-500">基于当前 {version.version} 创建新版本，历史版本保持不变。</p></div><button type="button" onClick={onClose} className="text-sm text-gray-400 hover:text-gray-700">关闭</button></div>
      <div className="border-b border-gray-100 px-6 pt-4"><div className="inline-flex rounded-lg bg-gray-100 p-1"><button type="button" onClick={() => setMode('edit')} className={`rounded-md px-4 py-2 text-sm font-medium ${mode === 'edit' ? 'bg-white text-blue-700 shadow-sm' : 'text-gray-500'}`}>直接编辑</button><button type="button" onClick={() => setMode('upload')} className={`rounded-md px-4 py-2 text-sm font-medium ${mode === 'upload' ? 'bg-white text-blue-700 shadow-sm' : 'text-gray-500'}`}>上传 Skill 包</button></div></div>
      {mode === 'edit' ? <div className="grid min-h-0 flex-1 lg:grid-cols-[220px_minmax(0,1fr)]">
        <aside className="overflow-auto border-r border-gray-100 p-4"><p className="mb-2 text-[10px] font-semibold uppercase tracking-wider text-gray-400">可编辑文本文件</p>{loadedContent?.files.map((item) => <button type="button" key={item.path} disabled={!item.text} onClick={() => void selectPath(item.path)} className={`block w-full truncate rounded-lg px-3 py-2 text-left font-mono text-xs ${selectedPath === item.path ? 'bg-blue-50 text-blue-700' : 'text-gray-600 hover:bg-gray-50'} disabled:text-gray-300`}>{item.path}</button>)}</aside>
        <label className="flex min-h-[480px] min-w-0 flex-col p-4"><span className="mb-2 text-xs font-medium text-gray-600">文件内容</span><textarea aria-label="文件内容" value={value} disabled={!selectedPath} onChange={(event) => setDrafts((current) => ({ ...current, [selectedPath]: event.target.value }))} className="min-h-0 flex-1 resize-none rounded-xl border border-gray-200 bg-[#0b1020] p-4 font-mono text-xs leading-6 text-gray-100 outline-none focus:border-blue-400" /></label>
      </div> : <div className="flex-1 p-6"><label className="block rounded-xl border border-dashed border-gray-300 bg-gray-50 p-8 text-center"><span className="block text-sm font-medium text-gray-800">选择完整 Skill ZIP 包</span><span className="mt-1 block text-xs text-gray-500">上传后创建新版本，不覆盖当前或历史版本。</span><input aria-label="选择 Skill ZIP 包" className="mt-4 text-sm" type="file" accept=".zip,application/zip" onChange={(event) => setFile(event.target.files?.[0] ?? null)} /></label>{file && <p className="mt-3 text-sm text-gray-600">已选择：{file.name}</p>}</div>}
      <div className="flex items-center justify-between border-t border-gray-100 px-6 py-4"><p role="alert" className="text-sm text-red-600">{error}</p><div className="flex gap-2"><button type="button" onClick={onClose} className="rounded-lg border border-gray-200 px-4 py-2 text-sm text-gray-600">取消</button><button type="button" disabled={submitting || (mode === 'edit' ? !hasChanges : !file)} onClick={() => void submit()} className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white disabled:cursor-not-allowed disabled:bg-blue-300">{mode === 'edit' ? '创建新版本' : '上传并创建新版本'}</button></div></div>
    </div>
  </div>
}

export function SkillVersionRollbackDialog({ version, submitting, error, onClose, onConfirm }: {
  version: Version
  submitting: boolean
  error: string
  onClose: () => void
  onConfirm: () => void
}) {
  return <div className="fixed inset-0 z-50 flex items-center justify-center bg-gray-950/45 p-4">
    <div role="dialog" aria-modal="true" aria-label={`确认回滚到 ${version.version}？`} className="w-full max-w-lg rounded-2xl bg-white p-6 shadow-2xl">
      <h2 className="text-lg font-semibold text-gray-950">确认回滚到 {version.version}？</h2>
      <p className="mt-3 text-sm leading-6 text-gray-600">将基于 {version.version} 的内容创建新的最新版本，不会覆盖或删除任何历史版本。</p>
      <p className="mt-2 font-mono text-xs text-gray-400">来源版本 ID：{version.versionId}</p>
      {error && <p role="alert" className="mt-3 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>}
      <div className="mt-6 flex justify-end gap-2"><button type="button" onClick={onClose} className="rounded-lg border border-gray-200 px-4 py-2 text-sm text-gray-600">取消</button><button type="button" disabled={submitting} onClick={onConfirm} className="rounded-lg bg-amber-600 px-4 py-2 text-sm font-medium text-white disabled:bg-amber-300">确认创建新版本</button></div>
    </div>
  </div>
}
