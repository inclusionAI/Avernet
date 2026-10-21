import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, it } from 'vitest'
import type { RepairRevision } from '../../../../../server/contracts/repair-batch'
import type { RepairTaskDetail } from '../../../../../server/contracts/repair-workbench'
import RepairCandidateReview from '../RepairCandidateReview'

function fixture(checks: Record<string, unknown>) {
  const revision = {
    revision: 10, phase: 'review',
    input: {
      baseline: { packCommit: 'a'.repeat(40), activeDeployNumber: 1 },
      items: [{ itemId: 'selected', proposal: { summary: '核对输入来源' }, instruction: '' }],
    },
    draft: { itemResults: [{ itemId: 'selected', status: 'changed', reason: '已生成输入配置修改' }] },
    checks, error: null,
  } as unknown as RepairRevision
  const detail = { taskId: 'review-test', latestAttempt: revision, capabilities: { diff: false } } as RepairTaskDetail
  return { detail, revision, active: false }
}

// Minimal shape of the contradictory revision 10 report; no original IDs or evidence are copied.
const contradictoryChecks = () => ({
  static: { status: 'passed' },
  mock: { status: 'not_covered', caseCount: 0, assertionCount: 0 },
  coverage: [{ itemId: 'selected', mode: 'mock', assertionCount: 1, caseNames: ['mock-compatibility'] }],
  cases: [],
})

it('does not present revision 10 coverage claims as executed mock assertions', () => {
  const props = fixture(contradictoryChecks())
  const originalChecks = structuredClone(props.revision.checks)
  render(<RepairCandidateReview {...props} />)
  expect(screen.getByText('验证覆盖：未验证')).toBeVisible()
  expect(screen.getByText(/Mock 检查：未覆盖/)).toBeVisible()
  expect(screen.queryByText('验证覆盖：Mock · 1 条断言')).not.toBeInTheDocument()
  expect(props.revision.checks).toEqual(originalChecks)
})

it.each([
  { name: 'mock explicitly not covered', mock: { status: 'not_covered', caseCount: 1, assertionCount: 1 }, cases: [{ name: 'mock-compatibility' }] },
  { name: 'no cases ran', mock: { status: 'passed', caseCount: 0, assertionCount: 1 }, cases: [{ name: 'mock-compatibility' }] },
  { name: 'no assertions ran', mock: { status: 'passed', caseCount: 1, assertionCount: 0 }, cases: [{ name: 'mock-compatibility' }] },
  { name: 'empty case evidence', mock: { status: 'passed', caseCount: 1, assertionCount: 1 }, cases: [] },
])('keeps a claimed per-item mock result unverified when $name', ({ mock, cases }) => {
  render(<RepairCandidateReview {...fixture({ ...contradictoryChecks(), mock, cases })} />)
  expect(screen.getByText('验证覆盖：未验证')).toBeVisible()
  expect(screen.queryByText('验证覆盖：Mock · 1 条断言')).not.toBeInTheDocument()
})

it('does not convert aggregate mock totals or unverified coverage records into per-item verification', () => {
  const props = fixture({
    mock: { status: 'passed', caseCount: 5, assertionCount: 20 },
    cases: [{ name: 'mock-compatibility' }],
    coverage: [{ itemId: 'selected', mode: 'mock', assertionCount: 1, caseNames: ['mock-compatibility'] }],
  })
  props.revision.input.items.push({ ...props.revision.input.items[0], itemId: 'other', proposal: { summary: '无逐项覆盖的建议' } })
  render(<RepairCandidateReview {...props} />)
  expect(screen.getByText('验证覆盖：未验证（仅有覆盖记录）')).toBeVisible()
  expect(screen.getByText('验证覆盖：未覆盖')).toBeVisible()
  expect(screen.queryByText(/验证覆盖：Mock/)).not.toBeInTheDocument()
})

it('keeps zero-assertion coverage uncovered even when aggregate mock checks passed', () => {
  render(<RepairCandidateReview {...fixture({
    mock: { status: 'passed', caseCount: 1, assertionCount: 20 },
    coverage: [{ itemId: 'selected', mode: 'none', assertionCount: 0, caseNames: [] }],
  })} />)
  expect(screen.getByText('验证覆盖：未覆盖')).toBeVisible()
})

it('uses the three outcome categories consistently and keeps their reasons', () => {
  const props = fixture(contradictoryChecks())
  props.revision.input.items.push(
    { ...props.revision.input.items[0], itemId: 'already', proposal: { summary: '已满足的建议' } },
    { ...props.revision.input.items[0], itemId: 'unknown', proposal: { summary: '证据不足的建议' } },
  )
  props.revision.draft!.itemResults = [
    { itemId: 'selected', status: 'changed', reason: '已生成输入配置修改' },
    { itemId: 'already', status: 'not_needed', reason: '现有配置已满足原始目标' },
    { itemId: 'unknown', status: 'unresolved', reason: '缺少实际输入样本，需要确认边界' },
  ]
  render(<RepairCandidateReview {...props} />)
  for (const label of ['已生成修改', '未采纳', '待确认']) {
    expect(screen.getByText(`处理结论：${label}`)).toBeVisible()
  }
  expect(screen.getByText('已生成修改 1 项 · 未采纳 1 项 · 待确认 1 项')).toBeVisible()
  expect(screen.getByText('现有配置已满足原始目标')).toBeVisible()
  expect(screen.getByText('缺少实际输入样本，需要确认边界')).toBeVisible()
})

it('does not classify goals as rejected when candidate generation failed technically', () => {
  const props = fixture(contradictoryChecks())
  props.revision.phase = 'failed'
  props.revision.draft = null
  props.revision.error = { code: 'AIS_FAILED', message: 'controlled execution failure' }
  render(<RepairCandidateReview {...props} />)
  expect(screen.getByText('所选版本 v10：生成失败')).toBeVisible()
  expect(screen.getByText('尚未返回逐项处理结果')).toBeVisible()
  expect(screen.queryByText(/未采纳/)).not.toBeInTheDocument()
})

it('preserves the original historical check report for inspection', async () => {
  render(<RepairCandidateReview {...fixture(contradictoryChecks())} />)
  await userEvent.click(screen.getByRole('button', { name: '检查详情' }))
  await userEvent.click(screen.getByText('完整检查报告', { selector: 'summary' }))
  expect(screen.getByText(/"caseNames": \[/)).toBeVisible()
  expect(screen.getByText(/"assertionCount": 1/)).toBeVisible()
})
