import { fireEvent, render, screen, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({ fetchJson: vi.fn(), analysis: vi.fn(), nodeCount: 1 }))
vi.mock('../../hooks/useObservedAuthEpoch', () => ({ useObservedAuthEpoch: () => 1 }))
vi.mock('@avernet/clawweb-shared/web/api/client', () => ({ fetchJson: mocks.fetchJson }))
vi.mock('../../api/hooks', () => ({
  useFlowRun: (flowId: string) => ({ data: { run: { flow_id: flowId, workflow_id: `wf-${flowId}`, status: 'succeeded', node_count: 1 }, nodes: Array.from({ length: flowId === 'parent' ? mocks.nodeCount : 1 }, (_, i) => ({ flow_id: flowId, node_id: i === 0 ? 'node-a' : `node-${i}`, node_title: 'node', executor_type: flowId === 'parent' ? 'subworkflow' : 'action', status: 'succeeded', attempt: 3 })) }, isLoading: false, isError: false, refetch: vi.fn() }),
  useDbWorkflow: () => ({ data: { nodes: [] } }),
  useEvolveDiagnoses: () => ({ data: { diagnoses: [] } }),
  useEvolveSuggestions: () => ({ data: { suggestions: [] } }),
  useAnalyzeRun: () => ({ isPending: false }), useResetAnalysisRun: () => ({ isPending: false }),
  useAnalysisProgress: () => ({}), useRunEvolutionAnalysis: mocks.analysis,
}))
vi.mock('@avernet/clawweb-shared/web/api/hooks', () => ({ useFlowApprovals: () => ({ data: { items: [] } }) }))
vi.mock('../../components/RunSummaryHeader', () => ({ default: ({ run, readOnly }: { run: { flow_id: string }; readOnly: boolean }) => <div data-readonly={String(readOnly)}>摘要 {run.flow_id}</div> }))
vi.mock('../../components/NodeExecutionList', () => ({ default: ({ nodes, renderSubworkflowLinks, onSelectNode, selectedNodeId, readOnly }: any) => <div data-readonly={String(readOnly)}><button onClick={() => onSelectNode('node-a')}>选择节点</button>{selectedNodeId && <span>已选择 {selectedNodeId}</span>}{nodes.filter((node: { executor_type: string }) => node.executor_type === 'subworkflow').map((node: { node_id: string }) => <div key={node.node_id}>{renderSubworkflowLinks(node)}</div>)}</div> }))
vi.mock('../../components/RunDagView', () => ({ default: () => <div>DAG内容</div> }))
vi.mock('../../components/SimpleRunLogsPanel', () => ({ default: ({ flowId }: { flowId: string }) => <div>日志 {flowId}</div> }))
vi.mock('../../components/InterventionPanel', () => ({ default: () => <div>干预面板</div> }))
vi.mock('../../components/ApprovalPanel', () => ({ default: () => null }))
vi.mock('../../components/AnalyzeRunBotModal', () => ({ default: () => null }))
vi.mock('../../components/RunArchivePanel', () => ({ default: () => null }))

import RunDetail from '../RunDetail'

function Navigation() {
  const location = useLocation(), navigate = useNavigate()
  return <><span data-testid="location">{location.pathname}{location.search}</span><button onClick={() => navigate('/runs/other')}>切换运行</button></>
}

