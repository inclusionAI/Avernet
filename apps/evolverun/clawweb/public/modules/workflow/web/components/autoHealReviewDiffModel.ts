import { stringify as stringifyYaml } from 'yaml'
import { assertLosslessNumbers } from '../editor/losslessNumbers'
import { sideBySideDiff, type DiffLine, type DiffRow } from './workflow-version-diff/sideBySide'

// Bound the actual matrix, including its boundary row/column (at most ~1 MB).
export const MAX_REVIEW_LCS_CELLS = 250_000
// Guard before YAML's duplicate-key/lossless scan, which is costly on wide maps.
export const MAX_REVIEW_FORMAT_CHARS = 64 * 1024

/** Same complete-document contract as formatSpecForDiff, without importing its
 * React/API entry point. Never unwrap content or strip historical-looking keys.
 * Unsafe numbers, duplicate keys and authored scalar strings stay verbatim. */
export function formatReviewSnapshotWithStatus(specJson: string): { text: string; oversized: boolean } {
  if (specJson.length > MAX_REVIEW_FORMAT_CHARS) return { text: specJson, oversized: true }
  try {
    assertLosslessNumbers(specJson)
    const parsed: unknown = JSON.parse(specJson)
    return { text: typeof parsed === 'string' ? specJson : stringifyYaml(parsed, { lineWidth: 0 }), oversized: false }
  } catch {
    return { text: specJson, oversized: false }
  }
}

/** Preserve the string-only formatter contract for existing callers. */
export function formatReviewSnapshot(specJson: string): string {
  return formatReviewSnapshotWithStatus(specJson).text
}

function positionalRows(a: string[], b: string[]): DiffRow[] {
  return Array.from({ length: Math.max(a.length, b.length) }, (_, index) => {
    if (index < a.length && index < b.length && a[index] === b[index]) {
      const line: DiffLine = { type: 'ctx', text: a[index], fromLine: index + 1, toLine: index + 1 }
      return { left: line, right: line, changeId: null }
    }
    return {
      left: index < a.length ? { type: 'del', text: a[index], fromLine: index + 1 } : null,
      right: index < b.length ? { type: 'add', text: b[index], toLine: index + 1 } : null,
      changeId: index,
    }
  })
}

/** Full, non-folded review rows. Use bounded LCS for normal documents and linear
 * same-position comparison for large ones. Neither path truncates source text. */
export function compareReviewText(fromText: string, toText: string) {
  const a = fromText.split('\n'), b = toText.split('\n')
  const n = a.length, m = b.length, width = m + 1
  const identical = fromText === toText
  const positional = !identical && (n + 1) * width > MAX_REVIEW_LCS_CELLS
  let rows: DiffRow[]
  if (identical || positional) {
    rows = positionalRows(a, b)
  } else {
    const dp = new Uint32Array((n + 1) * width)
    for (let i = n - 1; i >= 0; i--) {
      for (let j = m - 1; j >= 0; j--) {
        dp[i * width + j] = a[i] === b[j]
          ? dp[(i + 1) * width + j + 1] + 1
          : Math.max(dp[(i + 1) * width + j], dp[i * width + j + 1])
      }
    }
    const lines: DiffLine[] = []
    let i = 0, j = 0
    while (i < n || j < m) {
      if (i < n && j < m && a[i] === b[j]) {
        lines.push({ type: 'ctx', text: a[i], fromLine: i + 1, toLine: j + 1 })
        i++; j++
      } else if (i < n && (j === m || dp[(i + 1) * width + j] >= dp[i * width + j + 1])) {
        lines.push({ type: 'del', text: a[i], fromLine: i + 1 })
        i++
      } else {
        lines.push({ type: 'add', text: b[j], toLine: j + 1 })
        j++
      }
    }
    rows = sideBySideDiff(lines)
  }
  const additions = rows.filter(row => row.right?.type === 'add').length
  const deletions = rows.filter(row => row.left?.type === 'del').length
  return { rows, additions, deletions, positional }
}
