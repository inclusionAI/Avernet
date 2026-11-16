// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { stringify } from 'yaml'
import WorkflowBaselineComparison from '../WorkflowBaselineComparison'
import { createWorkflowReconciliationOwner } from '../../api/workflowSnapshotReconciliation'
import type { ReconciliationContext } from '../../api/workflowSnapshotReconciliation'

const db = { id: 'wf', title: 'database', version: '1', nodes: [{ id: 'start', title: 'Start', executor: { type: 'done' } }] }
const git = { ...db, title: 'git' }
const base64 = (text: string) => Buffer.from(text, 'utf8').toString('base64')
function fixture(gitYaml = stringify(git), candidateSpecJson = JSON.stringify({ ...db, title: 'local edit' })) {
  const file = { path: 'workflow.yaml', mode: '100644' as const, contentBase64: base64(gitYaml) }
  const context: ReconciliationContext = { workflowId: 'wf', packId: 'pack', sourceVersion: 'draft', sourceCommit: 'a'.repeat(40),
    expectedSavedHead: 'a'.repeat(40), expectedDraftToken: 'b'.repeat(64), workflowPath: 'workflow.yaml',
    dbSpecJson: JSON.stringify(db), candidateSpecJson, gitYaml,
    filesBefore: [file], filesAfter: [file], changes: [], revision: '1' }
  return { context, review: createWorkflowReconciliationOwner().open(context) }
}
afterEach(() => { cleanup(); vi.restoreAllMocks() })

function expectTitleHighlight(container: HTMLElement, title: string) {
  const key = Array.from(container.querySelectorAll('[data-syntax-kind="key"]')).find(token => token.textContent === 'title')
  const value = Array.from(container.querySelectorAll('[data-syntax-kind="string"]')).find(token => token.textContent?.includes(title))
  expect(key).toHaveClass('text-primary')
  expect(value).toHaveClass('text-success')
}

function expectDiffTextAndLineNumbers(container: HTMLElement, text: string) {
  const numbers = Array.from(container.querySelectorAll<HTMLElement>('[aria-hidden="true"]')).filter(number => number.textContent)
  expect(numbers.map(number => number.textContent)).toEqual(text.split('\n').map((_, index) => String(index + 1)))
  expect(numbers.map(number => number.nextElementSibling?.textContent).join('\n')).toBe(text)
}

it('defaults to the database/editor candidate and never writes on comparison', () => {
  const { context, review } = fixture(), database = vi.fn(), gitSelected = vi.fn()
  render(<WorkflowBaselineComparison review={review} current={context} onUseDatabase={database} onUseGit={gitSelected} onCancel={() => {}} />)
  expect(screen.getByRole('radio', { name: /使用数据库工作流/ })).toBeChecked()
  expect(screen.getByRole('region', { name: '数据库工作流 + 当前编辑内容' })).toHaveTextContent('local edit')
  expect(screen.getByRole('region', { name: 'Git 原始 YAML内容' })).toHaveTextContent('title: git')
  fireEvent.click(screen.getByRole('button', { name: '保留当前内容' }))
  expect(database).toHaveBeenCalledOnce(); expect(gitSelected).not.toHaveBeenCalled()
})

it('requires explicit confirmation before applying Git, and never applies on cancel', () => {
  const { context, review } = fixture(), gitSelected = vi.fn()
  render(<WorkflowBaselineComparison review={review} current={context} onUseDatabase={() => {}} onUseGit={gitSelected} onCancel={() => {}} />)
  fireEvent.click(screen.getByRole('radio', { name: /使用 Git YAML/ }))
  fireEvent.click(screen.getByRole('button', { name: '使用 Git 内容' }))
  expect(screen.getByRole('alertdialog', { name: '确认替换当前编辑' })).toBeVisible()
  fireEvent.click(screen.getByRole('button', { name: '返回' }))
  expect(gitSelected).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: '使用 Git 内容' }))
  fireEvent.click(screen.getByRole('button', { name: '确认替换' }))
  expect(gitSelected).toHaveBeenCalledWith(git)
})

