import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { expect, it, vi } from 'vitest'

const hooks = vi.hoisted(() => ({
  useFlowRuns: vi.fn(() => ({ data: { runs: [{ flow_id: 'visible-flow', workflow_title: 'Workflow', status: 'succeeded', started_at: 1,
    node_count: 1, succeeded_count: 1, failed_count: 0, total_token_usage: 123 }], total: 1 }, isLoading: false, refetch: vi.fn() })),
  // Keep cached formal data present even when disabled, to catch accidental rendering.
  useWorkflowHealth: vi.fn(() => ({ data: { totalTokens: 999999 } })),
  useAnalyzeRun: () => ({ mutate: vi.fn() }), useDeleteFlowRun: () => ({ mutate: vi.fn() }), useRerunFlowRun: () => ({ mutate: vi.fn() }),
}))
vi.mock('@avernet/workflow/web/api/hooks', () => hooks)
vi.mock('@avernet/clawweb-shared/web/hooks/useClientUser', () => ({ getClientUser: () => ({ isAdmin: true }) }))
vi.mock('@avernet/workflow/web/components/SuccessTrendCard', () => ({ SuccessTrendCard: () => <div>正式趋势与版本对比</div> }))
vi.mock('../../components/HealthScoreCard', () => ({ HealthScoreCard: () => <div>正式健康评分</div> }))
vi.mock('@avernet/workflow/web/components/NodeAnalysisPanel', () => ({ NodeAnalysisPanel: () => <div>正式节点分析</div> }))
vi.mock('../../components/AutoHealPanel', () => ({ default: () => null }))
vi.mock('../../components/AnalyzeRunBotModal', () => ({ default: () => null }))
import WorkflowRuns from '../WorkflowRuns'

it('isolates candidate rows from formal analytics and resets the node tab when switching purpose', async () => {
  render(<MemoryRouter initialEntries={['/workflows/wf/runs']}><Routes><Route path="/workflows/:workflowId/runs" element={<WorkflowRuns />} /></Routes></MemoryRouter>)
  expect(screen.getByText('正式趋势与版本对比')).toBeInTheDocument()
  expect(screen.getByText('正式健康评分')).toBeInTheDocument()
  expect(screen.getByText((999999).toLocaleString())).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: '节点分析', exact: true }))
  expect(screen.getByText('正式节点分析')).toBeInTheDocument()
  await userEvent.selectOptions(screen.getByRole('combobox', { name: '运行用途' }), 'trial')
  expect(hooks.useFlowRuns).toHaveBeenLastCalledWith(expect.objectContaining({ purpose: 'trial' }))
  expect(hooks.useWorkflowHealth).toHaveBeenLastCalledWith(null)
  expect(screen.queryByText('正式趋势与版本对比')).not.toBeInTheDocument()
  expect(screen.queryByText('正式健康评分')).not.toBeInTheDocument()
  expect(screen.queryByText('正式节点分析')).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: '节点分析', exact: true })).not.toBeInTheDocument()
  expect(screen.queryByText((999999).toLocaleString())).not.toBeInTheDocument()
  expect(screen.getByText('123')).toBeInTheDocument()
  expect(screen.getByText('visible-flow')).toBeInTheDocument()
  await userEvent.selectOptions(screen.getByRole('combobox', { name: '运行用途' }), 'production')
  expect(hooks.useWorkflowHealth).toHaveBeenLastCalledWith('wf')
  expect(screen.getByText('正式趋势与版本对比')).toBeInTheDocument()
  expect(screen.getByText('正式健康评分')).toBeInTheDocument()
  expect(screen.getByText('visible-flow')).toBeInTheDocument()
})
