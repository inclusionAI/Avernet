import { render as renderView, screen, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { AutomationSnapshot, WorkflowAutomationPolicy } from '../../../../server/contracts/workflow-automation'

const mocks = vi.hoisted(() => ({ update: vi.fn(), retry: vi.fn(), refetch: vi.fn(), policy: {} as WorkflowAutomationPolicy, snapshot: {} as AutomationSnapshot }))
vi.mock('../../../api/workflow-automation', () => ({
  useAutomationPolicy: () => ({ data: mocks.policy, isLoading: false, isError: false, refetch: vi.fn() }),
  useSaveAutomationPolicy: () => ({ mutate: mocks.update, isPending: false }),
  useAutomationStatus: () => ({ data: mocks.snapshot, isError: false, refetch: mocks.refetch }),
  useAutomationRecords: (_id: string, options: {limit: number}) => ({ data: {jobs: mocks.snapshot.jobs.slice(0, options.limit), nextCursor: null}, isFetching: false, isError: false, isPending: false }),
  retryAutomationFailures: mocks.retry,
}))
import AutoAnalysisPanel from '../AutoAnalysisPanel'
function render(ui: React.ReactNode) { return renderView(<QueryClientProvider client={new QueryClient()}>{ui}</QueryClientProvider>) }
beforeEach(() => {
  vi.clearAllMocks()
  mocks.policy = { workflowId: 'wf-1', enabled: false, autoDraft: false, dailyTime: '09:00', version: 0, dailyConfigured: false, source: 'default', timezone: 'Asia/Shanghai', updatedAtMs: 0 }
  mocks.snapshot = { schemaVersion: 'workflow-automation/v1', workflowId: 'wf-1', jobs: [], retryableJobs: [], lastScannedAtMs: null, pauseReason: null, coverage: { total: 0, pending: 0, failed: 0, partial: 0, unknown: 0 } }
})
describe('automatic analysis settings and execution records', () => {
  it('preserves the existing enable request and disabled controls', async () => {
    render(<AutoAnalysisPanel workflowId="wf-1" />)
    expect(screen.getByText('自动分析')).toBeInTheDocument()
    expect(screen.getByText(/不会自动部署/)).toBeInTheDocument()
    const toggle = screen.getByRole('switch', { name: '自动分析' })
    expect(toggle).toHaveAttribute('aria-checked', 'false')
    await userEvent.click(toggle)
    expect(mocks.update).toHaveBeenCalledWith({ enabled: true, autoDraft: false, dailyTime: '09:00', expectedVersion: 0 }, expect.objectContaining({ onError: expect.any(Function) }))
    expect(screen.getByLabelText('每日执行时间')).toBeDisabled()
    expect(screen.getByLabelText('自动生成优化草稿')).toBeDisabled()
  })
  it('distinguishes inherited failure analysis from unconfigured daily scheduling and saves both settings explicitly', async () => {
    mocks.policy.enabled = true; mocks.policy.source = 'environment'
    render(<AutoAnalysisPanel workflowId="wf-1" />)
    expect(screen.getByText('失败后自动分析：已开启；每日定时分析：未配置。')).toBeVisible()
    expect(mocks.update).not.toHaveBeenCalled()
    await userEvent.selectOptions(screen.getByLabelText('最低影响'), 'high')
    await userEvent.click(screen.getByRole('button', { name: '保存设置' }))
    expect(mocks.update).toHaveBeenCalledWith(expect.objectContaining({ dailyTime: '09:00', minimumImpact: 'high' }), expect.any(Object))
  })
  it('shows real run identity, Shanghai time, partial evidence and existing analysis detail links', () => {
    mocks.snapshot.jobs = [
      { id: 'j1', kind: 'auto_run', status: 'waiting', attempts: 1, reason: null, flowId: 'flow/a', createdAtMs: Date.parse('2026-10-20T02:03:04Z') },
      { id: 'j2', kind: 'auto_run', status: 'done', attempts: 1, reason: 'PARTIAL_ANALYSIS', flowId: 'flow-b', analysisId: 'analysis/b', complete: false },
      { id: 'j3', kind: 'auto_run', status: 'done', attempts: 1, reason: null, complete: true, flowId: 'flow-c' },
    ]
    render(<AutoAnalysisPanel workflowId="wf-1" />)
    expect(screen.getByText('运行分析 · 分析中，等待分析结果')).toBeVisible()
    expect(screen.getByText('运行分析 · 分析结束，证据不完整')).toBeVisible()
    expect(screen.getByText('运行分析 · 分析结束')).toBeVisible()
    expect(screen.getByText(/2026\/10\/20 10:03:04.*运行 flow\/a/)).toBeVisible()
    expect(screen.getAllByRole('link', { name: '查看分析' }).map(a => a.getAttribute('href'))).toEqual(['/runs/flow%2Fa', '/runs/flow-b?analysisId=analysis%2Fb', '/runs/flow-c'])
    expect(screen.getByText(/每日扫描尚无记录；失败事件触发的分析单独记录在下方/)).toBeVisible()
  })
  it('keeps old records inspectable without inventing links or complete evidence', async () => {
    mocks.snapshot.jobs = [{ id: 'old-job', kind: 'auto_run', status: 'done', attempts: 2, reason: null }]
    delete mocks.snapshot.retryableJobs
    render(<AutoAnalysisPanel workflowId="wf-1" />)
    expect(screen.getByText('运行分析 · 分析结束，证据完整性待核对')).toBeVisible()
    expect(screen.queryByRole('link')).not.toBeInTheDocument()
    await userEvent.click(screen.getByText('查看详情'))
    expect(screen.getByText('任务编号：old-job；尝试次数：2')).toBeVisible()
    expect(screen.queryByRole('button', { name: '重试失败任务' })).not.toBeInTheDocument()
    expect(screen.getByText(/暂未获取可重试对象/)).toBeVisible()
  })
  it('lists the exact retry targets across analysis, draft and notification before the authorized retry', async () => {
    mocks.snapshot.retryableJobs = [
      { id: 'analysis-job', kind: 'auto_run', status: 'blocked', attempts: 3, reason: 'ANALYSIS_FAILED', flowId: 'failed-run' },
      { id: 'draft-job', kind: 'auto_draft', status: 'blocked', attempts: 1, reason: 'DRAFT_REQUIRES_MANUAL_REVIEW', taskId: 'repair-1' },
      { id: 'notice-job', kind: 'auto_notice', status: 'blocked', attempts: 3, reason: 'NOTIFICATION_REJECTED' },
    ]
    render(<AutoAnalysisPanel workflowId="wf-1" />)
    expect(screen.getByText(/将重试以下 3 个失败任务/)).toBeVisible()
    const list = screen.getByRole('list', { name: '将重试的失败任务' })
    expect(within(list).getAllByRole('listitem')).toHaveLength(3)
    expect(within(list).getByText(/运行 failed-run/)).toBeVisible()
    expect(within(list).getByRole('link', { name: '查看原优化任务' })).toHaveAttribute('href', '/workflows/workspace?workflowId=wf-1&tab=evolution&repairTaskId=repair-1')
    expect(within(list).getByText(/任务 notice-job/)).toBeVisible()
    expect(mocks.retry).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button', { name: '重试失败任务' }))
    expect(mocks.retry).toHaveBeenCalledWith('wf-1')
    expect(mocks.refetch).toHaveBeenCalledOnce()
  })
  it('hides the empty retry operation', () => {
    render(<AutoAnalysisPanel workflowId="wf-1" />)
    expect(screen.queryByRole('button', { name: '重试失败任务' })).not.toBeInTheDocument()
    expect(screen.getByText(/当前没有可重试的失败任务/)).toBeVisible()
  })
})

it('keeps retry targets outside history filters and pagination', async () => {
  mocks.snapshot.retryableJobs = [{id:'failure',kind:'auto_run',status:'blocked',reason:'ANALYSIS_FAILED',flowId:'target-run',attempts:3}]
  render(<AutoAnalysisPanel workflowId="wf-1" />)
  await userEvent.click(screen.getByRole('button',{name:'查看全部记录'}))
  await userEvent.selectOptions(screen.getByLabelText('执行记录时间范围'),'today')
  await userEvent.selectOptions(screen.getByLabelText('执行记录状态'),'done')
  expect(within(screen.getByRole('list',{name:'将重试的失败任务'})).getByText(/运行 target-run/)).toBeVisible()
  expect(screen.getByRole('button',{name:'重试失败任务'})).toBeEnabled()
})
