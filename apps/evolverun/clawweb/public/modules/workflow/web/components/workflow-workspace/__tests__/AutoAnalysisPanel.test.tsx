import { act, render as renderView, screen, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { AutomationSnapshot, WorkflowAutomationPolicy } from '../../../../server/contracts/workflow-automation'

const mocks = vi.hoisted(() => ({ update: vi.fn(), retry: vi.fn(), refetch: vi.fn(), policy: {} as WorkflowAutomationPolicy, snapshot: {} as AutomationSnapshot }))
vi.mock('../../../api/workflow-automation', () => ({
  useAutomationPolicy: () => ({ data: mocks.policy, isLoading: false, isError: false, refetch: vi.fn() }),
  useSaveAutomationPolicy: () => ({ mutate: mocks.update, isPending: false }),
  useAutomationStatus: () => ({ data: mocks.snapshot, isError: false, refetch: mocks.refetch }),
  useAutomationRecords: (_id: string, options: {limit: number; kind?: string; status?: string}) => ({ data: {jobs: mocks.snapshot.jobs.filter(job => !options.kind || options.kind === 'all' || job.kind === ({analysis:'auto_run',optimization:'auto_draft',notification:'auto_notice'} as Record<string,string>)[options.kind]).filter(job => !options.status || options.status === 'all' || (options.status === 'attention' ? ['retryable','uncertain','blocked'].includes(job.status) : job.status === options.status)).slice(0, options.limit), nextCursor: null}, isFetching: false, isError: false, isPending: false }),
  retryAutomationFailures: mocks.retry,
}))
import AutoAnalysisPanel from '../AutoAnalysisPanel'
function render(ui: React.ReactNode) { return renderView(<QueryClientProvider client={new QueryClient()}>{ui}</QueryClientProvider>) }
async function openRecords(region = '自动分析设置') {
  await userEvent.click(within(screen.getByRole('region', { name: region })).getByRole('button', { name: '查看记录' }))
}
beforeEach(() => {
  vi.clearAllMocks()
  mocks.policy = { workflowId: 'wf-1', enabled: false, autoDraft: false, dailyTime: '09:00', version: 0, dailyConfigured: false, source: 'default', timezone: 'Asia/Shanghai', updatedAtMs: 0 }
  mocks.snapshot = { schemaVersion: 'workflow-automation/v1', workflowId: 'wf-1', jobs: [], retryableJobs: [], lastScannedAtMs: null, pauseReason: null, coverage: { total: 0, pending: 0, failed: 0, partial: 0, unknown: 0 } }
})
describe('automatic analysis settings and execution records', () => {
  it('saves the execution environment immediately while analysis is off and keeps the switch off', async () => {
    render(<AutoAnalysisPanel workflowId="wf-1" />)
    const select = screen.getByRole('combobox', { name: '自动任务执行环境' })
    expect(select).toHaveValue('prod')
    await userEvent.selectOptions(select, 'pre')
    expect(mocks.update).toHaveBeenCalledWith(expect.objectContaining({ executionEnvironment: 'pre', enabled: false, dailyConfigured: false }), expect.any(Object))
    expect(screen.getByRole('status')).toHaveTextContent('执行环境保存中')
    expect(select).toBeDisabled()
  })
  it('hides successful notification traffic and folds only delivery exceptions separately', async () => {
    mocks.snapshot.jobs = [
      {id:'sent-notice',kind:'auto_notice',status:'done',attempts:1,reason:null},
      {id:'uncertain-notice',kind:'auto_notice',status:'uncertain',attempts:1,reason:'NOTIFICATION_OUTCOME_UNKNOWN'},
    ]
    render(<AutoAnalysisPanel workflowId="wf-1" />)
    expect(screen.queryByText('任务通知 · 已结束')).not.toBeInTheDocument()
    expect(screen.queryByText('任务通知 · 待核对')).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '查看通知异常' }))
    expect(screen.getByText('任务通知 · 待核对')).toBeVisible()
  })
  it('immediately acknowledges a slow switch save and rejects repeat clicks', async () => {
    render(<AutoAnalysisPanel workflowId="wf-1" />)
    await userEvent.click(screen.getByRole('switch', { name: '自动分析' }))
    expect(screen.getByRole('status')).toHaveTextContent('开启中')
    expect(screen.getByRole('switch', { name: '自动分析' })).toBeDisabled()
    await userEvent.click(screen.getByRole('switch', { name: '自动分析' }))
    expect(mocks.update).toHaveBeenCalledTimes(1)
  })
  it('saves notification recipients while automation is disabled without saving unrelated drafts', async () => {
    render(<AutoAnalysisPanel workflowId="wf-1" />)
    expect(screen.getByRole('region', { name: '自动优化' })).toBeVisible()
    await userEvent.type(screen.getByLabelText('通知人'), 'alice, bob')
    await userEvent.click(screen.getByRole('button', { name: '保存通知人' }))
    expect(mocks.update).toHaveBeenCalledWith(expect.objectContaining({ enabled: false, dailyConfigured: false, notificationRecipients: ['alice', 'bob'] }), expect.any(Object))
    expect(screen.getByRole('status')).toHaveTextContent('保存通知人')
  })
  it('restores the Owner default by clearing recipients while analysis remains off', async () => {
    mocks.policy.notificationRecipients = ['10001']
    mocks.policy.notificationVersion = 4
    render(<AutoAnalysisPanel workflowId="wf-1" />)
    await userEvent.clear(screen.getByLabelText('通知人'))
    await userEvent.click(screen.getByRole('button', { name: '保存通知人' }))
    expect(mocks.update).toHaveBeenCalledWith(expect.objectContaining({ enabled: false, dailyConfigured: false, notificationRecipients: [], expectedNotificationVersion: 4 }), expect.any(Object))
  })
  it('preserves the existing enable request and disabled controls', async () => {
    render(<AutoAnalysisPanel workflowId="wf-1" />)
    expect(screen.getByRole('region', { name: '自动分析设置' })).toBeInTheDocument()
    expect(screen.getByText(/不会自动部署/)).toBeInTheDocument()
    const toggle = screen.getByRole('switch', { name: '自动分析' })
    expect(toggle).toHaveAttribute('aria-checked', 'false')
    await userEvent.click(toggle)
    expect(mocks.update).toHaveBeenCalledWith({ enabled: true, autoDraft: false, dailyTime: '09:00', dailyConfigured: false, expectedVersion: 0 }, expect.objectContaining({ onError: expect.any(Function) }))
    expect(screen.getByLabelText('每日执行时间')).toBeDisabled()
    expect(screen.queryByLabelText('自动生成优化草稿')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '开启自动分析' })).toBeDisabled()
  })
  it('distinguishes inherited failure analysis from unconfigured daily scheduling and saves both settings explicitly', async () => {
    mocks.policy.enabled = true; mocks.policy.source = 'environment'
    render(<AutoAnalysisPanel workflowId="wf-1" />)
    expect(screen.getByText('尚未启用')).toBeVisible()
    expect(screen.getByRole('switch', { name: '自动分析' })).toHaveAttribute('aria-checked', 'true')
    expect(mocks.update).not.toHaveBeenCalled()
    await userEvent.selectOptions(screen.getByLabelText('最低影响'), 'high')
    await userEvent.click(screen.getByRole('button', { name: '保存每日草稿计划' }))
    expect(mocks.update).toHaveBeenCalledWith(expect.objectContaining({ dailyTime: '09:00', minimumImpact: 'high' }), expect.any(Object))
  })
  it('shows real run identity, Shanghai time, partial evidence and existing analysis detail links on demand', async () => {
    mocks.snapshot.jobs = [
      { id: 'j1', kind: 'auto_run', status: 'waiting', attempts: 1, reason: null, flowId: 'flow/a', createdAtMs: Date.parse('2026-10-20T02:03:04Z') },
      { id: 'j2', kind: 'auto_run', status: 'done', attempts: 1, reason: 'PARTIAL_ANALYSIS', flowId: 'flow-b', analysisId: 'analysis/b', complete: false },
      { id: 'j3', kind: 'auto_run', status: 'done', attempts: 1, reason: null, complete: true, flowId: 'flow-c' },
    ]
    render(<AutoAnalysisPanel workflowId="wf-1" />)
    await openRecords()
    expect(screen.getByText('运行分析 · 分析中，等待分析结果')).toBeVisible()
    expect(screen.getByText('运行分析 · 分析结束，证据不完整')).toBeVisible()
    expect(screen.getByText('运行分析 · 分析结束')).toBeVisible()
    expect(screen.getByText(/2026\/10\/20 10:03:04.*运行 flow\/a/)).toBeVisible()
    expect(screen.getAllByRole('link', { name: '查看分析' }).map(a => a.getAttribute('href'))).toEqual(['/runs/flow%2Fa', '/runs/flow-b?analysisId=analysis%2Fb', '/runs/flow-c'])
  })
  it('keeps old records inspectable without inventing links or complete evidence', async () => {
    mocks.snapshot.jobs = [{ id: 'old-job', kind: 'auto_run', status: 'done', attempts: 2, reason: null }]
    delete mocks.snapshot.retryableJobs
    render(<AutoAnalysisPanel workflowId="wf-1" />)
    await openRecords()
    expect(screen.getByText('运行分析 · 分析结束，证据完整性待核对')).toBeVisible()
    expect(screen.queryByRole('link')).not.toBeInTheDocument()
    await userEvent.click(screen.getByText('查看详情'))
    expect(screen.getByText('任务编号：old-job；尝试次数：2')).toBeVisible()
    expect(screen.queryByRole('button', { name: '重试失败任务' })).not.toBeInTheDocument()
    expect(screen.queryByRole('region', { name: '失败任务重试' })).not.toBeInTheDocument()
  })
  it('retries only the selected record with a type-specific action and no mixed bulk operation', async () => {
    mocks.snapshot.retryableJobs = [
      { id: 'analysis-job', kind: 'auto_run', status: 'blocked', attempts: 3, reason: 'ANALYSIS_FAILED', flowId: 'failed-run' },
      { id: 'draft-job', kind: 'auto_draft', status: 'blocked', attempts: 1, reason: 'DRAFT_REQUIRES_MANUAL_REVIEW', taskId: 'repair-1' },
      { id: 'notice-job', kind: 'auto_notice', status: 'blocked', attempts: 3, reason: 'NOTIFICATION_REJECTED' },
    ]
    mocks.snapshot.jobs = mocks.snapshot.retryableJobs
    render(<AutoAnalysisPanel workflowId="wf-1" />)
    expect(screen.queryByRole('region', { name: '失败任务重试' })).not.toBeInTheDocument()
    expect(mocks.retry).not.toHaveBeenCalled()
    await openRecords()
    await userEvent.click(screen.getByRole('button', { name: '重试分析' }))
    expect(mocks.retry).toHaveBeenLastCalledWith('wf-1', 'analysis-job')
    expect(screen.getByRole('status')).toHaveTextContent('已加入重试队列')
    await openRecords('自动优化')
    await userEvent.click(screen.getByRole('button', {name:'重试生成'}))
    expect(mocks.retry).toHaveBeenLastCalledWith('wf-1','draft-job')
    await userEvent.click(screen.getByRole('button', { name: '查看通知异常' }))
    await userEvent.click(screen.getByRole('button', {name:'重发通知'}))
    expect(mocks.retry).toHaveBeenLastCalledWith('wf-1','notice-job')
    expect(mocks.retry).toHaveBeenCalledTimes(3)
  })
  it('hides the empty retry operation', () => {
    render(<AutoAnalysisPanel workflowId="wf-1" />)
    expect(screen.queryByRole('button', { name: '重试失败任务' })).not.toBeInTheDocument()
    expect(screen.queryByRole('region', { name: '失败任务重试' })).not.toBeInTheDocument()
    expect(screen.queryByText(/当前没有可重试的失败任务/)).not.toBeInTheDocument()
  })
})

