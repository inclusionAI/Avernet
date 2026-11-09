// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { stringify } from 'yaml'
import WorkflowBaselineComparison from '../WorkflowBaselineComparison'
import { createWorkflowReconciliationOwner } from '../../api/workflowSnapshotReconciliation'
import type { ReconciliationContext } from '../../api/workflowSnapshotReconciliation'

const db = { id: 'wf', title: 'database', version: '1', nodes: [{ id: 'start', title: 'Start', executor: { type: 'done' } }] }
const git = { ...db, title: 'git' }
const base64 = (text: string) => Buffer.from(text, 'utf8').toString('base64')
function fixture(gitYaml = stringify(git)) {
  const file = { path: 'workflow.yaml', mode: '100644' as const, contentBase64: base64(gitYaml) }
  const context: ReconciliationContext = { workflowId: 'wf', packId: 'pack', sourceVersion: 'draft', sourceCommit: 'a'.repeat(40),
    expectedSavedHead: 'a'.repeat(40), expectedDraftToken: 'b'.repeat(64), workflowPath: 'workflow.yaml',
    dbSpecJson: JSON.stringify(db), candidateSpecJson: JSON.stringify({ ...db, title: 'local edit' }), gitYaml,
    filesBefore: [file], filesAfter: [file], changes: [], revision: '1' }
  return { context, review: createWorkflowReconciliationOwner().open(context) }
}
afterEach(cleanup)

it('defaults to the database/editor candidate and never writes on comparison', () => {
  const { context, review } = fixture(), database = vi.fn(), gitSelected = vi.fn()
  render(<WorkflowBaselineComparison review={review} current={context} onUseDatabase={database} onUseGit={gitSelected} onCancel={() => {}} />)
  expect(screen.getByRole('radio', { name: /使用数据库工作流/ })).toBeChecked()
  expect(within(screen.getByRole('region', { name: '数据库工作流 + 当前编辑内容' })).getByText(/local edit/)).toBeVisible()
  expect(within(screen.getByRole('region', { name: 'Git 原始 YAML内容' })).getByText(/title: git/)).toBeVisible()
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
