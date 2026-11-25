import { describe, expect, it, vi } from 'vitest'
import { parse } from 'yaml'
import * as losslessNumbers from '../../editor/losslessNumbers'
import { compareReviewText, formatReviewSnapshot, formatReviewSnapshotWithStatus, MAX_REVIEW_FORMAT_CHARS, MAX_REVIEW_LCS_CELLS } from '../autoHealReviewDiffModel'

// Keep this helper independent of the historical-version React/API entry point.
vi.mock('../WorkflowVersionDiff', () => { throw new Error('Review must not import the version diff component') })

describe('formatReviewSnapshot', () => {
  it('checks the size budget before lossless YAML parsing a 32,000-field JSON object', () => {
    const input = JSON.stringify(Object.fromEntries(Array.from({ length: 32_000 }, (_, index) => [`field-${index}`, index])))
    expect(input.length).toBeGreaterThan(64 * 1024)
    const validation = vi.spyOn(losslessNumbers, 'assertLosslessNumbers').mockImplementation(() => { throw new Error('oversized input reached YAML parsing') })
    try {
      expect(formatReviewSnapshot(input)).toBe(input)
      expect(validation.mock.calls.length).toBe(0)
    } finally { validation.mockRestore() }
  })

  it('formats exactly at the character budget but bypasses parsing at budget + 1', () => {
    const overhead = JSON.stringify({ text: '' }).length
    const atBudget = JSON.stringify({ text: 'x'.repeat(MAX_REVIEW_FORMAT_CHARS - overhead) })
    expect(atBudget.length).toBe(MAX_REVIEW_FORMAT_CHARS)
    const validation = vi.spyOn(losslessNumbers, 'assertLosslessNumbers')
    try {
      const formatted = formatReviewSnapshotWithStatus(atBudget)
      expect(formatted.oversized).toBe(false)
      expect(parse(formatted.text)).toEqual(JSON.parse(atBudget))
      expect(validation.mock.calls.length).toBe(1)
      validation.mockClear()
      const overBudget = `${atBudget} `
      expect(overBudget.length).toBe(MAX_REVIEW_FORMAT_CHARS + 1)
      expect(formatReviewSnapshotWithStatus(overBudget)).toEqual({ text: overBudget, oversized: true })
      expect(validation.mock.calls.length).toBe(0)
    } finally { validation.mockRestore() }
  })

  it('round-trips every field, template, script and nested content as a complete document', () => {
    const spec = {
      id: 'ids-log-self-audit', version: 7, updatedAt: '2026-11-08T00:00:00Z',
      facade: { command: '/audit', enabled: false },
      content: 'id: nested\nversion: 1\n',
      nodes: [{ id: 'task', script: 'echo "${{ inputs.value }}"\n<script>alert("x")</script>\n\n', timeout: 10 }],
      __clawweb_snapshot_v1: { version: 1, contentSource: 'untrusted' },
    }
    const input = JSON.stringify(spec)
    const result = formatReviewSnapshot(input)
    expect(result.split('\n').length).toBeGreaterThan(10)
    expect(result).toContain('  command: /audit')
    expect(result).toContain('${{ inputs.value }}')
    expect(parse(result)).toEqual(spec)
    expect(input).toBe(JSON.stringify(spec))
  })

  it.each([
    '{"id":"x","value":9007199254740993}',
    '{"id":"x","id":"y"}',
    '{"value":0.10000000000000001}',
    '{"value":-0}',
    '{not-json',
    '"authored scalar"',
  ])('retains the exact source rather than losing information: %s', input => {
    expect(formatReviewSnapshot(input)).toBe(input)
  })
})

describe('compareReviewText', () => {
  it('aligns an insertion and keeps the following context and original line numbers', () => {
    const result = compareReviewText('id: audit\nlast: retained', 'id: audit\nnew: value\nlast: retained')
    expect(result.positional).toBe(false)
    expect(result.additions).toBe(1)
    expect(result.deletions).toBe(0)
    expect(result.rows).toEqual([
      { left: { type: 'ctx', text: 'id: audit', fromLine: 1, toLine: 1 }, right: { type: 'ctx', text: 'id: audit', fromLine: 1, toLine: 1 }, changeId: null },
      { left: null, right: { type: 'add', text: 'new: value', toLine: 2 }, changeId: 0 },
      { left: { type: 'ctx', text: 'last: retained', fromLine: 2, toLine: 3 }, right: { type: 'ctx', text: 'last: retained', fromLine: 2, toLine: 3 }, changeId: null },
    ])
  })

  it('pairs replacements and retains deletion-only lines', () => {
    const result = compareReviewText('same\nold\nremoved\nlast', 'same\nnew\nlast')
    expect(result.additions).toBe(1)
    expect(result.deletions).toBe(2)
    expect(result.rows[1]).toMatchObject({ left: { text: 'old', fromLine: 2 }, right: { text: 'new', toLine: 2 } })
    expect(result.rows[2]).toMatchObject({ left: { text: 'removed', fromLine: 3 }, right: null })
    expect(result.rows[3].left?.text).toBe('last')
  })

  it('caps LCS allocation and falls back to linear positional rows without truncating either side', () => {
    const left = Array.from({ length: 20_000 }, (_, index) => `left-${index}`)
    const right = Array.from({ length: 20_001 }, (_, index) => `right-${index}`)
    expect((left.length + 1) * (right.length + 1)).toBeGreaterThan(MAX_REVIEW_LCS_CELLS)
    const allocation = vi.spyOn(globalThis, 'Uint32Array')
    try {
      const result = compareReviewText(left.join('\n'), right.join('\n'))
      expect(allocation).not.toHaveBeenCalled()
      expect(result.positional).toBe(true)
      expect(result.rows).toHaveLength(20_001)
      expect(result.rows.map(row => row.left?.text).filter(text => text !== undefined)).toEqual(left)
      expect(result.rows.map(row => row.right?.text)).toEqual(right)
      expect(result.rows.at(-1)).toMatchObject({ left: null, right: { text: 'right-20000', toLine: 20_001 } })
      expect(result.additions).toBe(right.length)
      expect(result.deletions).toBe(left.length)
    } finally { allocation.mockRestore() }
  })

  it('avoids the matrix and misleading fallback warning for identical large documents', () => {
    const text = Array.from({ length: 20_000 }, (_, index) => `line-${index}`).join('\n')
    const allocation = vi.spyOn(globalThis, 'Uint32Array')
    try {
      const result = compareReviewText(text, text)
      expect(allocation).not.toHaveBeenCalled()
      expect(result.positional).toBe(false)
      expect(result.rows).toHaveLength(20_000)
      expect(result.additions).toBe(0)
      expect(result.deletions).toBe(0)
      expect(result.rows.every(row => row.changeId === null && row.left?.type === 'ctx' && row.right?.type === 'ctx')).toBe(true)
    } finally { allocation.mockRestore() }
  })

  it('preserves blank and trailing lines', () => {
    const result = compareReviewText('a\n\nb\n', 'a\n\nc\n')
    expect(result.rows.map(row => row.left?.text).join('\n')).toBe('a\n\nb\n')
    expect(result.rows.map(row => row.right?.text).join('\n')).toBe('a\n\nc\n')
  })
})
