import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

vi.mock('../../../api/hooks', () => ({
  useWorkflowHealth: () => ({ data: null }),
  useWorkflowHealthTrend: () => ({ data: [] }),
  useFlowRuns: () => ({
    data: {
      runs: [{
        flow_id: 'flow-progress-1', workflow_id: 'wf-1', status: 'failed',
        evolution_analysis_status: 'analyzing', node_count: 3, succeeded_count: 2,
        failed_count: 1, started_at: 1000, total_duration_ms: 2000,
      }],
      total: 1,
    },
    isLoading: false, isError: false, error: null, refetch: vi.fn(), isFetching: false,
  }),
  useAnalysisProgress: () => ({
    data: {
      analysisId: 'AN-PROGRESS-1', status: 'analyzing',
      progress: {
        phase: 'agent_analyzing', message: 'Agent 正在分析', elapsedMs: 42_000, updatedAtMs: 1000,
        inputSummary: {
          evidenceStatus: 'complete', evidenceTotal: 12, evidenceIncluded: 10,
          nodeCount: 3, failedNodeCount: 1, traceCount: 5, flowEventCount: 4,
          warnErrorLogCount: 2, truncated: true,
        },
      },
    },
  }),
}))
vi.mock('../../SuccessTrendCard', () => ({ SuccessTrendCard: () => <div>成功率趋势</div> }))
vi.mock('../../NodeAnalysisPanel', () => ({ default: () => <div>节点分析</div> }))

import OverviewTab from '../OverviewTab'

describe('Task Guard managed-analysis progress', () => {
  it('shows the current phase, elapsed time, and safe input summary for an analyzing run', () => {
    render(<MemoryRouter><OverviewTab workflow={{
      workflow_id: 'wf-1', workflow_title: '测试工作流', run_count: 1,
      last_status: 'failed', last_run_at: 1, updated_at: 1,
    }} /></MemoryRouter>)

    expect(screen.getByText('Agent 正在分析')).toBeInTheDocument()
    expect(screen.getByText('已用时 42秒')).toBeInTheDocument()
    expect(screen.getByText('证据 10/12 · 节点 3（失败 1）· Trace 5')).toBeInTheDocument()
    expect(screen.getByText('输入已截断')).toBeInTheDocument()
  })
})
