import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { parse } from 'yaml'
import AutoHealSaveReview from '../AutoHealSaveReview'
import AutoHealReviewDiff from '../AutoHealReviewDiff'
import * as yamlViewer from '../YamlCodeViewer'
import type { AutoHealUnifiedBaseline } from '../../api/autoHealUnifiedBaseline'

const original = {
  id: 'ids-log-self-audit', version: '2', updatedAt: 123,
  facade: { command: '/audit', enabled: true },
  content: 'title: nested content must stay nested\n',
  nodes: [{ id: 'audit', script: 'echo "${{ inputs.message }}"\nprintf "<img src=x onerror=alert(1)>"\n' }],
}
const baseline: AutoHealUnifiedBaseline = Object.freeze({
  workflowId: original.id, packId: 'audit-pack', sourceVersion: 'draft',
  sourceCommit: 'a'.repeat(40), expectedSavedHead: 'a'.repeat(40),
  expectedDraftToken: 'b'.repeat(64), originalSpecJson: JSON.stringify(original),
})
const candidate = { ...original, updatedAt: 124, nodes: [...original.nodes, { id: 'added', script: 'echo done\n' }] }
const candidateJson = JSON.stringify(candidate)

function review(json = candidateJson) {
  const onConfirm = vi.fn(), onCancel = vi.fn()
  return { ...render(<AutoHealSaveReview baseline={baseline} candidateJson={json} onConfirm={onConfirm} onCancel={onCancel} />), onConfirm, onCancel }
}

function codeText(pane: HTMLElement) {
  return Array.from(pane.querySelectorAll('code')).map(line => line.textContent).join('\n')
}

afterEach(cleanup)

