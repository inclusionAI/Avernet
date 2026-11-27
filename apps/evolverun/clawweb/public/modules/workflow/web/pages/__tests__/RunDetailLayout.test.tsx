import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({ status: 'waiting', definitionError: false, refetchWorkflow: vi.fn(), cards: [] as Array<Record<string, unknown>> }))
vi.mock('../../api/hooks', () => ({
  useFlowRun: () => ({ data: {
    run: { flow_id: 'layout-run', workflow_id: 'layout-workflow', status: mocks.status, node_count: 3 },
    nodes: [{ flow_id: 'layout-run', node_id: 'a', node_title: '已有记录 A', status: 'succeeded', executor_type: 'action' }],
  }, isLoading: false, isError: false, refetch: vi.fn() }),
  useDbWorkflow: () => ({ isError: mocks.definitionError, refetch: mocks.refetchWorkflow, data: { nodes: [
    { id: 'a', title: '已有记录 A', executor: { type: 'action' } },
    { id: 'future', title: '尚未执行', executor: { type: 'action' }, dependsOn: ['a'] },
  ] } }),
  useEvolveDiagnoses: () => ({ data: { diagnoses: [] }, isLoading: false }),
  useEvolveSuggestions: () => ({ data: { suggestions: [] }, isLoading: false }),
  useAnalyzeRun: () => ({ isPending: false }), useResetAnalysisRun: () => ({ isPending: false }),
  useAnalysisProgress: () => ({}), useRunEvolutionAnalysis: () => ({ data: null, isLoading: false, refetch: vi.fn() }),
}))
vi.mock('@avernet/clawweb-shared/web/api/hooks', () => ({
  useFlowApprovals: () => ({ data: { items: mocks.cards }, isLoading: false, isError: false, refetch: vi.fn() }),
}))
vi.mock('../../components/RunSummaryHeader', () => ({ default: ({ run }: any) => <section aria-label="运行摘要">摘要 {run.flow_id}</section> }))
vi.mock('../../components/RunDagView', () => ({ default: ({ specNodes, onNodeClick }: any) => <div>{specNodes.map((node: any) => <button key={node.id} onClick={() => onNodeClick(node.id)}>定位节点 {node.id}</button>)}</div> }))
vi.mock('../../components/NodeExecutionList', () => ({ default: ({ selectedNodeId, locateRequest }: any) => <section aria-label="节点执行记录" data-selected={selectedNodeId} data-request={locateRequest?.requestId}>节点列表</section> }))
vi.mock('../../components/SubworkflowLinks', () => ({ default: () => null, UnattributedSubworkflowLinks: () => <div>未归属子运行入口</div>, SubworkflowProvider: ({ children }: any) => children }))
vi.mock('../../components/InterventionPanel', () => ({ default: () => <section aria-label="人工干预">原人工干预及会话内容</section> }))
vi.mock('../../components/RunArchivePanel', () => ({ default: ({ runStatus }: any) => <div>档案状态 {runStatus}</div> }))
vi.mock('../../components/AnalyzeRunBotModal', () => ({ default: () => null }))
vi.mock('../../components/AutoHealPanel', () => ({ default: () => null }))
vi.mock('../../components/ApprovalSlidePanel', () => ({ default: () => null }))
import RunDetail from '../RunDetail'

afterEach(cleanup)
beforeEach(() => {
  mocks.status = 'waiting'
  mocks.definitionError = false
  mocks.refetchWorkflow.mockReset()
  mocks.cards = [{ id: 42, status: 'pending', approvalType: 'HUMAN_CONFIRM', approverNames: ['示例审批人'], approverIds: ['approver-1'], approvedBy: [], rejectedBy: [], nodeId: 'a', createdAt: 1795651200 }]
})
function open() {
  return render(<MemoryRouter initialEntries={['/runs/layout-run?from=workspace']}><Routes><Route path="/runs/:flowId" element={<RunDetail />} /></Routes></MemoryRouter>)
}
function precedes(a: Element, b: Element) { return Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING) }

