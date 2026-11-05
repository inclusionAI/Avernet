/** A unified diff line, structurally compatible with WorkflowVersionDiff. */
export type DiffLine = {
  type: 'add' | 'del' | 'ctx' | 'skip'
  text: string
  fromLine?: number
  toLine?: number
}

export type DiffRow = {
  left: DiffLine | null
  right: DiffLine | null
  changeId: number | null
}

/**
 * Align each contiguous run of changes in original order on each side.
 * Context and pre-existing skip markers are retained on both sides and end a
 * change group. Lines are reused without changing their original numbers.
 */
export function sideBySideDiff(lines: DiffLine[]): DiffRow[] {
  const rows: DiffRow[] = []
  let index = 0
  let changeId = 0

  while (index < lines.length) {
    const line = lines[index]
    if (line.type === 'ctx' || line.type === 'skip') {
      rows.push({ left: line, right: line, changeId: null })
      index++
      continue
    }

    const deleted: DiffLine[] = []
    const added: DiffLine[] = []
    while (index < lines.length && (lines[index].type === 'del' || lines[index].type === 'add')) {
      const changed = lines[index++]
      if (changed.type === 'del') deleted.push(changed)
      else added.push(changed)
    }
    for (let offset = 0; offset < Math.max(deleted.length, added.length); offset++) {
      rows.push({ left: deleted[offset] ?? null, right: added[offset] ?? null, changeId })
    }
    changeId++
  }
  return rows
}

type CompactRow =
  | { kind: 'row'; row: DiffRow; index: number }
  | { kind: 'skip'; start: number; end: number; count: number }

/**
 * Keep changed rows and up to contextLines surrounding rows. Skipped ranges
 * use zero-based, start-inclusive/end-exclusive indices into the input rows.
 * If nothing changed, every row is retained. Negative context is treated as 0.
 */
export function compactRows(rows: DiffRow[], contextLines = 3): CompactRow[] {
  const context = Number.isFinite(contextLines) ? Math.max(0, Math.floor(contextLines)) : 0
  const windows: Array<{ start: number; end: number }> = []

  rows.forEach((row, index) => {
    if (row.changeId === null) return
    const start = Math.max(0, index - context)
    const end = Math.min(rows.length, index + context + 1)
    const previous = windows[windows.length - 1]
    if (previous && start <= previous.end) previous.end = Math.max(previous.end, end)
    else windows.push({ start, end })
  })

  if (windows.length === 0) {
    return rows.map((row, index) => ({ kind: 'row', row, index }))
  }

  const result: CompactRow[] = []
  let cursor = 0
  for (const { start, end } of windows) {
    if (cursor < start) result.push({ kind: 'skip', start: cursor, end: start, count: start - cursor })
    for (let index = start; index < end; index++) {
      result.push({ kind: 'row', row: rows[index], index })
    }
    cursor = end
  }
  if (cursor < rows.length) {
    result.push({ kind: 'skip', start: cursor, end: rows.length, count: rows.length - cursor })
  }
  return result
}
