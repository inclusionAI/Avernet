import { act, fireEvent, render, screen, within } from '@testing-library/react'
import { focusManager, QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({ fetchJson: vi.fn(), snapshot: vi.fn(), workflow: vi.fn(), childId: 'child', childVersion: 4 as number | null, childDeploy: 2 as number | null }))
vi.mock('../../hooks/useObservedAuthEpoch', () => ({ useObservedAuthEpoch: () => 1 }))
vi.mock('@avernet/clawweb-shared/web/api/client', () => ({ fetchJson: mocks.fetchJson }))
vi.mock('../../api/hooks', () => ({
  useFlowRun: (flowId: string) => ({ data: { run: { flow_id: flowId, workflow_id: `wf-${flowId}`, workflow_version: mocks.childVersion, workflow_deploy_number: mocks.childDeploy, status: 'succeeded', node_count: 1 }, nodes: [{ flow_id: flowId, node_id: 'node-a', node_title: 'node', executor_type: flowId === 'parent' ? 'subworkflow' : 'action', status: 'succeeded' }] }, isLoading: false, isError: false, refetch: vi.fn() }),
  useDbWorkflow: mocks.workflow,
  useEvolveDiagnoses: () => ({ data: { diagnoses: [] } }), useEvolveSuggestions: () => ({ data: { suggestions: [] } }),
  useAnalyzeRun: () => ({ isPending: false }), useResetAnalysisRun: () => ({ isPending: false }),
  useAnalysisProgress: () => ({}), useRunEvolutionAnalysis: () => ({ data: null, isLoading: false, refetch: vi.fn() }),
}))
vi.mock('@avernet/clawweb-shared/web/api/hooks', () => ({ useFlowApprovals: () => ({ data: { items: [] } }) }))
vi.mock('../../components/RunSummaryHeader', () => ({ default: () => null }))
vi.mock('../../components/NodeExecutionList', () => ({ default: ({ nodes, renderSubworkflowLinks, workflowSpec }: any) => <div><span>规格 {workflowSpec?.nodes?.map((n: any) => n.id).join(',') ?? '无'}</span>{nodes.filter((n: any) => n.executor_type === 'subworkflow').map((n: any) => <div key={n.node_id}>{renderSubworkflowLinks(n)}</div>)}</div> }))
vi.mock('../../components/RunDagView', () => ({ default: ({ specNodes }: any) => <div>DAG: {specNodes.map((node: any) => node.id).join(',')}</div> }))
vi.mock('../../components/InterventionPanel', () => ({ default: () => null }))
vi.mock('../../components/ApprovalPanel', () => ({ default: () => null }))
vi.mock('../../components/AnalyzeRunBotModal', () => ({ default: () => null }))
vi.mock('../../components/RunArchivePanel', () => ({ default: () => null }))
import RunDetail from '../RunDetail'

