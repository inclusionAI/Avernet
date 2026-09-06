import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  overview: vi.fn(),
  trend: vi.fn(),
  chartOptions: [] as Array<Record<string, unknown>>,
}))

vi.mock('../../../api/insight', () => ({ insightApi: mocks }))
vi.mock('echarts-for-react', () => ({
  default: ({ option }: { option: Record<string, unknown> }) => {
    mocks.chartOptions.push(option)
    return <div data-testid="insight-chart" />
  },
}))

import InsightOverview from '../InsightOverview'

type TrendPoint = {
  date: string
  totalTaskCount: number
  validTaskCount: number
  completeTaskCount: number
  capabilityTaskCount: number
  capabilityCompleteTaskCount: number
  autoCompleteTaskCount: number
  completionRate: number
  capabilityCompletionRate: number
  autoCompletionRate: number
}

function optionSeries(option: Record<string, unknown>) {
  return option.series as Array<{ name: string; data?: unknown[] }>
}

const points: TrendPoint[] = [
  { date: '20260803', totalTaskCount: 10, validTaskCount: 10, completeTaskCount: 5, capabilityTaskCount: 10, capabilityCompleteTaskCount: 8, autoCompleteTaskCount: 7, completionRate: 0.5, capabilityCompletionRate: 0.8, autoCompletionRate: 0.7 },
  { date: '20260804', totalTaskCount: 20, validTaskCount: 20, completeTaskCount: 15, capabilityTaskCount: 20, capabilityCompleteTaskCount: 12, autoCompleteTaskCount: 14, completionRate: 0.75, capabilityCompletionRate: 0.6, autoCompletionRate: 0.7 },
  { date: '20260810', totalTaskCount: 10, validTaskCount: 10, completeTaskCount: 10, capabilityTaskCount: 10, capabilityCompleteTaskCount: 10, autoCompleteTaskCount: 10, completionRate: 1, capabilityCompletionRate: 1, autoCompletionRate: 1 },
]

beforeEach(() => {
  mocks.chartOptions.length = 0
  mocks.overview.mockResolvedValue({
    contractVersion: 'insight-serving/v1', dataAsOf: '2026-08-11T00:00:00Z', sourceBatchId: 'test',
    scope: { userId: 'owner-a', botId: null },
    counts: { totalTaskCount: 40, validTaskCount: 40, completeTaskCount: 30, capabilityTaskCount: 40, capabilityCompleteTaskCount: 30, autoCompleteTaskCount: 31 },
    rates: { completionRate: 0.75, capabilityCompletionRate: 0.75, autoCompletionRate: 0.775 }, failureDistribution: [], botComparison: [],
  })
  mocks.trend.mockResolvedValue({
    contractVersion: 'insight-serving/v1', dataAsOf: '2026-08-11T00:00:00Z', sourceBatchId: 'test',
    scope: { userId: 'owner-a', botId: null }, points, governanceEvents: [],
  })
})

describe('InsightOverview trend granularity', () => {
  it('shows the error trend to non-admin users and aggregates counts by week', async () => {
    const user = userEvent.setup()
    render(<MemoryRouter><InsightOverview isAdmin={false} scope={{}} botOptions={[]} onScopeChange={() => undefined} onFailureDrilldown={() => undefined} /></MemoryRouter>)

    expect(await screen.findByText('错误任务趋势')).toBeInTheDocument()
    const dailyFailureOption = mocks.chartOptions.at(-1)!
    expect(optionSeries(dailyFailureOption).map((series) => series.name)).toEqual(['失败任务数量', '能力失败数量'])

    await user.click(screen.getByRole('button', { name: '按周' }))
    await waitFor(() => expect(mocks.chartOptions.at(-1)?.xAxis).toEqual(expect.objectContaining({ data: ['20260803', '20260810'] })))

    const weeklyFailureOption = mocks.chartOptions.at(-1)!
    expect(optionSeries(weeklyFailureOption).find((series) => series.name === '失败任务数量')?.data).toEqual([10, 0])
    expect(optionSeries(weeklyFailureOption).find((series) => series.name === '能力失败数量')?.data).toEqual([10, 0])

    const weeklyRateOption = mocks.chartOptions.find((option) => {
      const series = optionSeries(option)
      return series.some((item) => item.name === '完成率') && (option.xAxis as { data?: string[] })?.data?.length === 2
    })
    expect(weeklyRateOption).toBeDefined()
    expect(optionSeries(weeklyRateOption!).find((series) => series.name === '完成率')?.data).toEqual([62.5, 100])
    expect(optionSeries(weeklyRateOption!).map((series) => series.name)).toContain('完成率波动范围')
  })
})
