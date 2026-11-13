import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { FlowRun, WorkflowTypeRow } from '@avernet/clawweb-shared/web/types'
import WorkflowRuns from '../../pages/WorkflowRuns'
import OverviewTab from '../workflow-workspace/OverviewTab'
import RunSummaryHeader from '../RunSummaryHeader'

const mocks = vi.hoisted(() => {
  const useFlowRuns = vi.fn()
  const mutation = { mutate: vi.fn(), reset: vi.fn(), isPending: false }
  return {
    useFlowRuns,
    user: { userId: 'viewer', isAdmin: false },
    mutation,
    hooks: {
      useFlowRuns,
      useWorkflowHealth: () => ({ data: undefined }),
      useAnalysisProgress: () => ({ data: null }),
      useAnalyzeRun: () => mutation,
      useDeleteFlowRun: () => mutation,
      useRerunFlowRun: () => mutation,
    },
  }
})
vi.mock('@avernet/workflow/web/api/hooks', () => mocks.hooks)
vi.mock('../../api/hooks', () => mocks.hooks)
vi.mock('@avernet/clawweb-shared/web/api/hooks', () => ({
  useDeleteFlowRun: () => mocks.mutation,
  useRerunFlowRun: () => mocks.mutation,
  useAbortFlowRun: () => mocks.mutation,
  useFlowApprovals: () => ({ data: { items: [] }, isLoading: false }),
}))
vi.mock('@avernet/clawweb-shared/web/hooks/useClientUser', () => ({ getClientUser: () => mocks.user }))
vi.mock('../SuccessTrendCard', () => ({ SuccessTrendCard: () => null }))
vi.mock('@avernet/workflow/web/components/SuccessTrendCard', () => ({ SuccessTrendCard: () => null }))
vi.mock('../RunCountTrendCard', () => ({ RunCountTrendCard: () => null }))
vi.mock('../HealthScoreCard', () => ({ HealthScoreCard: () => null }))
vi.mock('../NodeAnalysisPanel', () => ({ NodeAnalysisPanel: () => null }))
vi.mock('@avernet/workflow/web/components/NodeAnalysisPanel', () => ({ NodeAnalysisPanel: () => null }))
vi.mock('../AutoHealPanel', () => ({ default: () => null }))
vi.mock('../AnalyzeRunBotModal', () => ({ default: () => null }))
vi.mock('../ApprovalSlidePanel', () => ({ default: () => null }))

const workflow: WorkflowTypeRow = {
  workflow_id: 'marker-workflow',
  workflow_title: 'Marker workflow',
  run_count: 4,
  last_status: 'succeeded',
  last_run_at: 1,
  updated_at: 1,
}

function makeRun(flowId: string, fields: Partial<FlowRun> = {}): FlowRun {
  return {
    flow_id: flowId,
    workflow_id: workflow.workflow_id,
    workflow_title: workflow.workflow_title!,
    status: 'succeeded',
    triggered_by: 'creator',
    node_count: 1,
    succeeded_count: 1,
    failed_count: 0,
    total_duration_ms: 1000,
    total_token_usage: null,
    started_at: '2026-11-12T01:00:00Z',
    completed_at: '2026-11-12T01:00:01Z',
    user_id: 'creator',
    origin_bot_id: 'origin-bot',
    plugin_version: '1.0',
    engine: 'engine-not-an-env',
    workflow_version: 1,
    workflow_deploy_number: 1,
    ...fields,
  }
}

function mockRuns(runs: FlowRun[]) {
  mocks.useFlowRuns.mockReturnValue({
    data: { runs, total: runs.length, statusCounts: { succeeded: runs.length } },
    isLoading: false,
    isPending: false,
    isError: false,
    isFetching: false,
    refetch: vi.fn(),
  })
}

function renderList(view: 'WorkflowRuns' | 'OverviewTab') {
  return render(
    <MemoryRouter initialEntries={['/workflows/marker-workflow/runs']}>
      <Routes>
        <Route path="/workflows/:workflowId/runs" element={view === 'WorkflowRuns' ? <WorkflowRuns /> : <OverviewTab workflow={workflow} />} />
        <Route path="/runs/:flowId" element={<div>Run detail destination</div>} />
      </Routes>
    </MemoryRouter>,
  )
}

function runIdCell(flowId: string) {
  const row = screen.getByRole('row', { name: new RegExp(flowId) })
  return within(row).getAllByRole('cell')[0]
}

const longTag = `release-"<&>${'长标签'.repeat(100)}<img src=x onerror="alert(1)"><script>alert(2)</script>`

beforeEach(() => {
  vi.clearAllMocks()
  sessionStorage.clear()
  mocks.user.isAdmin = false
})

