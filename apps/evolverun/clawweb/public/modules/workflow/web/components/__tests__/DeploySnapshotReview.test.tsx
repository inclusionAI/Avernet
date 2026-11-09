import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import DeploySnapshotReview, { type DeployReview } from '../DeploySnapshotReview'

afterEach(cleanup)

const side = (text: string, mode = '100644') => ({
  mode,
  contentBase64: btoa(Array.from(new TextEncoder().encode(text), byte => String.fromCharCode(byte)).join('')),
})

function renderChange(change: DeployReview) {
  return render(<DeploySnapshotReview changes={[change]} reviewed={false} onReviewedChange={vi.fn()} />)
}

function expectToken(region: HTMLElement, kind: string, value: string) {
  const tokens = Array.from(region.querySelectorAll(`[data-syntax-kind="${kind}"]`), node => node.textContent)
  expect(tokens).toContain(value)
}

describe('DeploySnapshotReview', () => {
  it.each([
    { path: 'scripts/main.py', before: 'def old():\n    return True\n', after: 'def new():\n    return False\n', kind: 'keyword', beforeToken: 'def', afterToken: 'return' },
    { path: 'workflow.yaml', before: 'name: "old"\nenabled: true\n', after: 'name: "new"\nenabled: false\n', kind: 'string', beforeToken: '"old"', afterToken: '"new"' },
    { path: 'config.json', before: '{"name":"old","count":1}\n', after: '{"name":"new","count":2}\n', kind: 'string', beforeToken: '"old"', afterToken: '"new"' },
  ])('shows and highlights both versions of $path without changing the contents', ({ path, before, after, kind, beforeToken, afterToken }) => {
    renderChange({ path, operation: '更新', before: side(before), after: side(after) })
    expect(screen.getByRole('heading', { name: `更新：${path}` })).toBeVisible()
    for (const [label, text, token] of [
      ['生效内容', before, beforeToken],
      ['保存版本内容', after, afterToken],
    ] as const) {
      const region = screen.getByRole('region', { name: label })
      expect(region.querySelector('code')?.textContent).toBe(text)
      expectToken(region, kind, token)
      expect(within(region).getByText('[原始字节 Base64]')).toBeVisible()
      expect(within(region).getByText(side(text).contentBase64, { exact: true })).toBeVisible()
    }
  })

  it('keeps unsupported file contents unchanged and unhighlighted', () => {
    const before = '  before\t<img src=x>\r\n\n'
    const after = 'after & <script>literal</script>\n'
    renderChange({ path: 'notes.unknown', operation: '更新', before: side(before), after: side(after) })
    for (const [label, text] of [['生效内容', before], ['保存版本内容', after]] as const) {
      const region = screen.getByRole('region', { name: label })
      expect(region.querySelector('code')?.textContent).toBe(text)
      expect(region.querySelector('[data-syntax-kind]')).toBeNull()
      expect(region.querySelector('img, script')).toBeNull()
      expect(within(region).getByText(side(text).contentBase64, { exact: true })).toBeVisible()
    }
  })

  it('shows binary base64 and exposes the exact raw bytes of Unicode text', () => {
    const binary = { mode: '100755', contentBase64: btoa('\x00\xff\x80\x01') }
    const text = '标题: "你好 🌏"\n'
    const utf8 = side(text)
    renderChange({ path: 'workflow.yml', operation: '更新', before: binary, after: utf8 })
    const before = screen.getByRole('region', { name: '生效内容' })
    expect(within(before).getByText('二进制 Base64')).toBeVisible()
    expect(within(before).getByText(binary.contentBase64, { exact: true })).toBeVisible()
    expect(before.querySelector('[data-syntax-kind]')).toBeNull()
    const after = screen.getByRole('region', { name: '保存版本内容' })
    expect(after.querySelector('code')?.textContent).toBe(text)
    expect(within(after).getByText('[原始字节 Base64]')).toBeVisible()
    expect(within(after).getByText(utf8.contentBase64, { exact: true })).toBeVisible()
    expect(screen.getByText(/生效模式：/)).toHaveTextContent('100755')
    expect(screen.getByText(/草稿模式：/)).toHaveTextContent('100644')
  })

  it.each([
    { operation: '新增' as const, before: null, after: side('added\n'), missing: '生效内容', present: '保存版本内容' },
    { operation: '删除' as const, before: side('removed\n'), after: null, missing: '保存版本内容', present: '生效内容' },
  ])('represents the missing side of $operation files', ({ operation, before, after, missing, present }) => {
    renderChange({ path: 'file.txt', operation, before, after })
    expect(within(screen.getByRole('region', { name: missing })).getByText('（不存在）')).toBeVisible()
    expect(screen.getByRole('region', { name: present }).querySelector('code')?.textContent).toBe(operation === '新增' ? 'added\n' : 'removed\n')
    expect(screen.getByText(/生效模式：/)).toHaveTextContent('无')
  })

  it('shows the empty state and forwards both checkbox transitions', () => {
    const onReviewedChange = vi.fn()
    const view = render(<DeploySnapshotReview changes={[]} reviewed={false} onReviewedChange={onReviewedChange} />)
    expect(screen.getByText('与已捕获的当前生效 tag 文件完全一致。')).toBeVisible()
    expect(screen.queryByRole('region', { name: '生效内容' })).not.toBeInTheDocument()
    expect(screen.queryByRole('region', { name: '保存版本内容' })).not.toBeInTheDocument()
    const checkbox = screen.getByRole('checkbox', { name: /我已核对当前生效 tag/ })
    expect(checkbox).not.toBeChecked()
    fireEvent.click(checkbox)
    expect(onReviewedChange).toHaveBeenLastCalledWith(true)
    view.rerender(<DeploySnapshotReview changes={[]} reviewed onReviewedChange={onReviewedChange} />)
    expect(checkbox).toBeChecked()
    fireEvent.click(checkbox)
    expect(onReviewedChange).toHaveBeenLastCalledWith(false)
    expect(onReviewedChange).toHaveBeenCalledTimes(2)
  })
})

describe('large review contents', () => {
  it('keeps many plain YAML lines lossless without one DOM node per line', () => {
    const text = '\n'.repeat(100_000) + 'name: "last"\n'
    renderChange({ path: 'workflow.yaml', operation: '更新', before: null, after: side(text) })
    const scroller = screen.getByRole('region', { name: '保存版本内容代码内容' })
    const code = scroller.querySelector('code')!
    expect(scroller).toHaveAttribute('tabindex', '0')
    expect(code.textContent).toBe(text)
    expect(code.childNodes.length).toBeLessThan(10)
    expect(within(screen.getByRole('region', { name: '保存版本内容' })).getByText(side(text).contentBase64)).toBeInTheDocument()
  })
})
