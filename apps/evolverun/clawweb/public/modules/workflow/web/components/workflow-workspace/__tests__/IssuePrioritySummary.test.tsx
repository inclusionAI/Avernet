import { render, screen } from '@testing-library/react'
import { expect, it } from 'vitest'
import type { IssuePriority } from '@avernet/clawevolve/server/services/evolution/issue-priority'
import IssuePrioritySummary from '../IssuePrioritySummary'
const window = { affected: 8, related: 363, analyzed: 25, complete: 0, legacyDiagnosed: 2, sampleHits: 0, sampleHitRate: null, occurrenceRate: null }
const priority = { severity: 'critical', severitySources: ['diagnosis:1'], nodes: ['query'], day: window,
  week: { ...window, affected: 33, related: 2399, analyzed: 120 }, previousWeek: window,
  trend: null, lastOccurredAtMs: null, missingRunTimes: 4, sortReason: 'recent diagnosis' } as IssuePriority
it('shows severity, observed counts and the latest run time without coverage statistics', () => {
  const at = new Date('2026-10-19T04:00:00Z').getTime()
  render(<IssuePrioritySummary priority={{ ...priority, lastOccurredAtMs: at, trend: 'improving' }} />)
  expect(screen.getByLabelText('影响等级：严重')).toBeVisible()
  expect(screen.getByText(/近24小时发现 8 次/)).toBeVisible()
  expect(screen.getByText(/近7天发现 33 次/)).toBeVisible()
  expect(screen.getByText(`最近发生：${new Date(at).toLocaleString()}`)).toBeVisible()
  expect(screen.queryByText(/统计详情|分析覆盖|发生率|样本|趋势|改善|排序依据|完成分析/)).not.toBeInTheDocument()
})
it('shows unknown run time explicitly', () => {
  render(<IssuePrioritySummary priority={priority} />)
  expect(screen.getByText('最近发生：运行时间未知')).toBeVisible()
})
it.each([['critical', '严重', 'bg-destructive'], ['high', '高', 'bg-warning-foreground'], ['medium', '中', 'bg-warning'],
  ['low', '低', 'bg-primary'], [null, '待评估', 'bg-muted']] as const)('preserves %s severity', (severity, label, token) => {
  render(<IssuePrioritySummary priority={{ ...priority, severity }} />)
  expect(screen.getByLabelText(`影响等级：${label}`)).toHaveClass(token)
})
