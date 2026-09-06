import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

vi.mock('../../api/hooks', () => ({
  useFlowRun: () => ({
    data: {
      run: {
        flow_id: 'flow-progress-detail',
        workflow_id: 'wf-progress-detail',
        workflow_title: '测试工作流',
        status: 'failed',
        evolution_analysis_status: 'analyzing',
        node_count: 2,
        succeeded_count: 1,
        failed_count: 1,
        started_at: 1,
      },
      nodes: [],
    },
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  }),
  useDbWorkflow: () => ({ data: { nodes: [] } }),
  useEvolveDiagnoses: () => ({ data: { diagnoses: [] }, isLoading: false }),
  useEvolveSuggestions: () => ({ data: { suggestions: [] }, isLoading: false }),
  useAnalyzeRun: () => ({ mutate: vi.fn(), isPending: false, isError: false, error: null }),
  useResetAnalysisRun: () => ({ mutate: vi.fn(), isPending: false }),
  useAnalysisProgress: () => ({
    data: {
      analysisId: 'AN-PROGRESS-DETAIL',
      status: 'analyzing',
      progress: {
        phase: 'input_ready',
        message: '分析输入已准备',
        elapsedMs: 65_000,
        updatedAtMs: 1,
        inputSummary: {
          evidenceStatus: 'partial',
          evidenceTotal: 0,
          evidenceIncluded: 0,
          nodeCount: 2,
          failedNodeCount: 1,
          traceCount: 24,
          flowEventCount: 0,
          warnErrorLogCount: 0,
          truncated: false,
        },
      },
    },
    isError: false,
  }),
  useRunEvolutionAnalysis: () => ({
    data: null,
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  }),
}))

vi.mock('../../components/RunSummaryHeader', () => ({ default: () => <div>运行摘要</div> }))
vi.mock('../../components/NodeExecutionList', () => ({ default: () => <div>节点列表</div> }))
vi.mock('../../components/RunDagView', () => ({ default: () => <div>DAG</div> }))
vi.mock('../../components/ErrorState', () => ({ default: ({ message }: { message: string }) => <div>{message}</div> }))
vi.mock('../../components/SimpleRunLogsPanel', () => ({ default: () => <div>日志</div> }))
vi.mock('../../components/AnalysisModal', () => ({ default: () => <div>节点分析</div> }))
vi.mock('../../components/InterventionPanel', () => ({ default: () => <div>人工干预</div> }))
vi.mock('../../components/AnalyzeRunBotModal', () => ({ default: () => null }))

import RunDetail from '../RunDetail'

describe('run detail managed-analysis progress', () => {
  it('shows the reported phase, elapsed time, and safe input summary while analyzing', () => {
    render(
      <MemoryRouter initialEntries={['/runs/flow-progress-detail?from=workspace']}>
        <Routes><Route path="/runs/:flowId" element={<RunDetail />} /></Routes>
      </MemoryRouter>,
    )

    expect(screen.getByText('分析输入已准备')).toBeInTheDocument()
    expect(screen.getByText('已用时 1分5秒')).toBeInTheDocument()
    expect(screen.getByText('结构化事件 0/0（不完整）· 节点 2（失败 1）· Trace 24')).toBeInTheDocument()
    expect(screen.queryByText('点击“分析”开始诊断。')).not.toBeInTheDocument()
  })
})
