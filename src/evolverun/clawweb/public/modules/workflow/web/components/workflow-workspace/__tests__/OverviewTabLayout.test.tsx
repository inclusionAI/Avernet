import { render, screen, within, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  useWorkflowHealth: vi.fn(),
  useFlowRuns: vi.fn(),
}))

vi.mock('../../../api/hooks', () => ({
  useWorkflowHealth: mocks.useWorkflowHealth,
  useWorkflowHealthTrend: () => ({ data: [] }),
  useFlowRuns: mocks.useFlowRuns,
  useAnalysisProgress: () => ({ data: null, isError: false }),
}))
vi.mock('../../SuccessTrendCard', () => ({ SuccessTrendCard: ({ days }: { days?: number }) => <div>成功率趋势 · {days}天</div> }))
vi.mock('../../NodeAnalysisPanel', () => ({ default: () => <div>节点分析</div> }))

import OverviewTab from '../OverviewTab'

const health = {
    overallScore: 50,
    successRate: 78,
    nodeFailureRate: 2.5,
    p95DurationMs: 188000,
    retryRate: 1,
    bottleneckNode: 'report',
    fragileNode: 'search',
    recommendation: '优先处理 report 节点耗时',
}

function mockQueries() {
  mocks.useWorkflowHealth.mockReturnValue({ data: health })
  mocks.useFlowRuns.mockReturnValue({
    data: {
      runs: [],
      total: 28,
      statusCounts: { succeeded: 14, failed: 4, aborted: 1, cancelled: 1, running: 3, waiting: 2, blocked: 2, queued: 1 },
    },
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
    isFetching: false,
  })
}

const workflow = {
  workflow_id: 'tech-research',
  workflow_title: '技术调研',
  run_count: 28,
  last_status: 'succeeded',
  last_run_at: 1,
  updated_at: 1,
}

describe('task escort overview layout', () => {
  it('uses a consistent window, terminal success rate, abnormal endings and live-state breakdown', () => {
    mockQueries()
    render(<MemoryRouter><OverviewTab workflow={workflow} /></MemoryRouter>)

    const metrics = screen.getByRole('region', { name: '工作流关键指标' })
    expect(within(metrics).getByText('健康度')).toBeInTheDocument()
    expect(within(metrics).getByText('运行成功率')).toBeInTheDocument()
    expect(within(metrics).getByText('70%')).toBeInTheDocument()
    expect(within(metrics).getByText('异常结束')).toBeInTheDocument()
    expect(within(metrics).getByText('6')).toBeInTheDocument()
    expect(within(metrics).getByText('节点耗时 P95')).toBeInTheDocument()
    expect(screen.getByText('运行中 3')).toBeInTheDocument()
    expect(screen.getByText('等待中 2')).toBeInTheDocument()
    expect(screen.getByText('阻塞 2')).toBeInTheDocument()
    expect(screen.getByText('排队中 1')).toBeInTheDocument()
    expect(screen.getByText('成功率趋势 · 7天')).toBeInTheDocument()
    expect(screen.queryByText('SUCCESS RATE')).not.toBeInTheDocument()
    expect(mocks.useWorkflowHealth).toHaveBeenLastCalledWith('tech-research', 7)
    expect(mocks.useFlowRuns).toHaveBeenLastCalledWith(expect.objectContaining({
      workflowId: 'tech-research',
      limit: 20,
      offset: 0,
    }))
  })

  it('can switch the page window and paginate through all matching runs', async () => {
    mockQueries()
    render(<MemoryRouter><OverviewTab workflow={workflow} /></MemoryRouter>)

    await userEvent.click(screen.getByRole('button', { name: '30天' }))
    expect(mocks.useWorkflowHealth).toHaveBeenLastCalledWith('tech-research', 30)
    expect(mocks.useFlowRuns).toHaveBeenLastCalledWith(expect.objectContaining({ workflowId: 'tech-research', limit: 20, offset: 0 }))

    await userEvent.click(screen.getByRole('button', { name: '下一页' }))
    await waitFor(() => expect(mocks.useFlowRuns).toHaveBeenLastCalledWith(expect.objectContaining({
      workflowId: 'tech-research',
      limit: 20,
      offset: 20,
    })))
  })
})
