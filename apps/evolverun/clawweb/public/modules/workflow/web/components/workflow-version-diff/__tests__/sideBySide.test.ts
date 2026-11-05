import { describe, expect, it } from 'vitest'
import { compactRows, sideBySideDiff, type DiffLine, type DiffRow } from '../sideBySide'

const ctx = (text: string, fromLine: number, toLine = fromLine): DiffLine => ({ type: 'ctx', text, fromLine, toLine })
const del = (text: string, fromLine: number): DiffLine => ({ type: 'del', text, fromLine })
const add = (text: string, toLine: number): DiffLine => ({ type: 'add', text, toLine })
const row = (line: DiffLine): DiffRow => ({ left: line, right: line, changeId: null })

describe('sideBySideDiff', () => {
  it('pairs uneven contiguous changes in order, preserving line objects and group IDs', () => {
    const before = ctx('before', 1)
    const old1 = del('old 1', 2)
    const old2 = del('old 2', 3)
    const old3 = del('old 3', 4)
    const new1 = add('new 1', 2)
    const after = ctx('after', 5, 3)
    const new2 = add('new 2', 4)
    const new3 = add('new 3', 5)
    const old4 = del('old 4', 6)
    const input = [before, old1, old2, old3, new1, after, new2, new3, old4]

    const result = sideBySideDiff(input)
    expect(result).toEqual([
      row(before),
      { left: old1, right: new1, changeId: 0 },
      { left: old2, right: null, changeId: 0 },
      { left: old3, right: null, changeId: 0 },
      row(after),
      { left: old4, right: new2, changeId: 1 },
      { left: null, right: new3, changeId: 1 },
    ])
    expect(result[1].left).toBe(old1)
    expect(result[1].right).toBe(new1)
    expect(input).toEqual([before, old1, old2, old3, new1, after, new2, new3, old4])
  })

  it('handles pure insertions, pure deletions, and empty input', () => {
    const inserted = add('inserted', 10)
    const deleted = del('deleted', 20)
    expect(sideBySideDiff([inserted])).toEqual([{ left: null, right: inserted, changeId: 0 }])
    expect(sideBySideDiff([deleted])).toEqual([{ left: deleted, right: null, changeId: 0 }])
    expect(sideBySideDiff([])).toEqual([])
  })

  it('retains pre-existing skip markers on both sides and treats them as group boundaries', () => {
    const marker: DiffLine = { type: 'skip', text: 'already omitted', fromLine: 8, toLine: 9 }
    const first = del('old', 7)
    const second = add('new', 10)
    expect(sideBySideDiff([first, marker, second])).toEqual([
      { left: first, right: null, changeId: 0 },
      row(marker),
      { left: null, right: second, changeId: 1 },
    ])
  })
})

describe('compactRows', () => {
  it('keeps context and reports leading and trailing skips as half-open row ranges', () => {
    const lines = Array.from({ length: 10 }, (_, i) => ctx(`line ${i}`, i + 1))
    const changed = del('old', 5)
    const rows = lines.map(row)
    rows[4] = { left: changed, right: null, changeId: 0 }
    expect(compactRows(rows, 1)).toEqual([
      { kind: 'skip', start: 0, end: 3, count: 3 },
      { kind: 'row', row: rows[3], index: 3 },
      { kind: 'row', row: rows[4], index: 4 },
      { kind: 'row', row: rows[5], index: 5 },
      { kind: 'skip', start: 6, end: 10, count: 4 },
    ])
  })

  it('merges overlapping and adjacent context windows and skips only gaps', () => {
    const rows = Array.from({ length: 12 }, (_, i) => row(ctx(`${i}`, i + 1)))
    rows[2] = { left: del('a', 3), right: null, changeId: 0 }
    rows[5] = { left: null, right: add('b', 6), changeId: 1 }
    rows[10] = { left: del('c', 11), right: null, changeId: 2 }
    expect(compactRows(rows, 1)).toEqual([
      { kind: 'skip', start: 0, end: 1, count: 1 },
      ...rows.slice(1, 7).map((entry, i) => ({ kind: 'row', row: entry, index: i + 1 })),
      { kind: 'skip', start: 7, end: 9, count: 2 },
      ...rows.slice(9).map((entry, i) => ({ kind: 'row', row: entry, index: i + 9 })),
    ])
  })

  it('keeps all rows if there are no changes, including an empty input', () => {
    const rows = [row(ctx('one', 1)), row(ctx('two', 2))]
    expect(compactRows(rows, 0)).toEqual(rows.map((entry, index) => ({ kind: 'row', row: entry, index })))
    expect(compactRows([])).toEqual([])
  })

  it('supports zero context and defaults to three context rows', () => {
    const rows = Array.from({ length: 9 }, (_, i) => row(ctx(`${i}`, i + 1)))
    rows[4] = { left: del('old', 5), right: add('new', 5), changeId: 0 }
    expect(compactRows(rows, 0)).toEqual([
      { kind: 'skip', start: 0, end: 4, count: 4 },
      { kind: 'row', row: rows[4], index: 4 },
      { kind: 'skip', start: 5, end: 9, count: 4 },
    ])
    expect(compactRows(rows)).toEqual([
      { kind: 'skip', start: 0, end: 1, count: 1 },
      ...rows.slice(1, 8).map((entry, i) => ({ kind: 'row', row: entry, index: i + 1 })),
      { kind: 'skip', start: 8, end: 9, count: 1 },
    ])
  })
})
