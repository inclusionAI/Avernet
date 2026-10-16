import { render, screen } from '@testing-library/react'
import { expect, it } from 'vitest'
import type { IssuePriority } from '@avernet/clawevolve/server/services/evolution/issue-priority'
import IssuePrioritySummary from '../IssuePrioritySummary'

it.each([['critical', '严重', 'bg-destructive'], ['high', '高', 'bg-warning-foreground'], ['medium', '中', 'bg-warning'],
  ['low', '低', 'bg-primary'], [null, '待评估', 'bg-muted']] as const)('labels %s with a semantic color and distinguishes completion from coverage', (severity, label, token) => {
  const window = { affected: 1, related: 9, analyzed: 3, complete: 1, legacyDiagnosed: 2, sampleHitRate: 1, occurrenceRate: null }
  render(<IssuePrioritySummary priority={{ severity, severitySources: [], nodes: ['node-1'], day: window, week: window,
    previousWeek: window, trend: null, lastOccurredAtMs: null, missingRunTimes: 0, sortReason: 'priority' } as IssuePriority} />)
  expect(screen.getByLabelText(`影响等级：${label}`)).toHaveClass(token)
  expect(screen.getAllByText(/完成分析 3.*可判定 1.*旧诊断证据 2/)).toHaveLength(2)
  expect(screen.getAllByText(/可判定样本命中率 100.0%/)).toHaveLength(2)
  expect(screen.queryByText(/发生率 100.0%/)).not.toBeInTheDocument()
})
