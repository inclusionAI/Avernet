// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { stringify } from 'yaml'
import ActiveHistoryMismatchReview from '../ActiveHistoryMismatchReview'
import { DiffPanes, WorkflowBaselineNotice } from '../WorkflowBaselineComparison'
import { unifiedDiff } from '../WorkflowVersionDiff'
import { sideBySideDiff } from '../workflow-version-diff/sideBySide'

const literalHtml = '<img src=x onerror="window.alert(1)"><script>window.alert(2)</script> & <b>literal</b>'
const leftLabel = '发布记录 #21（只读）'
const rightLabel = '候选 Git YAML（只读）'

afterEach(() => { cleanup(); vi.restoreAllMocks() })

function yamlText(side: 'database' | 'git', extraLines = 0): string {
  return [
    '# Keep "quotes", <tags> and comments',
    'id: flow',
    `title: "${side}: quoted"`,
    '',
    `description: '${literalHtml}'`,
    'enabled: true',
    'count: 7',
    ...Array.from({ length: extraLines }, (_, index) => `# ${side} comment ${index}`),
    `${side}_only: true`,
    ...(side === 'git' ? ['extra_git_only: true'] : []),
    `tail: "${side.toUpperCase()}-END"`,
    '',
    '',
  ].join('\n')
}

function expectToken(region: HTMLElement, kind: string, text: string, className: string) {
  const highlightedSpan = Array.from(region.querySelectorAll(`[data-syntax-kind="${kind}"]`))
    .find(node => node.textContent === text)
  expect(highlightedSpan).toHaveClass(className)
}

function expectYamlHighlight(region: HTMLElement, side: 'database' | 'git') {
  expectToken(region, 'key', 'title', 'text-primary')
  expectToken(region, 'string', `"${side}: quoted"`, 'text-success')
}

function expectNumberedText(region: HTMLElement, text: string) {
  const numbers = Array.from(region.querySelectorAll('[aria-hidden="true"]'))
    .filter(node => node.textContent !== '')
  expect(numbers.map(node => node.textContent)).toEqual(text.split('\n').map((_, index) => String(index + 1)))
  expect(numbers.map(node => node.nextElementSibling?.textContent).join('\n')).toBe(text)
  return numbers.map(node => node.nextElementSibling)
}

function expectLiteralHtml(region: HTMLElement) {
  expect(region.textContent).toContain(literalHtml)
  expect(region.querySelector('img, script, b, [onerror]')).toBeNull()
}

describe('shared DiffPanes highlighting after baseline comparison extraction', () => {
  it('honors custom labels and highlights both numbered diff sides without changing text or change backgrounds', () => {
    const left = yamlText('database'), right = yamlText('git')
    const rows = sideBySideDiff(unifiedDiff(left, right))
    expect(rows.some(row => row.left === null)).toBe(true)
    render(<DiffPanes rows={rows} left={left} right={right} leftLabel={leftLabel} rightLabel={rightLabel} />)

    for (const [label, text, side, background] of [
      [leftLabel, left, 'database', 'bg-destructive/10'],
      [rightLabel, right, 'git', 'bg-success/10'],
    ] as const) {
      const pane = screen.getByRole('region', { name: label })
      expect(within(pane).getByText(label, { exact: true })).toBeVisible()
      const region = within(pane).getByRole('region', { name: `${label}内容` })
      expect(region).toHaveAttribute('tabindex', '0')
      expect(region.querySelector('pre')).toBeNull()
      const cells = expectNumberedText(region, text)
      const title = cells.find(cell => cell?.textContent === `title: "${side}: quoted"`)
      expect(title?.parentElement).toHaveClass(background)
      expect(title).toHaveClass('whitespace-pre')
      expectYamlHighlight(region, side)
      expectToken(region, 'comment', '# Keep "quotes", <tags> and comments', 'text-muted-foreground')
      expectLiteralHtml(region)
    }
  })

  it('highlights both rows=null full-document panes through their tails while preserving whitespace and literal HTML', () => {
    const left = yamlText('database', 510), right = yamlText('git', 510)
    expect(left.split('\n').length * right.split('\n').length).toBeGreaterThan(250_000)
    render(<DiffPanes rows={null} left={left} right={right} leftLabel={leftLabel} rightLabel={rightLabel} />)

    for (const [label, text, side] of [[leftLabel, left, 'database'], [rightLabel, right, 'git']] as const) {
      expect(screen.getByText(label, { exact: true })).toBeVisible()
      const region = screen.getByRole('region', { name: `${label}内容` })
      expect(region.querySelector('pre')).toHaveClass('whitespace-pre')
      expect(region.querySelector('pre')?.textContent).toBe(text)
      expect(region.querySelector('[aria-hidden="true"]')).toBeNull()
      expectYamlHighlight(region, side)
      expectToken(region, 'key', 'tail', 'text-primary')
      expectToken(region, 'string', `"${side.toUpperCase()}-END"`, 'text-success')
      expectToken(region, 'comment', `# ${side} comment 509`, 'text-muted-foreground')
      expectLiteralHtml(region)
    }
  })

})

