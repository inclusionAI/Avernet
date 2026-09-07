import { useEffect, useState, useCallback } from 'react'
import { api } from '@avernet/clawweb-shared/web/api/client'
import type { VersionListItem } from '@avernet/clawweb-shared/web/types'

export interface VersionSelectorProps {
  workflowId: string
  value?: number | null
  onChange?: (version: number) => void
  /** If true, list every deploy version; otherwise only the active/default version. */
  includeInactive?: boolean
  disabled?: boolean
  placeholder?: string
  className?: string
}

/** Reusable version selector for a workflow.
 *
 * Loads deploy versions from ClawWeb and renders a dropdown. Active version
 * rows are highlighted; when includeInactive is true each row also shows its
 * deploy number and a "默认" badge for the active entry.
 */
export default function VersionSelector({
  workflowId,
  value,
  onChange,
  includeInactive = true,
  disabled = false,
  placeholder = '选择版本…',
  className = '',
}: VersionSelectorProps) {
  const [versions, setVersions] = useState<VersionListItem[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(() => {
    if (!workflowId) return
    setLoading(true)
    setError(null)
    api.workflows
      .listVersions(workflowId)
      .then((r) => {
        const items = r.versions ?? []
        setVersions(includeInactive ? items : items.filter((v) => v.isActive))
      })
      .catch((err) => setError(err instanceof Error ? err.message : '加载版本失败'))
      .finally(() => setLoading(false))
  }, [workflowId, includeInactive])

  useEffect(() => {
    load()
  }, [load])

  return (
    <div className={`relative ${className}`}>
      <select
        value={value ?? ''}
        onChange={(e) => onChange?.(Number(e.target.value))}
        disabled={disabled || loading}
        className="w-full rounded border border-gray-300 bg-white px-2 py-1 text-sm text-gray-700 focus:border-blue-500 focus:outline-none disabled:opacity-60"
      >
        <option value="" disabled>
          {loading ? '加载中…' : placeholder}
        </option>
        {versions.map((v) => (
          <option key={v.version} value={v.version}>
            v{v.version} #{v.deployNumber}
            {v.tagName ? ` (${v.tagName})` : ''}
            {v.isActive ? ' — 默认' : ''}
          </option>
        ))}
      </select>
      {error && <p className="mt-1 text-xs text-red-600">{error}</p>}
    </div>
  )
}
