import { useEffect, useState } from 'react'
import { api, type EvolveSpace, type EvolveSpaceOwnership } from '../api/client'
import { useClientUser } from '../hooks/useClientUser'

export function spaceLabel(record: EvolveSpaceOwnership): string {
  if (record.spaceType === 'TEAM') return `团队空间 · ${record.spaceName || record.spaceId || '名称未知'}`
  if (record.spaceType === 'PERSONAL') return '私有'
  return record.spaceId ? `所属空间 · ${record.spaceName || record.spaceId}` : '私有'
}

export default function SpaceSelector({ value, onChange, disabled = false }: {
  value: string
  onChange: (spaceId: string) => void
  disabled?: boolean
}) {
  const { user } = useClientUser()
  const [spaces, setSpaces] = useState<EvolveSpace[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    let active = true
    setLoading(true)
    setError('')
    setSpaces([])
    void Promise.resolve().then(() => api.evolve.listSpaces()).then((result) => {
      if (active) setSpaces(result.items.filter((space) => space.type === 'TEAM'))
    }).catch((reason) => {
      if (active) setError(reason instanceof Error ? reason.message : '空间加载失败')
    }).finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [user?.userId, attempt])

  return <div>
    <label className="block">
      <span className="mb-1.5 block text-xs font-medium text-gray-600">所属空间</span>
      <select value={value} onChange={(event) => onChange(event.target.value)} disabled={disabled || loading || Boolean(error)} className="w-full rounded-lg border border-gray-200 bg-white px-3 py-2.5 text-sm disabled:bg-gray-50">
        <option value="">私有</option>
        {value && !spaces.some((space) => space.id === value) && <option value={value}>所选空间 · {value}</option>}
        {spaces.map((space) => <option key={space.id} value={space.id}>团队空间 · {space.name}</option>)}
      </select>
    </label>
    {loading && <p role="status" className="mt-1 text-xs text-gray-500">正在加载空间…</p>}
    {error && <div role="alert" className="mt-1 text-xs text-red-600">空间加载失败：{error}<button type="button" disabled={disabled} onClick={() => setAttempt((value) => value + 1)} className="ml-2 underline">重试</button></div>}
  </div>
}
