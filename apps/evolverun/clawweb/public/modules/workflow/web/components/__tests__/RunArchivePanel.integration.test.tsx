import { cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ARCHIVE_FLOW_ID, ARCHIVE_TIMESTAMP_SECONDS, createArchiveFixture } from './RunArchivePanel.fixture'

const hooks = vi.hoisted(() => ({
  useRunArchive: vi.fn(), useRunTimeline: vi.fn(), useFlowRun: vi.fn(), useDbWorkflow: vi.fn(),
  useEvolveDiagnoses: vi.fn(), useEvolveSuggestions: vi.fn(), useAnalyzeRun: vi.fn(),
  useResetAnalysisRun: vi.fn(), useAnalysisProgress: vi.fn(), useRunEvolutionAnalysis: vi.fn(),
}))
vi.mock('@avernet/workflow/web/api/hooks', () => hooks)
vi.mock('../../api/hooks', () => hooks)
vi.mock('@avernet/clawweb-shared/web/api/hooks', () => ({
  useFlowApprovals: () => ({ data: { items: [] }, isLoading: false, refetch: vi.fn() }),
}))

// Unrelated panes are isolated; RunDetail, RunArchivePanel, and SimpleRunLogsPanel stay real.
vi.mock('../RunSummaryHeader', () => ({ default: () => <div>运行摘要</div> }))
vi.mock('../SubworkflowLinks', () => ({
  default: () => null,
  UnattributedSubworkflowLinks: () => null,
  SubworkflowProvider: ({ children }: { children: React.ReactNode }) => children,
}))
vi.mock('../NodeExecutionList', () => ({ default: () => <div>节点列表</div> }))
vi.mock('../RunDagView', () => ({ default: () => null }))
vi.mock('../AnalysisModal', () => ({ default: () => null }))
vi.mock('../InterventionPanel', () => ({ default: () => null }))
vi.mock('../AnalyzeRunBotModal', () => ({ default: () => null }))
vi.mock('../AutoHealPanel', () => ({ default: () => null }))
vi.mock('../ApprovalPanel', () => ({ default: () => null }))
vi.mock('../ApprovalSlidePanel', () => ({ default: () => null }))
vi.mock('../evolution/RunEvolutionAnalysis', () => ({ default: () => null }))
vi.mock('../workflow-workspace/AggregationProgress', () => ({ default: () => null }))

import RunDetail from '../../pages/RunDetail'

afterEach(cleanup)
beforeEach(() => {
  hooks.useRunArchive.mockReturnValue({ data: createArchiveFixture(), isLoading: false, isError: false, error: null })
  hooks.useRunTimeline.mockReturnValue({
    data: { events: [{
      id: 'synthetic-live-log', timestamp: ARCHIVE_TIMESTAMP_SECONDS, relativeMs: 0,
      nodeId: null, source: 'run_log', displayType: 'info', detail: 'Top-level live log marker',
      payload: { level: 'info', source: 'runtime' },
    }] },
    isLoading: false,
  })
  hooks.useFlowRun.mockReturnValue({
    data: { run: { flow_id: ARCHIVE_FLOW_ID, workflow_id: 'archive-regression-workflow', workflow_title: '回归测试示例流程', status: 'failed', node_count: 0, evolution_analysis_status: 'none' }, nodes: [] },
    isLoading: false, isError: false, error: null, refetch: vi.fn(),
  })
  hooks.useDbWorkflow.mockReturnValue({ data: { nodes: [] } })
  hooks.useEvolveDiagnoses.mockReturnValue({ data: { diagnoses: [] }, isLoading: false })
  hooks.useEvolveSuggestions.mockReturnValue({ data: { suggestions: [] }, isLoading: false })
  hooks.useAnalyzeRun.mockReturnValue({ mutate: vi.fn(), isPending: false, isError: false })
  hooks.useResetAnalysisRun.mockReturnValue({ mutate: vi.fn(), isPending: false })
  hooks.useAnalysisProgress.mockReturnValue({ data: null, isError: false })
  hooks.useRunEvolutionAnalysis.mockReturnValue({ data: null, isLoading: false, isError: false, refetch: vi.fn() })
})

describe('RunArchivePanel integration with the unchanged run detail page', () => {
  it('keeps both the top-level Logs entry and the archive-local Run Logs entry usable', async () => {
    const user = userEvent.setup()
    render(
      <MemoryRouter initialEntries={[`/runs/${ARCHIVE_FLOW_ID}`]}>
        <Routes><Route path="/runs/:flowId" element={<RunDetail />} /></Routes>
      </MemoryRouter>,
    )
    const runNav = screen.getByRole('navigation', { name: /运行详情视图/ })
    await user.click(within(runNav).getByRole('button', { name: /^日志$/ }))
    expect(screen.getByText('Top-level live log marker')).toBeVisible()
    expect(hooks.useRunTimeline).toHaveBeenCalledWith(ARCHIVE_FLOW_ID)

    await user.click(within(runNav).getByRole('button', { name: /运行档案/ }))
    await user.click(within(screen.getByRole('navigation', { name: /运行档案分区/ })).getByRole('button', { name: /运行过程/ }))
    await user.click(within(screen.getByRole('navigation', { name: /运行过程视图/ })).getByRole('button', { name: /运行日志/ }))
    expect(screen.getByText(/Synthetic startup marker/)).toBeVisible()
    expect(screen.getByText(/Synthetic error marker/)).toBeVisible()
    expect(hooks.useRunArchive).toHaveBeenCalledWith(ARCHIVE_FLOW_ID)
    expect(within(runNav).getByRole('button', { name: /^日志$/ })).toBeVisible()

    await user.click(within(runNav).getByRole('button', { name: /^日志$/ }))
    expect(screen.getByText('Top-level live log marker')).toBeVisible()
    expect(screen.queryByText(/Synthetic startup marker/)).not.toBeInTheDocument()
  })
})