describe.each(['WorkflowRuns', 'OverviewTab'] as const)('%s env/tag wiring', (view) => {
  it('shows per-run markers beside the ID without changing engine columns or server order', () => {
    const runs = [
      makeRun('z-test', { env: 'test', tag: 'release-candidate' }),
      makeRun('b-missing'),
      makeRun('a-null', { env: null, tag: null }),
      makeRun('c-default', { env: 'prod', tag: '' }),
    ]
    mockRuns(runs)
    renderList(view)
    const table = screen.getByRole('table')
    const headers = within(table).getAllByRole('columnheader')
    expect(headers).toHaveLength(view === 'WorkflowRuns' ? 11 : 9)
    expect(within(table).getByRole('columnheader', { name: '运行引擎' })).toBeInTheDocument()
    expect(within(table).queryByRole('columnheader', { name: '环境', exact: true })).not.toBeInTheDocument()
    expect(within(table).getAllByText('engine-not-an-env')).toHaveLength(runs.length)
    const firstCells = within(table).getAllByRole('row').slice(1).map(row => within(row).getAllByRole('cell')[0])
    expect(firstCells.map(cell => cell.firstElementChild?.textContent)).toEqual(runs.map(run => run.flow_id))
    expect(within(runIdCell('z-test')).getByText('test')).toBeInTheDocument()
    expect(within(runIdCell('z-test')).getByText('流量标识')).toBeInTheDocument()
    expect(within(runIdCell('z-test')).getByText('标签')).toBeInTheDocument()
    expect(within(runIdCell('z-test')).getByText('release-candidate')).toHaveAttribute('title', '标签: release-candidate')
    const expectedMarkers = new Map([
      ['b-missing', '流量标识未记录标签未记录'],
      ['a-null', '流量标识未记录标签未记录'],
      ['c-default', '流量标识prod标签—'],
    ] as const)
    for (const flowId of ['b-missing', 'a-null', 'c-default'] as const) {
      const cell = runIdCell(flowId)
      const expectedMarker = expectedMarkers.get(flowId)
      expect(cell.textContent).toBe(`${flowId}${expectedMarker}`)
    }
  })

  it('bounds long tags, preserves the full title/text, and keeps row navigation', async () => {
    mockRuns([makeRun('long-tag-run', { env: 'test', tag: longTag })])
    const { container } = renderList(view)
    const tag = within(runIdCell('long-tag-run')).getByTitle(`标签: ${longTag}`)
    expect(tag.textContent).toBe(longTag)
    expect(tag).toHaveClass('truncate')
    expect(tag.parentElement).toHaveClass('min-w-0', 'max-w-48')
    expect(container.querySelector('img, script')).toBeNull()
    await userEvent.click(tag)
    expect(screen.getByText('Run detail destination')).toBeInTheDocument()
  })
})

describe('WorkflowRuns purpose and permissions', () => {
  it('keeps markers in trial runs and preserves the existing owner/admin request scope', async () => {
    mockRuns([makeRun('trial-run', { env: 'test', tag: 'trial-label' })])
    const view = renderList('WorkflowRuns')
    expect(mocks.useFlowRuns).toHaveBeenLastCalledWith(expect.objectContaining({ purpose: 'production', botOwnerId: 'viewer' }))
    await userEvent.selectOptions(screen.getByRole('combobox', { name: '运行用途' }), 'trial')
    expect(mocks.useFlowRuns).toHaveBeenLastCalledWith(expect.objectContaining({ purpose: 'trial', botOwnerId: 'viewer' }))
    expect(within(runIdCell('trial-run')).getByText('test')).toBeInTheDocument()
    expect(within(runIdCell('trial-run')).getByText('标签')).toBeInTheDocument()
    expect(within(runIdCell('trial-run')).getByText('trial-label')).toBeInTheDocument()
    view.unmount()
    mocks.user.isAdmin = true
    renderList('WorkflowRuns')
    expect(mocks.useFlowRuns).toHaveBeenLastCalledWith(expect.objectContaining({ botOwnerId: undefined }))
  })
})

describe('RunSummaryHeader env/tag wiring', () => {
  it.each([
    { name: 'missing', fields: {}, env: '未记录', tag: '未记录' },
    { name: 'null', fields: { env: null, tag: null }, env: '未记录', tag: '未记录' },
    { name: 'default', fields: { env: 'prod', tag: '' }, env: 'prod', tag: '—' },
    { name: 'test with tag', fields: { env: 'test', tag: 'detail-label' }, env: 'test', tag: 'detail-label' },
    { name: 'mixed null env', fields: { env: null, tag: 'tag-only' }, env: '未记录', tag: 'tag-only' },
    { name: 'mixed null tag', fields: { env: 'test', tag: null }, env: 'test', tag: '未记录' },
    { name: 'raw strings', fields: { env: '', tag: '  raw-tag  ' }, env: '', tag: '  raw-tag  ' },
  ])('shows separate env/tag fields for $name responses', ({ fields, env, tag }) => {
    render(<RunSummaryHeader run={makeRun('detail-run', fields)} nodeCount={1} />)
    const summary = screen.getByRole('region', { name: '运行摘要' })
    expect(within(summary).getByText('流量标识', { selector: 'dt' }).nextElementSibling?.textContent).toBe(env)
    expect(within(summary).getByText('标签', { selector: 'dt' }).nextElementSibling?.textContent).toBe(tag)
    expect(within(summary).getByText('运行引擎', { selector: 'dt' }).nextElementSibling).toHaveTextContent('engine-not-an-env')
  })

  it('keeps long tag text inert and bounded in read-only details', () => {
    const run = makeRun('read-only-run', { env: 'test', tag: longTag, status: 'waiting' })
    const { container } = render(<RunSummaryHeader run={run} nodeCount={1} readOnly onApproval={vi.fn()} pendingApprovalCount={1} />)
    const tag = screen.getByTitle(longTag)
    expect(tag.textContent).toBe(longTag)
    expect(tag).toHaveClass('truncate')
    expect(tag.parentElement).toHaveClass('min-w-0')
    expect(tag.closest('dl')).toHaveClass('grid', 'grid-cols-2', 'sm:grid-cols-3', 'lg:grid-cols-7')
    expect(container.querySelector('img, script')).toBeNull()
    expect(screen.getByText('流量标识', { selector: 'dt' }).nextElementSibling).toHaveTextContent('test')
    expect(screen.queryAllByRole('button')).toHaveLength(0)
    expect(mocks.mutation.mutate).not.toHaveBeenCalled()
  })
})