it.each(['id: [unterminated', 'id: other\ntitle: test\nnodes: []', 'id: wf\ntitle: test\nnodes: []\nn: 9007199254740993', 'id: wf\ntitle: !custom test\nnodes: []'])('blocks only Git choice for untrusted YAML %s', text => {
  const { context, review } = fixture(text), database = vi.fn()
  render(<WorkflowBaselineComparison review={review} current={context} onUseDatabase={database} onUseGit={() => {}} onCancel={() => {}} />)
  expect(screen.getByRole('radio', { name: /使用 Git YAML/ })).toBeDisabled()
  expect(screen.getByText(/Git YAML 无法安全解析/)).toBeVisible()
  fireEvent.click(screen.getByRole('button', { name: '保留当前内容' }))
  expect(database).toHaveBeenCalledOnce()
})

it('invalidates a comparison when its baseline changes before the choice', () => {
  const { context, review } = fixture(), selected = vi.fn()
  const view = render(<WorkflowBaselineComparison review={review} current={context} onUseDatabase={selected} onUseGit={selected} onCancel={() => {}} />)
  view.rerender(<WorkflowBaselineComparison review={review} current={{ ...context, revision: '2' }} onUseDatabase={selected} onUseGit={selected} onCancel={() => {}} />)
  expect(screen.getByRole('button', { name: '保留当前内容' })).toBeDisabled()
  expect(screen.getByRole('alert')).toHaveTextContent('已变化')
  expect(selected).not.toHaveBeenCalled()
})

it.each(['\u202e', '\u200b', '\u00ad'])('escapes invisible Git character %j and prevents Git import', char => {
  const text = stringify({ ...git, title: `A${char}B` })
  const { context, review } = fixture(text)
  render(<WorkflowBaselineComparison review={review} current={context} onUseDatabase={() => {}} onUseGit={() => {}} onCancel={() => {}} />)
  expect(screen.getByRole('radio', { name: /使用 Git YAML/ })).toBeDisabled()
  expect(screen.getByText(/不可安全显示的字符已转义/)).toBeVisible()
  expect(screen.getByRole('region', { name: 'Git 原始 YAML内容' })).toHaveTextContent(`\\u{${char.codePointAt(0)!.toString(16)}}`)
})

it('allows read-only comparison with pending form edits but prevents discarding them via Git', () => {
  const { context, review } = fixture(), selected = vi.fn()
  render(<WorkflowBaselineComparison review={review} current={context} hasPendingEdits onUseDatabase={selected} onUseGit={selected} onCancel={() => {}} />)
  expect(screen.getByRole('region', { name: 'Git 原始 YAML内容' })).toBeVisible()
  expect(screen.getByRole('radio', { name: /使用 Git YAML/ })).toBeDisabled()
  expect(screen.getByText(/存在未应用的配置草稿/)).toBeVisible()
  fireEvent.click(screen.getByRole('button', { name: '保留当前内容' }))
  expect(selected).toHaveBeenCalledOnce()
})

it('moves focus to the replacement confirmation and keeps keyboard focus within it', () => {
  const { context, review } = fixture()
  render(<WorkflowBaselineComparison review={review} current={context} onUseDatabase={() => {}} onUseGit={() => {}} onCancel={() => {}} />)
  fireEvent.click(screen.getByRole('radio', { name: /使用 Git YAML/ }))
  fireEvent.click(screen.getByRole('button', { name: '使用 Git 内容' }))
  const back = screen.getByRole('button', { name: '返回' }), confirm = screen.getByRole('button', { name: '确认替换' })
  expect(back).toHaveFocus()
  fireEvent.keyDown(back, { key: 'Tab', shiftKey: true })
  expect(confirm).toHaveFocus()
  fireEvent.keyDown(confirm, { key: 'Tab' })
  expect(back).toHaveFocus()
})

it.each(['\\u202e', '\\u200b'])('rejects escaped invisible character %s after YAML parsing', escaped => {
  const text = `id: wf\ntitle: "normal${escaped}text"\nnodes:\n  - id: start\n    title: Start\n    executor:\n      type: done\n`
  const { context, review } = fixture(text)
  render(<WorkflowBaselineComparison review={review} current={context} onUseDatabase={() => {}} onUseGit={() => {}} onCancel={() => {}} />)
  expect(screen.getByRole('radio', { name: /使用 Git YAML/ })).toBeDisabled()
  expect(screen.getByText(/Git YAML 无法安全解析/)).toBeVisible()
})

