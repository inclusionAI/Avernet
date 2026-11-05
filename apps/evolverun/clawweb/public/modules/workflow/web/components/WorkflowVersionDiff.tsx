import { useState, useEffect } from 'react'
import { parse as parseYaml, stringify as stringifyYaml } from 'yaml'
import { api } from '@avernet/clawweb-shared/web/api/client'
import type { VersionDiffResult } from '@avernet/clawweb-shared/web/types'
import SideBySideView from './workflow-version-diff/SideBySideView'

interface WorkflowVersionDiffProps {
  workflowId: string
  fromDeploy: number
  toDeploy: number
}

/** A single line in the unified diff view. */
interface DiffLine {
  type: 'add' | 'del' | 'ctx' | 'skip'
  text: string
  // 1-indexed line number on the side that contains this line; 0 for del-only/add-only
  fromLine?: number
  toLine?: number
}

/**
 * Compute a unified diff between two texts using LCS (longest common subsequence).
 * Zero-dependency; sufficient for spec YAML (typically <2k lines).
 * Pure function, exported for testing.
 */
export function unifiedDiff(fromText: string, toText: string): DiffLine[] {
  const a = fromText.split('\n')
  const b = toText.split('\n')
  const n = a.length
  const m = b.length

  // dp[i][j] = LCS length of a[i..] vs b[j..]
  const dp: number[][] = Array.from({ length: n + 1 }, () => new Array<number>(m + 1).fill(0))
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1])
    }
  }

  const lines: DiffLine[] = []
  let i = 0
  let j = 0
  let fromLine = 0
  let toLine = 0
  while (i < n && j < m) {
    if (a[i] === b[j]) {
      fromLine++
      toLine++
      lines.push({ type: 'ctx', text: a[i], fromLine, toLine })
      i++
      j++
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      fromLine++
      lines.push({ type: 'del', text: a[i], fromLine })
      i++
    } else {
      toLine++
      lines.push({ type: 'add', text: b[j], toLine })
      j++
    }
  }
  while (i < n) {
    fromLine++
    lines.push({ type: 'del', text: a[i], fromLine })
    i++
  }
  while (j < m) {
    toLine++
    lines.push({ type: 'add', text: b[j], toLine })
    j++
  }
  return lines
}

/**
 * Keep the change hunks visible by hiding unchanged lines far away from them.
 * The full diff remains available in the UI for cases where surrounding context
 * is needed.
 */
export function compactDiff(lines: DiffLine[], contextLines = 3): DiffLine[] {
  const changedIndexes = lines
    .map((line, index) => (line.type === 'add' || line.type === 'del' ? index : -1))
    .filter((index) => index >= 0)

  if (changedIndexes.length === 0) return lines

  const visible = new Set<number>()
  for (const index of changedIndexes) {
    for (let nearby = Math.max(0, index - contextLines); nearby <= Math.min(lines.length - 1, index + contextLines); nearby++) {
      visible.add(nearby)
    }
  }

  const compacted: DiffLine[] = []
  let previousIndex = -1
  for (const index of [...visible].sort((a, b) => a - b)) {
    if (index > previousIndex + 1) {
      compacted.push({ type: 'skip', text: `… ${index - previousIndex - 1} 行未变更 …` })
    }
    compacted.push(lines[index])
    previousIndex = index
  }
  if (previousIndex < lines.length - 1) {
    compacted.push({ type: 'skip', text: `… ${lines.length - previousIndex - 1} 行未变更 …` })
  }
  return compacted
}

/** Turn stored JSON (or the legacy { content: yaml } wrapper) into readable YAML.
 * The history API deliberately returns the original snapshot; formatting it here
 * keeps the raw snapshot lossless while making line-level diff useful. */
function stripSyntheticSpecFields(value: unknown): unknown {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return value
  const { updatedAt: _updatedAt, version: _version, facade: _facade, ...persistedSpec } = value as Record<string, unknown>
  return persistedSpec
}

/**
 * Format a deploy-history snapshot in the same persisted-spec shape used for
 * editor comparisons. `updatedAt`, `version`, and `facade` are enriched or
 * synchronized metadata, not deployable workflow content.
 */
export function formatSpecForDiff(specJson: string): string {
  try {
    const parsed = JSON.parse(specJson)
    if (parsed && typeof parsed === 'object' && typeof parsed.content === 'string' && !Array.isArray(parsed.nodes)) {
      return stringifyYaml(stripSyntheticSpecFields(parseYaml(parsed.content)), { lineWidth: 0 })
    }
    return stringifyYaml(stripSyntheticSpecFields(parsed), { lineWidth: 0 })
  } catch {
    return specJson
  }
}

/** Format the current editor value using the same persisted-spec shape as a deploy snapshot. */
export function formatCurrentSpecForDiff(spec: unknown): string {
  return stringifyYaml(stripSyntheticSpecFields(spec), { lineWidth: 0 })
}

function sortSpecValue(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(sortSpecValue)
  if (value && typeof value === 'object') {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>)
        .filter(([, nested]) => nested !== undefined)
        .sort(([a], [b]) => a.localeCompare(b))
        .map(([key, nested]) => [key, sortSpecValue(nested)]),
    )
  }
  return value
}

/** Compare parsed specs so YAML field ordering and formatting do not create a false pending deploy. */
export function specsEqual(fromText: string, toText: string): boolean {
  try {
    return JSON.stringify(sortSpecValue(parseYaml(fromText))) === JSON.stringify(sortSpecValue(parseYaml(toText)))
  } catch {
    return fromText === toText
  }
}

function topLevelChanges(fromText: string, toText: string): string[] {
  try {
    const from = parseYaml(fromText) as Record<string, unknown>
    const to = parseYaml(toText) as Record<string, unknown>
    if (!from || !to || Array.isArray(from) || Array.isArray(to)) return []
    return [...new Set([...Object.keys(from), ...Object.keys(to)])]
      .filter((key) => JSON.stringify(from[key]) !== JSON.stringify(to[key]))
  } catch {
    return []
  }
}

export default function WorkflowVersionDiff({
  workflowId,
  fromDeploy,
  toDeploy,
}: WorkflowVersionDiffProps) {
  const [result, setResult] = useState<VersionDiffResult | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    setResult(null)
    api.workflows
      .diffHistory(workflowId, fromDeploy, toDeploy)
      .then((r) => {
        if (!cancelled) setResult(r)
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : '加载对比失败')
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [workflowId, fromDeploy, toDeploy])

  if (loading) {
    return <div className="p-4 text-sm text-gray-500">加载版本对比…</div>
  }
  if (error) {
    return <div className="p-4 text-sm text-red-500">{error}</div>
  }
  if (!result) {
    return null
  }

  const fromText = formatSpecForDiff(result.from.specJson ?? '')
  const toText = formatSpecForDiff(result.to.specJson ?? '')
  const diffLines = unifiedDiff(fromText, toText)
  return <SideBySideView result={result} lines={diffLines} changedFields={topLevelChanges(fromText, toText)} />
}