describe('run detail layout keeps existing business content', () => {
  it('shows the DAG above intervention, approvals and evolution without placing it in a tab', () => {
    open()
    const summary = screen.getByRole('region', { name: '运行摘要' })
    const dag = screen.getByRole('heading', { name: /运行 DAG/ })
    const intervention = screen.getByRole('region', { name: '人工干预' })
    const approval = screen.getByRole('heading', { name: /^审批$/ })
    const evolution = screen.getByRole('region', { name: '进化分析' })
    expect(precedes(summary, dag)).toBe(true)
    expect(precedes(dag, intervention)).toBe(true)
    expect(precedes(intervention, approval)).toBe(true)
    expect(precedes(approval, evolution)).toBe(true)
    const nav = screen.getByRole('navigation', { name: '运行详情视图' })
    expect(within(nav).getAllByRole('button').map(button => button.textContent)).toEqual(['节点', '运行档案'])
    expect(screen.getByText('未归属子运行入口')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '返回任务护航' })).toHaveAttribute('href', '/workflows/workspace?workflowId=layout-workflow')
  })

  it('offers a real workflow-definition retry rather than showing a stale DAG after an error', () => {
    mocks.definitionError = true
    open()
    expect(screen.getByRole('alert')).toHaveTextContent('工作流节点定义加载失败')
    expect(screen.queryByRole('button', { name: '定位节点 a', exact: true })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '重新加载节点定义', exact: true }))
    expect(mocks.refetchWorkflow).toHaveBeenCalledTimes(1)
  })

  it.each(['waiting', 'succeeded', 'failed', 'cancelled'])('retains the middle approval record in a %s run', status => {
    mocks.status = status
    mocks.cards[0].status = status === 'waiting' ? 'pending' : 'approved'
    open()
    expect(screen.getByText('示例审批人')).toBeVisible()
    expect(screen.getByText('节点: a')).toBeVisible()
    expect(screen.getByRole('link', { name: /前往审批/ })).toHaveAttribute('href', '/approval/42')
  })

  it('switches from archive to nodes and issues a fresh locate request even for the same selected node', () => {
    open()
    fireEvent.click(screen.getByRole('button', { name: '运行档案', exact: true }))
    expect(screen.getByText('档案状态 waiting')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '定位节点 a', exact: true }))
    const list = screen.getByRole('region', { name: '节点执行记录' })
    expect(list).toHaveAttribute('data-selected', 'a')
    const request = Number(list.getAttribute('data-request'))
    fireEvent.click(screen.getByRole('button', { name: '定位节点 a', exact: true }))
    expect(list).toHaveAttribute('data-request', String(request + 1))
    expect(screen.queryByText('档案状态 waiting')).not.toBeInTheDocument()
  })

  it('keeps locate requests monotonic after a manual tab selection clears the current request', () => {
    open()
    fireEvent.click(screen.getByRole('button', { name: '定位节点 a', exact: true }))
    const first = Number(screen.getByRole('region', { name: '节点执行记录' }).getAttribute('data-request'))
    fireEvent.click(screen.getByRole('button', { name: '节点', exact: true }))
    fireEvent.click(screen.getByRole('button', { name: '定位节点 a', exact: true }))
    expect(screen.getByRole('region', { name: '节点执行记录' })).toHaveAttribute('data-request', String(first + 1))
  })

  it('explains a DAG node with no execution record rather than inventing a list record', () => {
    open()
    fireEvent.click(screen.getByRole('button', { name: '定位节点 future', exact: true }))
    expect(screen.getByRole('status')).toHaveTextContent('节点「future」尚无执行记录')
    fireEvent.click(screen.getByRole('button', { name: '节点', exact: true }))
    expect(screen.queryByText(/尚无执行记录/)).not.toBeInTheDocument()
  })
})
