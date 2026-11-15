import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

vi.mock('../../api/hooks', () => ({
  useFlowRun: () => ({
    data: {
      run: {
        flow_id: 'flow-recovered-detail',
        workflow_id: 'wf-recovered-detail',
        workflow_title: '恢复节点测试工作流',
        status: 'failed',
        node_count: 2,
        succeeded_count: 1,
        failed_count: 0,
        started_at: 100,
      },
      nodes: [{
        flow_id: 'flow-recovered-detail',
        node_id: 'persisted-node',
        node_title: '已持久化节点',
        executor_type: 'cli-script',
        triggered_by: null,
        phase: null,
        branch_id: null,
        session_key: null,
        session_id: null,
        embedded_session_key: null,
        status: 'succeeded',
        started_at: '101',
        completed_at: '111',
        duration_ms: 10_000,
        input_json: null,
        output_json: '{}',
        error_text: null,
        token_usage_json: null,
        system_context_json: null,
        progress_message: null,
        resolved_prompt: null,
        attempt: 1,
      }],
    },
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  }),
  useDbWorkflow: () => ({
    data: {
      nodes: [
        { id: 'persisted-node', title: '已持久化节点', executor: { type: 'cli-script' } },
        { id: 'missing-node', title: '事件补齐节点', executor: { type: 'cli-script' } },
      ],
    },
  }),
  useEvolveDiagnoses: () => ({ data: { diagnoses: [] }, isLoading: false }),
  useEvolveSuggestions: () => ({ data: { suggestions: [] }, isLoading: false }),
  useAnalyzeRun: () => ({ mutate: vi.fn(), isPending: false, isError: false, error: null }),
  useResetAnalysisRun: () => ({ mutate: vi.fn(), isPending: false }),
  useAnalysisProgress: () => ({ data: null, isError: false }),
  useRunEvolutionAnalysis: () => ({ data: null, isLoading: false, isError: false, error: null, refetch: vi.fn() }),
}))

vi.mock('../../utils/use-recovered-flow-events', () => ({
  useRecoveredFlowEvents: () => ({
    data: [
      {
        event_id: 'e-success',
        flow_id: 'flow-recovered-detail',
        event_type: 'node_succeeded',
        node_id: 'missing-node',
        attempt: 1,
        time: 121,
        data_json: null,
        error_text: null,
      },
      {
        event_id: 'e-start',
        flow_id: 'flow-recovered-detail',
        event_type: 'node_started',
        node_id: 'missing-node',
        attempt: 1,
        time: 101,
        data_json: null,
        error_text: null,
      },
    ],
  }),
}))
vi.mock('../../components/RunSummaryHeader', () => ({ default: () => <div>运行摘要</div> }))
vi.mock('../../components/NodeExecutionList', () => ({
  default: ({ nodes }: { nodes: Array<{ node_title: string | null; progress_message: string | null }> }) => (
    <div>
      {nodes.map((node, index) => (
        <div key={index}>
          <span>{node.node_title}</span>
          {node.progress_message ? <span>{node.progress_message}</span> : null}
        </div>
      ))}
    </div>
  ),
}))
vi.mock('../../components/RunDagView', () => ({ default: () => <div>DAG</div> }))
vi.mock('../../components/ErrorState', () => ({ default: ({ message }: { message: string }) => <div>{message}</div> }))
vi.mock('../../components/SimpleRunLogsPanel', () => ({ default: () => <div>日志</div> }))
vi.mock('../../components/AnalysisModal', () => ({ default: () => <div>节点分析</div> }))
vi.mock('../../components/InterventionPanel', () => ({ default: () => <div>人工干预</div> }))
vi.mock('../../components/AnalyzeRunBotModal', () => ({ default: () => null }))
vi.mock('@avernet/clawweb-shared/web/api/hooks', () => ({
  useFlowApprovals: () => ({ data: { items: [] }, isLoading: false }),
}))

import RunDetail from '../RunDetail'

describe('run detail recovered nodes', () => {
  it('shows a spec node when flow events prove an execution record is missing', () => {
    render(
      <MemoryRouter initialEntries={['/runs/flow-recovered-detail?from=workspace']}>
        <Routes><Route path="/runs/:flowId" element={<RunDetail />} /></Routes>
      </MemoryRouter>,
    )

    expect(screen.getByText('事件补齐节点')).toBeInTheDocument()
    expect(screen.getByText('执行记录缺失，本行状态由事件日志补齐')).toBeInTheDocument()
    expect(screen.getByText('已持久化节点')).toBeInTheDocument()
  })
})
