import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

vi.mock('../../api/hooks', () => ({
  useFlowRun: () => ({
    data: {
      run: {
        flow_id: 'flow-suggestion-detail',
        workflow_id: 'wf-suggestion-detail',
        workflow_title: '建议测试工作流',
        status: 'failed',
        evolution_analysis_status: 'completed',
        node_count: 1,
        succeeded_count: 0,
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
  useEvolveSuggestions: () => ({
    data: {
      suggestions: [{
        id: 'SG-1',
        evidenceRuns: ['flow-suggestion-detail'],
        description: '修复建议',
        status: 'pending',
      }],
    },
    isLoading: false,
  }),
  useAnalyzeRun: () => ({ mutate: vi.fn(), isPending: false, isError: false, error: null }),
  useResetAnalysisRun: () => ({ mutate: vi.fn(), isPending: false }),
  useAnalysisProgress: () => ({ data: null, isError: false }),
  useRunEvolutionAnalysis: () => ({
    data: {
      analysis: {
        analysisId: 'AN-SUGGESTION-1',
        flowId: 'flow-suggestion-detail',
        workflowId: 'wf-suggestion-detail',
        status: 'completed',
        evidenceStatus: 'partial',
        requestedAtMs: 1,
        completedAtMs: 2,
        errorCode: null,
        facts: [],
        inferences: [],
        unknowns: [],
        diagnoses: [{
          diagnosisId: 'DG-SUGGESTION-1',
          flowIds: ['flow-suggestion-detail'],
          nodeId: 'report',
          failureSignature: 'output-contract:report',
          failureMode: 'output-contract',
          severity: 'high',
          reasoning: '输出契约不匹配',
          evidenceEventIds: [],
          sourceEvidence: [],
          proposal: { summary: '修复建议' },
        }],
      },
    },
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

describe('run detail suggestion navigation', () => {
  it('returns to issue and optimization when opened from a related run', () => {
    render(
      <MemoryRouter initialEntries={['/runs/flow-suggestion-detail?from=workspace&workspaceView=diagnosis&analysisId=AN-SUGGESTION-1&issueSignature=output-contract%3Areport']}>
        <Routes><Route path="/runs/:flowId" element={<RunDetail />} /></Routes>
      </MemoryRouter>,
    )

    expect(screen.getByRole('link', { name: '返回任务护航' })).toHaveAttribute(
      'href',
      '/workflows/workspace?workflowId=wf-suggestion-detail&tab=evolution&evoTab=diagnosis&analysisId=AN-SUGGESTION-1&issueSignature=output-contract%3Areport',
    )
  })

  it('opens the issue and optimization view for the analyzed run', () => {
    render(
      <MemoryRouter initialEntries={['/runs/flow-suggestion-detail?from=workspace']}>
        <Routes><Route path="/runs/:flowId" element={<RunDetail />} /></Routes>
      </MemoryRouter>,
    )

    expect(screen.getByRole('link', { name: '查看建议' })).toHaveAttribute(
      'href',
      '/workflows/workspace?workflowId=wf-suggestion-detail&tab=evolution&evoTab=diagnosis&runId=flow-suggestion-detail&analysisId=AN-SUGGESTION-1&issueSignature=output-contract%3Areport',
    )
    expect(screen.getByText('输出契约不匹配')).toBeInTheDocument()
    expect(screen.getByText('问题来源')).toBeInTheDocument()
  })
})
