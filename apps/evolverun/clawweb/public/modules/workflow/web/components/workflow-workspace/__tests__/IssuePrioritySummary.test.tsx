import { fireEvent, render, screen } from '@testing-library/react'
import { expect, it } from 'vitest'
import type { IssuePriority } from '@avernet/clawevolve/server/services/evolution/issue-priority'
import IssuePrioritySummary from '../IssuePrioritySummary'
const window = { affected: 8, related: 363, analyzed: 25, complete: 0, legacyDiagnosed: 2, sampleHits: 0, sampleHitRate: null, occurrenceRate: null }
const priority = { severity: 'critical', severitySources: ['diagnosis:1'], nodes: ['query'], day: window,
  week: { ...window, affected: 33, related: 2399, analyzed: 120 }, previousWeek: window,
  trend: null, lastOccurredAtMs: null, missingRunTimes: 4, sortReason: 'recent diagnosis' } as IssuePriority
it('keeps decisions visible and evidence collapsed until requested', () => {
  render(<IssuePrioritySummary priority={priority} />)
  expect(screen.getByText(/近24小时发现 8 次/)).toBeVisible()
  expect(screen.getByText(/近7天发现 33 次/)).toBeVisible()
  expect(screen.getByText('分析覆盖不足，暂不能判断发生率')).toBeVisible()
  expect(screen.queryByText(/暂无可靠对照/)).not.toBeInTheDocument()
  expect(screen.getByText(/运行时间未知/)).not.toBeVisible()
  expect(screen.getByText('统计详情').closest('details')).not.toHaveAttribute('open')
  expect(screen.getAllByText(/完成分析 25/).every(el => !el.closest('details')!.open)).toBe(true)
  expect(screen.getAllByText(/可判定命中 0/).every(el => !el.closest('details')!.open)).toBe(true)
  fireEvent.click(screen.getByText('统计详情'))
  expect(screen.getAllByText(/完成分析 25/).every(el => el.closest('details')!.open)).toBe(true)
  expect(screen.getByText(/完成分析 120/)).toBeVisible()
  expect(screen.getByText(/4 个历史关联运行缺少时间/)).toBeVisible()
  expect(screen.getByText(/严重性来源：diagnosis:1/)).toBeVisible()
})
it('preserves unknown coverage rather than fabricating zeros', () => {
  render(<IssuePrioritySummary priority={{ ...priority, day: { ...window, complete: undefined, legacyDiagnosed: undefined, sampleHits: undefined } }} />)
  expect(screen.getByText(/可判定 待统计/)).toBeInTheDocument()
  expect(screen.getByText(/可判定命中 待统计/)).toBeInTheDocument()
})
it('shows a supported population rate and trend', () => {
  const complete = { ...window, analyzed: 363, complete: 363, occurrenceRate: 8 / 363 }
  render(<IssuePrioritySummary priority={{ ...priority, day: complete, week: complete, trend: 'improving' }} />)
  expect(screen.getByText(/改善/)).toBeVisible()
  expect(screen.getByText(/近7天发生率 2.2%/)).toBeVisible()
  expect(screen.queryByText('分析覆盖不足，暂不能判断发生率')).not.toBeInTheDocument()
})
it.each([['critical', '严重', 'bg-destructive'], ['high', '高', 'bg-warning-foreground'], ['medium', '中', 'bg-warning'],
  ['low', '低', 'bg-primary'], [null, '待评估', 'bg-muted']] as const)('preserves %s severity', (severity, label, token) => {
  render(<IssuePrioritySummary priority={{ ...priority, severity }} />)
  expect(screen.getByLabelText(`影响等级：${label}`)).toHaveClass(token)
})