describe('AutoHealSaveReview', () => {
  it('shows two complete multiline, highlighted documents without unwrapping or executing authored text', () => {
    const { container } = review()
    const left = screen.getByRole('region', { name: '当前数据库原始草稿' })
    const right = screen.getByRole('region', { name: '拟保存的完整修复内容' })
    expect(parse(codeText(left))).toEqual(original)
    expect(parse(codeText(right))).toEqual(candidate)
    expect(codeText(left)).toContain('  command: /audit')
    expect(codeText(left)).toContain('${{ inputs.message }}')
    expect(codeText(left)).toContain('<img src=x onerror=alert(1)>')
    expect(container.querySelector('img, script')).toBeNull()
    expect(left.querySelector('[data-syntax-kind="key"]')).toHaveClass('text-primary')
    expect(left.querySelector('[data-syntax-kind="boolean"]')).toHaveTextContent('true')
    expect(left.querySelectorAll('code').length).toBeGreaterThan(10)
    const grid = left.closest('.grid')!
    expect(grid).toHaveClass('grid-cols-2', 'min-w-[720px]')
    expect(grid.parentElement).toHaveClass('overflow-x-auto')
    expect(grid.querySelectorAll('[role="region"]')).toHaveLength(2)
    expect(screen.getByRole('region', { name: '保存前核对' })).toHaveTextContent('草稿 token 和 Git 基线')
    expect(screen.getByText(/nodes\[1\]/)).toBeInTheDocument()
  })

  it('aligns unchanged rows and shows true side line numbers, +/- markers and change backgrounds', () => {
    review(JSON.stringify({ ...original, extra: 'new field' }))
    const left = screen.getByRole('region', { name: '当前数据库原始草稿' })
    const right = screen.getByRole('region', { name: '拟保存的完整修复内容' })
    const leftRows = Array.from(left.querySelectorAll('[data-diff-row]'))
    const rightRows = Array.from(right.querySelectorAll('[data-diff-row]'))
    expect(leftRows.length).toBeGreaterThan(0)
    expect(leftRows).toHaveLength(rightRows.length)
    const added = right.querySelector('[data-line-kind="add"]')!
    expect(added).toHaveClass('bg-success/10')
    expect(added.querySelector('[data-line-marker]')).toHaveTextContent('+')
    expect(added.querySelector('[data-line-number]')?.textContent).toMatch(/^\d+$/)
    expect(left.querySelector('[aria-label="对齐空行"]')).toBeInTheDocument()
    leftRows.forEach((row, index) => {
      if (row.getAttribute('data-line-kind') === 'ctx') {
        expect(row.querySelector('code')?.textContent).toBe(rightRows[index].querySelector('code')?.textContent)
      }
    })
  })

  it('marks a replacement as deleted on the left and added on the right', () => {
    review()
    const left = screen.getByRole('region', { name: '当前数据库原始草稿' })
    const deleted = left.querySelector('[data-line-kind="del"]')!
    expect(deleted).toHaveClass('bg-destructive/10')
    expect(deleted.querySelector('[data-line-marker]')).toHaveTextContent('-')
    expect(deleted).toHaveTextContent('123')
    expect(deleted.querySelector('[data-line-number]')).toHaveTextContent('3')
  })

  it('synchronizes vertical scroll both ways without coupling horizontal scroll or looping', () => {
    review()
    const left = screen.getByRole('region', { name: '当前数据库原始草稿' })
    const right = screen.getByRole('region', { name: '拟保存的完整修复内容' })
    left.scrollLeft = 14; right.scrollLeft = 91
    fireEvent.scroll(left, { target: { scrollTop: 120 } })
    expect(right.scrollTop).toBe(120)
    expect(right.scrollLeft).toBe(91)
    fireEvent.scroll(right, { target: { scrollTop: 240 } })
    expect(left.scrollTop).toBe(240)
    expect(left.scrollLeft).toBe(14)
    fireEvent.scroll(left)
    expect(right.scrollTop).toBe(240)
    expect(right.scrollLeft).toBe(91)
    expect(left).toHaveClass('overflow-scroll')
    expect(right).toHaveClass('overflow-scroll')
  })

  it('keeps confirmation and cancellation callbacks and the exact review inputs untouched', () => {
    const before = JSON.stringify(baseline)
    const { onConfirm, onCancel } = review()
    fireEvent.click(screen.getByRole('button', { name: '取消核对' }))
    expect(onCancel).toHaveBeenCalledOnce()
    expect(onConfirm).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: '确认差异并保存' }))
    expect(onConfirm).toHaveBeenCalledOnce()
    expect(JSON.stringify(baseline)).toBe(before)
    expect(candidateJson).toBe(JSON.stringify(candidate))
    expect(screen.getByRole('region', { name: '保存前核对' })).toHaveTextContent('基线变化时由服务端拒绝')
  })

  it('explains the large-document fallback while keeping both full documents visible', () => {
    const content = Array.from({ length: 510 }, (_, index) => `script-line-${index}`).join('\n')
    const largeOriginal = JSON.stringify({ content })
    const largeCandidate = JSON.stringify({ content: content.replace('script-line-509', 'last-script-line') })
    render(<AutoHealSaveReview baseline={{ ...baseline, originalSpecJson: largeOriginal }} candidateJson={largeCandidate} onConfirm={vi.fn()} onCancel={vi.fn()} />)
    expect(screen.getByRole('status')).toHaveTextContent('同位置逐行对比')
    expect(screen.getByRole('status')).toHaveTextContent('完整内容')
    expect(parse(codeText(screen.getByRole('region', { name: '当前数据库原始草稿' })))).toEqual({ content })
    expect(parse(codeText(screen.getByRole('region', { name: '拟保存的完整修复内容' })))).toEqual(JSON.parse(largeCandidate))
  })

  it.each(['left', 'right'])('keeps oversized %s JSON complete and HTML-safe as one text node without tokenizing it', side => {
    const largeJson = JSON.stringify({
      ...Object.fromEntries(Array.from({ length: 32_000 }, (_, index) => [`field-${index}`, index])),
      ...original, unsafe: '<img src=x onerror=alert(1)><script>alert(1)</script>',
    })
    const smallJson = JSON.stringify(original)
    const tokenizer = vi.spyOn(yamlViewer, 'tokenizeYamlLine')
    try {
      const { container } = render(<AutoHealReviewDiff originalJson={side === 'left' ? largeJson : smallJson} candidateJson={side === 'right' ? largeJson : smallJson} />)
      const largePane = screen.getByRole('region', { name: side === 'left' ? '当前数据库原始草稿' : '拟保存的完整修复内容' })
      const smallPane = screen.getByRole('region', { name: side === 'left' ? '拟保存的完整修复内容' : '当前数据库原始草稿' })
      expect(codeText(largePane)).toBe(largeJson)
      expect(largePane.querySelector('[data-syntax-kind]')).toBeNull()
      const code = largePane.querySelector('code')!
      expect(code.childNodes).toHaveLength(1)
      expect(code.firstChild?.nodeType).toBe(Node.TEXT_NODE)
      expect(container.querySelector('img, script')).toBeNull()
      expect(screen.getByRole('status')).toHaveTextContent('超过格式化预算')
      expect(screen.getByRole('status')).toHaveTextContent('完整原始 JSON')
      expect(screen.getByRole('status')).toHaveTextContent('不截断')
      expect(screen.getByRole('status')).toHaveTextContent(side === 'left' ? '当前数据库原始草稿' : '拟保存的完整修复内容')
      expect(parse(codeText(smallPane))).toEqual(original)
      expect(smallPane.querySelector('[data-syntax-kind="key"]')).toHaveClass('text-primary')
      expect(tokenizer.mock.calls.length).toBeGreaterThan(0)
      expect(tokenizer.mock.calls.some(([text]) => text === largeJson)).toBe(false)
      expect(largePane.closest('.grid')).toHaveClass('grid-cols-2')
    } finally { tokenizer.mockRestore() }
  })

  it('shows the complete unchanged documents and no addition/deletion markers', () => {
    review(baseline.originalSpecJson)
    expect(screen.getByText('无结构差异')).toBeInTheDocument()
    expect(screen.getByText('两侧内容完全相同')).toBeInTheDocument()
    const area = screen.getByRole('region', { name: '保存前核对' })
    expect(area.querySelector('[data-line-kind="add"], [data-line-kind="del"]')).toBeNull()
    expect(parse(codeText(within(area).getByRole('region', { name: '当前数据库原始草稿' })))).toEqual(original)
    expect(parse(codeText(within(area).getByRole('region', { name: '拟保存的完整修复内容' })))).toEqual(original)
  })
})
