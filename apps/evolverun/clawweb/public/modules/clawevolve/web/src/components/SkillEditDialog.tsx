import { useId, useRef, useState } from 'react'
import { api, type EvolveSkillAsset, type EvolveSpaceOwnership } from '../api/client'
import SpaceSelector from './SpaceSelector'

export default function SkillEditDialog({ asset, onClose, onSaved }: {
  asset: EvolveSkillAsset
  onClose: () => void
  onSaved: (ownership: EvolveSpaceOwnership) => void
}) {
  const titleId = useId()
  const submitting = useRef(false)
  const [spaceId, setSpaceId] = useState(asset.spaceId ?? '')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  const save = async () => {
    if (submitting.current) return
    submitting.current = true; setSaving(true); setError('')
    try {
      const ownership = await api.evolve.updateSkillAsset(asset.assetId, { spaceId: spaceId || null })
      onSaved(ownership)
      onClose()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Skill 保存失败')
    } finally { submitting.current = false; setSaving(false) }
  }

  return <div className="fixed inset-0 z-50 flex items-center justify-center bg-gray-950/30 p-4" onClick={() => { if (!saving) onClose() }}>
    <div role="dialog" aria-modal="true" aria-labelledby={titleId} className="w-full max-w-xl rounded-2xl bg-white p-6 shadow-xl" onClick={event => event.stopPropagation()}>
      <div className="flex items-start justify-between gap-4">
        <h2 id={titleId} className="text-xl font-semibold text-gray-950">编辑 Skill</h2>
        <button disabled={saving} onClick={onClose} className="text-sm text-gray-400 disabled:opacity-40">关闭</button>
      </div>
      <div className="mt-5">
        <SpaceSelector value={spaceId} onChange={value => { setSpaceId(value); setError('') }} disabled={saving} />
        {error && <p role="alert" className="mt-3 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>}
      </div>
      <div className="mt-6 flex justify-end gap-2">
        <button disabled={saving} onClick={onClose} className="rounded-lg border border-gray-200 px-4 py-2 text-sm disabled:opacity-40">取消</button>
        <button disabled={saving} onClick={() => void save()} className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-40">{saving ? '保存中…' : '保存'}</button>
      </div>
    </div>
  </div>
}