const response = { nodes: [{ id: 'historical', title: 'Old', dependsOn: [], executor: { type: 'action' } }] }
async function openDrawer() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><MemoryRouter initialEntries={['/runs/parent']}><Routes><Route path="/runs/:flowId" element={<RunDetail />} /></Routes></MemoryRouter></QueryClientProvider>)
  fireEvent.click(await screen.findByRole('link', { name: mocks.childId }))
  const drawer = screen.getByRole('dialog', { name: '子运行详情' })
  fireEvent.click(within(drawer).getByRole('button', { name: 'DAG' }))
  return { drawer, client }
}
beforeEach(() => {
  mocks.childVersion = 4; mocks.childDeploy = 2; mocks.childId = 'child'
  mocks.snapshot.mockReset().mockResolvedValue(response)
  mocks.workflow.mockReset().mockImplementation(() => ({ data: { nodes: [{ id: 'latest' }] } }))
  mocks.fetchJson.mockReset().mockImplementation(async (url: string, init: RequestInit) => {
    if (url.endsWith('/workflow-snapshot')) return mocks.snapshot(url, init)
    return { completeness: 'best_effort', items: url.includes('/parent/') ? [{ parentNodeId: 'node-a', childFlowId: mocks.childId }] : [] }
  })
})
describe('child historical DAG', () => {
  it('loads the run-level static topology and preserves parent behavior', async () => {
    const { drawer } = await openDrawer()
    expect(await within(drawer).findByText('DAG: historical')).toBeInTheDocument()
    expect(mocks.snapshot).toHaveBeenCalledWith('/api/runs/child/workflow-snapshot', expect.objectContaining({ signal: expect.any(AbortSignal) }))
    expect(within(drawer).getByText(/静态 DAG.*不含动态节点/)).toBeInTheDocument()
    expect(mocks.fetchJson.mock.calls.every(([url]) => !url.includes('/history/'))).toBe(true)
    expect(mocks.workflow).toHaveBeenCalledWith('')
    fireEvent.click(within(drawer).getByRole('button', { name: '节点' }))
    expect(within(drawer).getByText('规格 无')).toBeInTheDocument()
    fireEvent.click(screen.getAllByRole('button', { name: 'DAG' })[0])
    expect(screen.getByText('DAG: latest')).toBeInTheDocument()
  })
  it('shows loading instead of the latest graph until the snapshot resolves', async () => {
    let resolve!: (value: typeof response) => void
    mocks.snapshot.mockReturnValue(new Promise(done => { resolve = done }))
    const { drawer } = await openDrawer()
    expect(within(drawer).getByText(/正在加载本次运行的静态 DAG 快照/)).toBeInTheDocument()
    expect(within(drawer).queryByText(/DAG: /)).not.toBeInTheDocument()
    resolve(response)
    expect(await within(drawer).findByText('DAG: historical')).toBeInTheDocument()
  })
  it('cancels an in-flight snapshot request when the drawer closes', async () => {
    mocks.snapshot.mockReturnValue(new Promise(() => {}))
    const { drawer } = await openDrawer()
    const signal = mocks.snapshot.mock.calls[0][1].signal as AbortSignal
    expect(signal.aborted).toBe(false)
    fireEvent.click(within(drawer).getByRole('button', { name: '关闭子运行详情' }))
    expect(signal.aborted).toBe(true)
  })
  it('hides a previously loaded graph when a subsequent authorization check fails', async () => {
    const { drawer } = await openDrawer()
    expect(await within(drawer).findByText('DAG: historical')).toBeInTheDocument()
    mocks.snapshot.mockRejectedValue(new Error('forbidden'))
    await act(async () => { focusManager.setFocused(false); focusManager.setFocused(true) })
    expect(await within(drawer).findByText(/无法确认本次运行的历史 DAG/)).toBeInTheDocument()
    expect(within(drawer).queryByText(/DAG: /)).not.toBeInTheDocument()
  })
  it.each(['404', '403', '409', '503', 'invalid'])('fails closed when snapshot is %s', async reason => {
    if (reason === 'invalid') mocks.snapshot.mockResolvedValue({ nodes: [{ id: 'bad' }] })
    else mocks.snapshot.mockRejectedValue(new Error(reason))
    const { drawer } = await openDrawer()
    expect(await within(drawer).findByText(/无法确认本次运行的历史 DAG/)).toBeInTheDocument()
    expect(within(drawer).queryByText(/DAG: /)).not.toBeInTheDocument()
    fireEvent.click(within(drawer).getByRole('button', { name: '节点' }))
    expect(within(drawer).getByText('规格 无')).toBeInTheDocument()
  })
  it.each([-1, null])('delegates version %s availability to the run endpoint', async version => {
    mocks.childVersion = version
    mocks.snapshot.mockRejectedValue(new Error('not available'))
    const { drawer } = await openDrawer()
    expect(await within(drawer).findByText(/无法确认本次运行的历史 DAG/)).toBeInTheDocument()
    expect(mocks.snapshot).toHaveBeenCalledTimes(1)
    expect(within(drawer).queryByText(/试运行/)).not.toBeInTheDocument()
  })
  it('URL-encodes the child flow identifier', async () => {
    mocks.childId = 'child /?#'
    const { drawer } = await openDrawer()
    expect(await within(drawer).findByText('DAG: historical')).toBeInTheDocument()
    expect(mocks.snapshot).toHaveBeenCalledWith('/api/runs/child%20%2F%3F%23/workflow-snapshot', expect.anything())
  })
  it('renders a valid empty topology rather than treating it as unavailable', async () => {
    mocks.snapshot.mockResolvedValue({ nodes: [] })
    const { drawer } = await openDrawer()
    expect(await within(drawer).findByText('DAG:')).toBeInTheDocument()
    expect(within(drawer).getByText(/静态 DAG.*不含动态节点/)).toBeInTheDocument()
  })
})
