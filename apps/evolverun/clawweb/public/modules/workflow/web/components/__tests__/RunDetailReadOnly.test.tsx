import { render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import type { FlowRun, NodeExecution } from '@avernet/clawweb-shared/web/types'
import RunSummaryHeader from '../RunSummaryHeader'
import NodeExecutionList from '../NodeExecutionList'
import ApprovalPanel from '../ApprovalPanel'

const mutate = vi.fn()
vi.mock('@avernet/clawweb-shared/web/api/hooks', () => ({
  useDeleteFlowRun: () => ({ mutate }), useRerunFlowRun: () => ({ mutate }), useAbortFlowRun: () => ({ mutate }),
  useFlowApprovals: () => ({ data: { items: [{ id: 'approval-1', status: 'pending', approvalType: 'HUMAN_CONFIRM', approverNames: [], approverIds: [], approvedBy: [], rejectedBy: [], nodeId: 'n', createdAt: null }] } }),
}))
vi.mock('../NodeOutputViewer', () => ({ default: () => null }))
vi.mock('../NodeInputPanel', () => ({ default: () => null }))
vi.mock('../RenderedNodeHero', () => ({ default: () => null }))
vi.mock('../NodeStepTracePanel', () => ({ default: () => null }))
vi.mock('../AnalysisModal', () => ({ default: () => null }))
const run = { flow_id: 'child', workflow_id: 'wf', status: 'waiting', origin_bot_id: 'bot', succeeded_count: 0, failed_count: 0, total_token_usage: 0 } as FlowRun
const node = { flow_id: 'child', node_id: 'n', node_title: 'node', executor_type: 'baas-call', status: 'running', session_id: 'session', attempt: 1 } as NodeExecution

describe('read-only run detail components', () => {
  it('retains the summary but hides rerun, abort, delete, heal, and approval actions', () => {
    const view = render(<RunSummaryHeader run={run} nodeCount={1} onAutoHeal={() => {}} onApproval={() => {}} pendingApprovalCount={1} />)
    expect(screen.getByTitle('重跑：重新发送原始命令到 Bot')).toBeInTheDocument()
    expect(screen.getByTitle('中止运行中的工作流')).toBeInTheDocument()
    expect(screen.getByTitle('AI 自动诊断与修复')).toBeInTheDocument()
    expect(screen.getByTitle('审批')).toBeInTheDocument()
    expect(screen.getByTitle('删除此运行实例')).toBeInTheDocument()
    view.rerender(<RunSummaryHeader run={run} nodeCount={1} onAutoHeal={() => {}} onApproval={() => {}} pendingApprovalCount={1} readOnly />)
    expect(screen.getByText('child')).toBeInTheDocument()
    expect(screen.queryAllByRole('button')).toHaveLength(0)
    expect(mutate).not.toHaveBeenCalled()
  })

  it('keeps approval information and selected node details but hides approval and node actions', () => {
    render(<MemoryRouter><ApprovalPanel flowId="child" readOnly /><NodeExecutionList nodes={[node]} selectedNodeId="n" onSelectNode={() => {}} onAnalyze={() => {}} onApproval={() => {}} approvalNodeIds={new Set(['n'])} readOnly /></MemoryRouter>)
    expect(screen.getByText('待审批')).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /前往审批/ })).not.toBeInTheDocument()
    const table = screen.getByRole('table')
    expect(within(table).getByText('节点详情')).toBeInTheDocument()
    expect(within(table).queryByTitle('查询BaaS调用状态')).not.toBeInTheDocument()
    expect(within(table).queryByTitle('审批')).not.toBeInTheDocument()
    expect(within(table).queryByRole('button', { name: '节点分析' })).not.toBeInTheDocument()
  })
})