it('does not leave a retry action outside a filtered-out record', async () => {
  mocks.snapshot.retryableJobs = [{id:'failure',kind:'auto_run',status:'blocked',reason:'ANALYSIS_FAILED',flowId:'target-run',attempts:3}]
  mocks.snapshot.jobs = mocks.snapshot.retryableJobs
  render(<AutoAnalysisPanel workflowId="wf-1" />)
  await openRecords()
  await userEvent.selectOptions(screen.getByLabelText('执行记录时间范围'),'today')
  await userEvent.selectOptions(screen.getByLabelText('执行记录状态'),'done')
  expect(screen.queryByRole('button',{name:'重试失败任务'})).not.toBeInTheDocument()
  expect(screen.queryByRole('button',{name:'重试分析'})).not.toBeInTheDocument()
  expect(mocks.retry).not.toHaveBeenCalled()
})

it('shows immediate per-record pending feedback, prevents duplicate submits and refreshes after rejection', async () => {
  mocks.snapshot.jobs = [{id:'chosen',kind:'auto_run',status:'blocked',attempts:3,reason:'ANALYSIS_FAILED',flowId:'run'}]
  mocks.snapshot.retryableJobs = mocks.snapshot.jobs
  let reject!: (error: Error)=>void
  mocks.retry.mockImplementationOnce(()=>new Promise((_resolve, fail)=>{reject=fail}))
  render(<AutoAnalysisPanel workflowId="wf-1" />)
  await openRecords()
  await userEvent.click(screen.getByRole('button',{name:'重试分析'}))
  expect(screen.getByRole('button',{name:'提交中…'})).toBeDisabled()
  expect(screen.getByRole('status')).toHaveTextContent('正在提交本条重试')
  await userEvent.click(screen.getByRole('button',{name:'提交中…'}))
  expect(mocks.retry).toHaveBeenCalledTimes(1)
  await act(async()=>reject(new Error('状态已变化')))
  expect(screen.getByRole('alert')).toHaveTextContent('状态已变化')
  expect(mocks.refetch).toHaveBeenCalledOnce()
  expect(screen.getByRole('button',{name:'重试分析'})).toBeEnabled()
})

it('does not offer retries for running, unknown or completed records even if an eligibility snapshot is stale', async () => {
  mocks.snapshot.jobs = ['waiting','uncertain','done'].map((status,i)=>({id:`job-${i}`,kind:'auto_run',status,attempts:1,reason:null}))
  mocks.snapshot.retryableJobs = mocks.snapshot.jobs
  render(<AutoAnalysisPanel workflowId="wf-1" />)
  await openRecords()
  expect(screen.queryByRole('button',{name:'重试分析'})).not.toBeInTheDocument()
})