describe('ActiveHistoryMismatchReview shared diff integration', () => {
  it.each([
    { mode: 'line diff', extraLines: 0 },
    { mode: 'large-document fallback', extraLines: 510 },
  ])('opens highlighted, safely escaped database and Git panes via the upstream caller ($mode)', ({ extraLines }) => {
    const database = {
      id: 'flow', title: 'database: quoted', description: literalHtml, note: 'A\u202eB', nodes: [],
      ...Object.fromEntries(Array.from({ length: extraLines }, (_, index) => [`entry_${index}`, index])),
      tail: 'DATABASE-END',
    }
    const gitYaml = `note: "A\u200bB"\n${yamlText('git', extraLines)}`
    const leftText = stringify(database, { lineWidth: 0 }).replaceAll('\u202e', '\\u{202e}')
    const rightText = gitYaml.replaceAll('\u200b', '\\u{200b}')
    const large = extraLines > 0
    expect(leftText.split('\n').length * rightText.split('\n').length > 250_000).toBe(large)
    const onChoose = vi.fn()
    render(<ActiveHistoryMismatchReview evidence={{ workflowId: 'flow', deployNumber: 21,
      gitPath: 'workflow.yaml', databaseText: JSON.stringify(database), gitYaml }}
      deployNumber={21} selected="database" onChoose={onChoose} />)

    const notice = screen.getByRole('status')
    expect(notice).toHaveTextContent('生效 #21的数据库发布记录与 Git 文件存在差异')
    expect(notice).toHaveTextContent('默认使用数据库发布记录作为对比基线；不影响后续新版本部署。当前以数据库发布记录为准。')
    fireEvent.click(within(notice).getByRole('button', { name: '查看差异' }))
    const dialog = screen.getByRole('dialog', { name: '对比生效数据库记录和 Git 工作流' })
    expect(within(dialog).getByText('不可安全显示的字符已转义显示；选择仅用于展示。')).toBeVisible()
    expect(within(dialog).queryByText('文档较大，展示完整原文，不计算行级差异。') !== null).toBe(large)

    for (const [label, text, side, escaped] of [
      ['数据库发布记录 #21内容', leftText, 'database', '\\u{202e}'],
      ['Git 原始 YAML内容', rightText, 'git', '\\u{200b}'],
    ] as const) {
      const region = within(dialog).getByRole('region', { name: label })
      if (large) {
        expect(region.querySelector('pre')?.textContent).toBe(text)
        expectToken(region, 'key', 'tail', 'text-primary')
      } else {
        expect(region.querySelector('pre')).toBeNull()
        expectNumberedText(region, text)
      }
      expectYamlHighlight(region, side)
      expect(region.textContent).toContain(escaped)
      expect(region.textContent).not.toMatch(/[\u202e\u200b]/u)
      expectLiteralHtml(region)
    }
    expect(onChoose).not.toHaveBeenCalled()
  })
})

describe('WorkflowBaselineNotice custom copy', () => {
  it('preserves title and description overrides across comparison states and while busy', () => {
    const title = '生效 #21：自定义对比提示'
    const description = '仅切换显示基线；不修改候选工作流。'
    const onCompare = vi.fn()
    const view = render(<WorkflowBaselineNotice comparison="different" canCompare busy={false}
      onCompare={onCompare} title={title} description={description} />)
    const notice = screen.getByRole('status')
    expect(within(notice).getByText(title, { exact: true })).toBeVisible()
    expect(within(notice).getByText(description, { exact: true })).toBeVisible()
    expect(notice).not.toHaveTextContent('默认使用数据库工作流及当前编辑器修改')
    fireEvent.click(within(notice).getByRole('button', { name: '查看差异' }))
    expect(onCompare).toHaveBeenCalledOnce()

    view.rerender(<WorkflowBaselineNotice comparison="uncomparable" canCompare busy
      onCompare={onCompare} title={title} description={description} />)
    expect(screen.getByText(title, { exact: true })).toBeVisible()
    expect(screen.getByText(description, { exact: true })).toBeVisible()
    expect(screen.getByRole('button', { name: '查看差异' })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: '查看差异' }))
    expect(onCompare).toHaveBeenCalledOnce()
  })
})
