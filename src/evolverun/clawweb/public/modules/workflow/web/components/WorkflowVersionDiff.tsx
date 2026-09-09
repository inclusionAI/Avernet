import { useState, useEffect } from 'react'
import { parse as parseYaml, stringify as stringifyYaml } from 'yaml'
import { api } from '@avernet/clawweb-shared/web/api/client'
import type { VersionDiffResult } from '@avernet/clawweb-shared/web/types'

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
export function formatSpecForDiff(specJson: string): string {
  try {
    const parsed = JSON.parse(specJson)
    if (parsed && typeof parsed === 'object' && typeof parsed.content === 'string' && !Array.isArray(parsed.nodes)) {
      return parsed.content
    }
    return stringifyYaml(parsed, { lineWidth: 0 })
  } catch {
    return specJson
  }
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

function formatTime(epochSec: number): string {
  if (!epochSec) return '-'
  return new Date(epochSec * 1000).toLocaleString()
}

export default function WorkflowVersionDiff({
  workflowId,
  fromDeploy,
  toDeploy,
}: WorkflowVersionDiffProps) {
  const [result, setResult] = useState<VersionDiffResult | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [showFullContext, setShowFullContext] = useState(false)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    setResult(null)
    setShowFullContext(false)
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
  const additions = diffLines.filter((l) => l.type === 'add').length
  const deletions = diffLines.filter((l) => l.type === 'del').length
  const changedFields = topLevelChanges(fromText, toText)
  const displayedLines = showFullContext ? diffLines : compactDiff(diffLines)

  return (
    <div className="flex h-full flex-col">
      {/* Header: from / to metadata + summary */}
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-gray-200 bg-gray-50 px-3 py-1.5 text-xs">
        <div className="flex items-center gap-3 text-gray-600">
          <span>
            <span className="text-gray-400">from </span>
            <span className="font-medium text-gray-700">v{result.from.version}</span>
            <span className="text-gray-400"> (deploy #{result.from.deployNumber}, {result.from.action})</span>
          </span>
          <span className="text-gray-400">→</span>
          <span>
            <span className="text-gray-400">to </span>
            <span className="font-medium text-gray-700">v{result.to.version}</span>
            <span className="text-gray-400"> (deploy #{result.to.deployNumber}, {result.to.action})</span>
          </span>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setShowFullContext((value) => !value)}
            className="rounded border border-gray-300 bg-white px-2 py-0.5 text-[11px] text-gray-600 hover:bg-gray-50"
          >
            {showFullContext ? '仅看变更' : '完整内容'}
          </button>
          <span className="text-green-600">+{additions}</span>
          <span className="text-red-500">-{deletions}</span>
        </div>
      </div>

      {changedFields.length > 0 && (
        <div className="border-b border-blue-100 bg-blue-50 px-3 py-2 text-xs text-slate-600">
          <span className="mr-2 font-medium text-slate-700">变更字段</span>
          {changedFields.map((field) => (
            <span key={field} className="mr-1 inline-block rounded bg-white px-1.5 py-0.5 font-mono text-[11px] text-blue-700">
              {field}
            </span>
          ))}
        </div>
      )}

      {/* Unified diff body */}
      <div className="flex-1 overflow-auto bg-gray-900 font-mono text-xs leading-relaxed">
        {diffLines.length === 0 && (
          <div className="p-4 text-gray-400">两个版本内容完全相同</div>
        )}
        {displayedLines.map((line, idx) => {
          if (line.type === 'skip') {
            return (
              <div key={`skip-${idx}`} className="border-y border-gray-700 bg-gray-800 px-3 py-1 text-center text-gray-500">
                {line.text}
              </div>
            )
          }
          const bg =
            line.type === 'add'
              ? 'bg-green-900/30'
              : line.type === 'del'
                ? 'bg-red-900/30'
                : ''
          const color =
            line.type === 'add' ? 'text-green-300' : line.type === 'del' ? 'text-red-300' : 'text-gray-300'
          const sign = line.type === 'add' ? '+' : line.type === 'del' ? '-' : ' '
          const ln = line.type === 'del' ? line.fromLine : line.toLine
          return (
            <div key={idx} className={`flex ${bg}`}>
              <span className="w-10 shrink-0 select-none border-r border-gray-700 px-1 text-right text-gray-500">
                {ln ?? ''}
              </span>
              <span className={`shrink-0 select-none px-1 ${color}`}>{sign}</span>
              <span className={`whitespace-pre px-1 ${color}`}>{line.text}</span>
            </div>
          )
        })}
      </div>

      <div className="border-t border-gray-200 bg-gray-50 px-3 py-1 text-[10px] text-gray-400">
        from {formatTime(result.from.gmtCreate)} · to {formatTime(result.to.gmtCreate)}
      </div>
    </div>
  )
}