describe('RunDetail child drawer', () => {
  it('preserves parent node state and route, isolates child analysis, and resets on parent route changes', async () => {
    mocks.nodeCount = 1
    mocks.fetchJson.mockReset()
    mocks.fetchJson.mockImplementation(async (url: string) => ({ completeness: 'best_effort', items: url === '/api/runs/parent/subworkflows' ? [{ parentNodeId: 'node-a', childFlowId: 'child', parentAttempt: null }] : [] }))
    mocks.analysis.mockImplementation(() => ({ data: null, isLoading: false, isError: false, refetch: vi.fn() }))
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={client}><MemoryRouter initialEntries={['/runs/parent?analysisId=parent-analysis']}><Navigation /><Routes><Route path="/runs/:flowId" element={<RunDetail />} /></Routes></MemoryRouter></QueryClientProvider>)
    fireEvent.click(screen.getByRole('button', { name: '选择节点' }))
    fireEvent.click(await screen.findByRole('link', { name: 'child' }))
    const drawer = screen.getByRole('dialog', { name: '子运行详情' })
    expect(within(drawer).getByText('摘要 child')).toHaveAttribute('data-readonly', 'true')
    expect(within(drawer).getByText('选择节点').parentElement).toHaveAttribute('data-readonly', 'true')
    expect(within(drawer).queryByRole('link', { name: '返回工作流' })).not.toBeInTheDocument()
    expect(within(drawer).queryByText('干预面板')).not.toBeInTheDocument()
    expect(within(drawer).queryByRole('button', { name: '分析' })).not.toBeInTheDocument()
    expect(screen.getByText('干预面板')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '分析' })).toBeInTheDocument()
    expect(screen.getByTestId('location')).toHaveTextContent('/runs/parent?analysisId=parent-analysis')
    expect(mocks.analysis).toHaveBeenCalledWith('parent', 'parent-analysis')
    expect(mocks.analysis).toHaveBeenCalledWith('child', undefined)
    fireEvent.click(within(drawer).getByRole('button', { name: '日志' }))
    expect(within(drawer).getByText('日志 child')).toBeInTheDocument()
    fireEvent.click(within(drawer).getByRole('button', { name: '关闭子运行详情' }))
    expect(screen.getByText('已选择 node-a')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '切换运行' }))
    expect(screen.getByText('摘要 other')).toBeInTheDocument()
    expect(screen.queryByText('已选择 node-a')).not.toBeInTheDocument()
    expect(screen.queryByText('child')).not.toBeInTheDocument()
  })
  it('shares one request and poller across 32 subworkflow nodes and hides all links after a failed refresh', async () => {
    mocks.nodeCount = 32
    mocks.fetchJson.mockReset().mockImplementation(async (url: string) => {
      if (url !== '/api/runs/parent/subworkflows') return { completeness: 'best_effort', items: [] }
      if (mocks.fetchJson.mock.calls.filter(([called]) => called === url).length > 1) throw new Error('forbidden')
      return { completeness: 'best_effort', items: [
      { parentNodeId: 'node-a', childFlowId: 'child-a' },
      { parentNodeId: 'node-1', childFlowId: 'child-b' },
    ] }
    })
    mocks.analysis.mockImplementation(() => ({ data: null, isLoading: false, isError: false, refetch: vi.fn() }))
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={client}><MemoryRouter initialEntries={['/runs/parent']}><Routes><Route path="/runs/:flowId" element={<RunDetail />} /></Routes></MemoryRouter></QueryClientProvider>)
    fireEvent.click(await screen.findByRole('link', { name: 'child-a' }))
    expect(screen.getByRole('link', { name: 'child-b' })).toBeInTheDocument()
    expect(mocks.fetchJson.mock.calls.filter(([url]) => url === '/api/runs/parent/subworkflows')).toHaveLength(1)
    await client.invalidateQueries({ queryKey: ['subworkflow-node-links'] })
    expect(mocks.fetchJson.mock.calls.filter(([url]) => url === '/api/runs/parent/subworkflows')).toHaveLength(2)
    await screen.findAllByRole('alert')
    expect(screen.queryByText('child-a')).not.toBeInTheDocument()
    expect(screen.queryByText('child-b')).not.toBeInTheDocument()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('shows authorized unattributed children even when the current parent has no node executions', async () => {
    mocks.nodeCount = 0
    mocks.fetchJson.mockReset().mockImplementation(async (url: string) => ({ completeness: 'best_effort', items: url === '/api/runs/parent/subworkflows' ? [{ parentNodeId: null, childFlowId: 'unattributed' }, { parentNodeId: 'old-node', childFlowId: 'historical' }] : [] }))
    mocks.analysis.mockImplementation(() => ({ data: null, isLoading: false, isError: false, refetch: vi.fn() }))
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={client}><MemoryRouter initialEntries={['/runs/parent']}><Routes><Route path="/runs/:flowId" element={<RunDetail />} /></Routes></MemoryRouter></QueryClientProvider>)
    expect(await screen.findByText('无归属子运行（仅显示可访问记录，结果可能不完整）')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('link', { name: 'historical' }))
    expect(within(screen.getByRole('dialog')).getByText('摘要 historical')).toBeInTheDocument()
  })

})
