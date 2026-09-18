import { act, render, screen, within, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  useWorkflowHealth: vi.fn(),
  useFlowRuns: vi.fn(),
  analyze: { isPending: false, isError: false, error: null as Error | null, variables: undefined as { flowId: string } | undefined, mutate: vi.fn(), reset: vi.fn() },
}))

vi.mock('../../../api/hooks', () => ({
  useWorkflowHealth: mocks.useWorkflowHealth,
  useWorkflowHealthTrend: () => ({ data: [] }),
  useFlowRuns: mocks.useFlowRuns,
  useAnalysisProgress: () => ({ data: null, isError: false }),
  useAnalyzeRun: () => mocks.analyze,
  useEligibleBotsForAnalyze: () => ({ data: { bots: [{ botId: 'origin', botName: 'Origin Bot', env: 'prod' }] }, isLoading: false }),
}))
vi.mock('../../SuccessTrendCard', () => ({ SuccessTrendCard: ({ days, currentDetail }: { days?: number; currentDetail?: string }) => <div><span>成功率趋势 · {days}天</span><span>{currentDetail}</span></div> }))
vi.mock('../../NodeAnalysisPanel', () => ({ default: () => <div>节点分析</div> }))
vi.mock('@avernet/workflow/web/api/hooks', () => ({
  useEligibleBotsForAnalyze: () => ({ data: { bots: [{ botId: 'origin', botName: 'Origin Bot', env: 'prod' }] }, isLoading: false }),
}))
vi.mock('@avernet/clawweb-shared/web/api/hooks', () => ({
  useDeleteFlowRun: () => ({ mutate: vi.fn(), isPending: false }),
  useRerunFlowRun: () => ({ mutate: vi.fn(), isPending: false }),
  useRunArchive: () => ({ data: null, isLoading: false, isError: false, error: null }),
}))

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
  beforeEach(() => {
    sessionStorage.clear()
    Object.assign(mocks.analyze, { isPending: false, isError: false, error: null, variables: undefined })
    mocks.analyze.mutate.mockReset()
  })

  it('starts analysis from the row without navigating and blocks duplicate dispatch', async () => {
    mockQueries()
    const data = mocks.useFlowRuns().data
    data.runs = [{ flow_id: 'run-1', workflow_id: workflow.workflow_id, origin_bot_id: 'origin:owner', status: 'failed', node_count: 1, failed_count: 1, succeeded_count: 0 }]
    const view = render(<MemoryRouter><OverviewTab workflow={workflow} /></MemoryRouter>)
    await userEvent.click(screen.getByTitle('分析'))
    expect(screen.getByText('选择 Bot 分析运行')).toBeInTheDocument()
    expect(screen.getByText('发起 Bot')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '确认分析' }))
    expect(mocks.analyze.mutate).toHaveBeenCalledWith({ flowId: 'run-1', botId: 'origin', botEnv: 'prod' }, expect.any(Object))
    Object.assign(mocks.analyze, { isPending: true, variables: { flowId: 'run-1' } })
    view.rerender(<MemoryRouter><OverviewTab workflow={workflow} /></MemoryRouter>)
    expect(screen.getByTitle('分析')).toBeDisabled()
    Object.assign(mocks.analyze, { isPending: false, isError: true, error: new Error('Bot unavailable') })
    view.rerender(<MemoryRouter><OverviewTab workflow={workflow} /></MemoryRouter>)
    expect(screen.getByRole('alert')).toHaveTextContent('Bot unavailable')
    expect(screen.getByRole('button', { name: '确认分析' })).toBeEnabled()
    act(() => mocks.analyze.mutate.mock.calls[0][1].onSuccess())
    expect(screen.queryByText('选择 Bot 分析运行')).not.toBeInTheDocument()
    expect(screen.getByText('run-1')).toBeInTheDocument()
  })

  it('retains submitted filters and pagination on return, isolated by workflow', async () => {
    mockQueries()
    const view = render(<MemoryRouter><OverviewTab workflow={workflow} /></MemoryRouter>)
    await userEvent.selectOptions(screen.getByRole('combobox', { name: '运行状态' }), 'failed')
    await userEvent.type(screen.getByRole('searchbox'), 'keyword{Enter}')
    await userEvent.click(screen.getByRole('button', { name: '下一页' }))
    view.unmount()
    const returned = render(<MemoryRouter><OverviewTab workflow={workflow} /></MemoryRouter>)
    expect(screen.getByRole('searchbox')).toHaveValue('keyword')
    expect(screen.getByRole('combobox', { name: '运行状态' })).toHaveValue('failed')
    expect(mocks.useFlowRuns).toHaveBeenLastCalledWith(expect.objectContaining({ query: 'keyword', status: 'failed', offset: 20 }))
    returned.rerender(<MemoryRouter><OverviewTab workflow={{ ...workflow, workflow_id: 'another' }} /></MemoryRouter>)
    expect(screen.getByRole('searchbox')).toHaveValue('')
    expect(mocks.useFlowRuns).toHaveBeenLastCalledWith(expect.objectContaining({ query: undefined, offset: 0 }))
  })

  it.each(['completed', 'failed'])('offers reanalysis for %s analyses', async (status) => {
    mockQueries()
    mocks.useFlowRuns().data.runs = [{ flow_id: 'run-retry', workflow_id: workflow.workflow_id, status: 'failed', evolution_analysis_status: status, node_count: 1, failed_count: 1, succeeded_count: 0 }]
    render(<MemoryRouter><OverviewTab workflow={workflow} /></MemoryRouter>)
    await userEvent.click(screen.getByTitle('重新分析'))
    expect(screen.getByText('选择 Bot 分析运行')).toBeInTheDocument()
  })

  it('falls back safely when saved filters are invalid', () => {
    mockQueries()
    sessionStorage.setItem(`workflow-run-list:${workflow.workflow_id}`, '{broken')
    render(<MemoryRouter><OverviewTab workflow={workflow} /></MemoryRouter>)
    expect(screen.getByRole('searchbox')).toHaveValue('')
    expect(mocks.useFlowRuns).toHaveBeenLastCalledWith(expect.objectContaining({ offset: 0 }))
  })
  it('filters all history, resets pagination, and leaves metrics independent', async () => {
    mockQueries()
    render(<MemoryRouter><OverviewTab workflow={workflow} /></MemoryRouter>)
    await userEvent.click(screen.getByRole('button', { name: '下一页' }))
    await userEvent.selectOptions(screen.getByRole('combobox', { name: '运行状态' }), 'failed')
    expect(mocks.useFlowRuns).toHaveBeenLastCalledWith(expect.objectContaining({ status: 'failed', offset: 0 }))
    await userEvent.type(screen.getByRole('searchbox', { name: '搜索运行记录' }), '  gateway-client  ')
    await userEvent.click(screen.getByRole('button', { name: '搜索', exact: true }))
    expect(mocks.useFlowRuns).toHaveBeenLastCalledWith(expect.objectContaining({ query: 'gateway-client', status: 'failed', offset: 0 }))
    await userEvent.click(screen.getByRole('button', { name: '下一页' }))
    expect(mocks.useFlowRuns).toHaveBeenLastCalledWith(expect.objectContaining({ query: 'gateway-client', status: 'failed', offset: 20 }))
    const metricCalls = mocks.useFlowRuns.mock.calls.filter(([params]) => params?.limit === 1)
    expect(metricCalls.every(([params]) => !params.query && !params.status && !params.statuses)).toBe(true)
    expect(screen.getByText('没有匹配的运行')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '重置筛选' }))
    expect(mocks.useFlowRuns).toHaveBeenLastCalledWith(expect.objectContaining({ query: undefined, status: undefined, statuses: undefined, offset: 0 }))
    expect(screen.getByRole('searchbox', { name: '搜索运行记录' })).toHaveValue('')
    expect(screen.getByText('暂无运行')).toBeInTheDocument()
  })

  it('supports both cancellation spellings and submits keyword search with Enter', async () => {
    mockQueries()
    render(<MemoryRouter><OverviewTab workflow={workflow} /></MemoryRouter>)
    await userEvent.selectOptions(screen.getByRole('combobox', { name: '运行状态' }), 'cancelled')
    expect(mocks.useFlowRuns).toHaveBeenLastCalledWith(expect.objectContaining({ status: undefined, statuses: ['cancelled', 'canceled'] }))
    await userEvent.type(screen.getByRole('searchbox', { name: '搜索运行记录' }), 'bot_123')
    expect(mocks.useFlowRuns).toHaveBeenLastCalledWith(expect.objectContaining({ query: undefined }))
    await userEvent.keyboard('{Enter}')
    expect(mocks.useFlowRuns).toHaveBeenLastCalledWith(expect.objectContaining({ query: 'bot_123', offset: 0 }))
  })

  it.each(['history', 'metrics'])('refreshes both queries and stays busy while %s is fetching', async (pendingQuery) => {
    mockQueries()
    const history = mocks.useFlowRuns()
    const metrics = { ...history, refetch: vi.fn() }
    mocks.useFlowRuns.mockImplementation((params) => params.limit === 1 ? metrics : history)
    const view = render(<MemoryRouter><OverviewTab workflow={workflow} /></MemoryRouter>)
    const refresh = screen.getByRole('button', { name: '刷新' })
    await userEvent.click(refresh)
    expect(history.refetch).toHaveBeenCalledTimes(1)
    expect(metrics.refetch).toHaveBeenCalledTimes(1)

    const pending = pendingQuery === 'history' ? history : metrics
    pending.isFetching = true
    view.rerender(<MemoryRouter><OverviewTab workflow={workflow} /></MemoryRouter>)
    expect(refresh).toBeDisabled()
    expect(refresh).toHaveAttribute('aria-busy', 'true')
    await userEvent.click(refresh)
    expect(history.refetch).toHaveBeenCalledTimes(1)
    expect(metrics.refetch).toHaveBeenCalledTimes(1)

    pending.isFetching = false
    view.rerender(<MemoryRouter><OverviewTab workflow={workflow} /></MemoryRouter>)
    expect(refresh).toBeEnabled()
    expect(refresh).toHaveAttribute('aria-busy', 'false')
  })

  it.each(['loading', 'error', 'refetchError', 'empty'] as const)('handles metric %s independently of successful history', async (state) => {
    mockQueries()
    const history = mocks.useFlowRuns()
    let metrics = {
      ...history,
      data: state === 'refetchError' ? history.data : state === 'empty' ? { runs: [], total: 0, statusCounts: {} } : undefined,
      isPending: state === 'loading',
      isError: state === 'error' || state === 'refetchError',
    }
    const retry = vi.fn(() => { metrics = { ...history, isPending: false, isError: false } })
    mocks.useFlowRuns.mockImplementation((params) => params.limit === 1 ? { ...metrics, refetch: retry } : history)
    const view = render(<MemoryRouter><OverviewTab workflow={workflow} /></MemoryRouter>)
    const region = screen.getByRole('region', { name: '工作流关键指标' })
    expect(within(region).getByText('异常结束').nextElementSibling).toHaveTextContent(state === 'empty' ? '0' : '—')
    for (const label of ['运行中', '等待中', '阻塞', '排队中']) {
      expect(screen.getByText(`${label} ${state === 'empty' ? '0' : '—'}`)).toBeInTheDocument()
    }
    expect(screen.getByRole('button', { name: '下一页' })).toBeEnabled()
    if (state === 'loading') expect(screen.getByRole('status')).toHaveTextContent('运行指标加载中')
    if (metrics.isError) {
      expect(screen.getByRole('alert')).toHaveTextContent('运行指标加载失败')
      expect(screen.queryByText('14 / 20 个终态运行')).not.toBeInTheDocument()
      await userEvent.click(screen.getByRole('button', { name: '重试指标' }))
      expect(retry).toHaveBeenCalledTimes(1)
      expect(history.refetch).not.toHaveBeenCalled()
      view.rerender(<MemoryRouter><OverviewTab workflow={workflow} /></MemoryRouter>)
      expect(screen.queryByRole('alert')).not.toBeInTheDocument()
      expect(within(region).getByText('70%')).toBeInTheDocument()
    }
  })

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

  it.each([[5, 0], [25, 20], [0, 0]])('clamps restored pagination for %s remaining runs', async (total, offset) => {
    mockQueries()
    mocks.useFlowRuns().data.total = total
    sessionStorage.setItem(`workflow-run-list:${workflow.workflow_id}`, JSON.stringify({ page: 4, statusFilter: '', query: '', searchInput: '', timeRange: '7d' }))
    render(<MemoryRouter><OverviewTab workflow={workflow} /></MemoryRouter>)
    await waitFor(() => expect(mocks.useFlowRuns).toHaveBeenLastCalledWith(expect.objectContaining({ offset })))
    expect(JSON.parse(sessionStorage.getItem(`workflow-run-list:${workflow.workflow_id}`)!).page).toBe(offset / 20)
  })

  it('switches list time independently and restores the selected range', async () => {
    mockQueries()
    const view = render(<MemoryRouter><OverviewTab workflow={workflow} /></MemoryRouter>)
    expect(screen.getByRole('combobox', { name: '运行时间范围' })).toHaveValue('7d')
    await userEvent.selectOptions(screen.getByRole('combobox', { name: '运行时间范围' }), '')
    expect(mocks.useFlowRuns.mock.calls.at(-1)?.[0].from).toBeUndefined()
    view.unmount()
    render(<MemoryRouter><OverviewTab workflow={workflow} /></MemoryRouter>)
    expect(screen.getByRole('combobox', { name: '运行时间范围' })).toHaveValue('')
    await userEvent.click(screen.getByRole('button', { name: '重置筛选' }))
    expect(screen.getByRole('combobox', { name: '运行时间范围' })).toHaveValue('7d')
  })

  it('keeps history pagination independent of the metric window', async () => {
    mockQueries()
    render(<MemoryRouter><OverviewTab workflow={workflow} /></MemoryRouter>)

    const initialHistory = mocks.useFlowRuns.mock.calls.at(-1)?.[0]
    expect(initialHistory).toEqual(expect.objectContaining({ workflowId: 'tech-research', limit: 20, offset: 0, from: expect.any(String), to: expect.any(String) }))

    await userEvent.click(screen.getByRole('button', { name: '下一页' }))
    await waitFor(() => expect(mocks.useFlowRuns).toHaveBeenLastCalledWith(expect.objectContaining({
      workflowId: 'tech-research',
      limit: 20,
      offset: 20,
    })))
    await userEvent.click(screen.getByRole('button', { name: '30天' }))
    expect(mocks.useWorkflowHealth).toHaveBeenLastCalledWith('tech-research', 30)
    expect(mocks.useFlowRuns).toHaveBeenLastCalledWith({ ...initialHistory, offset: 20 })
    const metricParams = mocks.useFlowRuns.mock.calls.at(-2)?.[0]
    expect(Number(metricParams.to) - Number(metricParams.from)).toBe(30 * 86400)
  })

  it('switches to "今天" starting the metric window from local midnight', async () => {
    mockQueries()
    render(<MemoryRouter><OverviewTab workflow={workflow} /></MemoryRouter>)

    await userEvent.click(screen.getByRole('button', { name: '今天' }))
    expect(mocks.useWorkflowHealth).toHaveBeenLastCalledWith('tech-research', 1)

    const metricParams = mocks.useFlowRuns.mock.calls.filter(([params]) => params?.limit === 1).at(-1)?.[0]
    const localMidnight = Math.floor(new Date(new Date().setHours(0, 0, 0, 0)).getTime() / 1000)
    expect(Number(metricParams?.from)).toBe(localMidnight)
    expect(Number(metricParams?.to)).toBeGreaterThanOrEqual(localMidnight)
  })
})