it('highlights keys and strings on both diff sides without losing change backgrounds, line numbers, or literal HTML', () => {
  const html = '<img src=x onerror="window.alert(1)"><script>window.alert(2)</script>'
  const candidate = { ...db, title: 'database: "quoted"', description: html }
  const leftText = stringify(candidate, { lineWidth: 0 })
  const rightText = `# Git comment stays verbatim
id: wf
title: "git: quoted"

description: '${html}'
nodes: []
`
  const { context, review } = fixture(rightText, JSON.stringify(candidate))
  const alert = vi.spyOn(window, 'alert').mockImplementation(() => {})
  const { container } = render(<WorkflowBaselineComparison review={review} current={context} onUseDatabase={() => {}} onUseGit={() => {}} onCancel={() => {}} />)
  const left = screen.getByRole('region', { name: '数据库工作流 + 当前编辑内容' })
  const right = screen.getByRole('region', { name: 'Git 原始 YAML内容' })

  expect(container.querySelector('pre')).toBeNull()
  expectDiffTextAndLineNumbers(left, leftText)
  expectDiffTextAndLineNumbers(right, rightText)
  for (const [region, text, background] of [[left, leftText, 'bg-destructive/10'], [right, rightText, 'bg-success/10']] as const) {
    const title = text.split('\n').find(line => line.startsWith('title:'))!
    const titleCell = Array.from(region.querySelectorAll('.whitespace-pre')).find(cell => cell.textContent === title)
    expect(titleCell?.parentElement).toHaveClass(background)
    expect(titleCell?.previousElementSibling).toHaveTextContent(String(text.split('\n').indexOf(title) + 1))
    expect(region).toHaveTextContent(html)
  }
  expect(container.querySelector('img, script, [onerror]')).toBeNull()
  expect(alert).not.toHaveBeenCalled()
  expectTitleHighlight(left, 'database: "quoted"')
  expectTitleHighlight(right, 'git: quoted')
})

it('highlights both full-document fallbacks above the LCS limit while preserving the tail, comments, blank lines, quotes, and HTML', () => {
  const html = '<img src=x onerror="window.alert(1)"><script>window.alert(2)</script>'
  const candidate = { ...db, title: 'database: "quoted"',
    nodes: Array.from({ length: 130 }, (_, index) => ({ id: `node-${index}`, title: 'Start', executor: { type: 'done' } })),
    description: `# database note

"quoted text"
${html}
DATABASE-END
` }
  const leftText = stringify(candidate, { lineWidth: 0 })
  const rightText = [
    '# Git comment stays verbatim', 'id: wf', 'title: "git: quoted"', '', 'nodes: []',
    ...Array.from({ length: 510 }, (_, index) => `# unchanged comment ${index}`),
    '', `description: '${html}'`, '# trailing "quoted" comment', '', 'tail: "GIT-END"', '', '',
  ].join('\n')
  expect(leftText.split('\n').length * rightText.split('\n').length).toBeGreaterThan(250_000)
  const { context, review } = fixture(rightText, JSON.stringify(candidate))
  const alert = vi.spyOn(window, 'alert').mockImplementation(() => {})
  const { container } = render(<WorkflowBaselineComparison review={review} current={context} onUseDatabase={() => {}} onUseGit={() => {}} onCancel={() => {}} />)
  const left = screen.getByRole('region', { name: '数据库工作流 + 当前编辑内容' })
  const right = screen.getByRole('region', { name: 'Git 原始 YAML内容' })

  expect(screen.getByText('文档较大，展示完整原文，不计算行级差异。')).toBeVisible()
  expect(container.querySelectorAll('pre')).toHaveLength(2)
  expect(left.querySelector('pre')?.textContent).toBe(leftText)
  expect(right.querySelector('pre')?.textContent).toBe(rightText)
  expect(left.textContent).toContain('DATABASE-END\n')
  expect(right.textContent).toContain('tail: "GIT-END"\n\n')
  for (const region of [left, right]) {
    expect(region.querySelector('pre')).toHaveClass('whitespace-pre')
    expect(region.querySelector('[aria-hidden="true"]')).toBeNull()
    expect(region.textContent).toContain(html)
  }
  expect(container.querySelector('img, script, [onerror]')).toBeNull()
  expect(alert).not.toHaveBeenCalled()
  expectTitleHighlight(left, 'database: "quoted"')
  expectTitleHighlight(right, 'git: quoted')
  expect(right.querySelector('[data-syntax-kind="comment"]')).toHaveClass('text-muted-foreground')
})
