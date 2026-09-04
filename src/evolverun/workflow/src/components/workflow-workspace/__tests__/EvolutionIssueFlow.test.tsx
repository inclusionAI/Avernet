import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

const { mutate, applyBatch, eligibleBots, applyTasks, runAnalysis } = vi.hoisted(() => ({
  mutate: vi.fn(),
  applyBatch: vi.fn(),
  applyTasks: vi.fn(() => ({ data: { tasks: [] } })),
  eligibleBots: vi.fn((_suggestionId?: string, enabled?: boolean) => ({
    data: { bots: [{ botId: 'bot-1', botName: '修复 Bot', env: 'pre' }] },
    isLoading: false,
    error: null,
    enabled,
  })),
  runAnalysis: vi.fn((_flowId?: string, analysisId?: string) => ({
    data: {
      analysis: {
        analysisId: analysisId ?? 'AN-1',
        flowId: analysisId === 'AN-2' ? 'run-4' : 'run-1',
        workflowId: 'wf-1',
        status: 'completed',
        evidenceStatus: 'partial',
        requestedAtMs: 1,
        completedAtMs: 2,
        errorCode: null,
        facts: [],
        inferences: [],
        unknowns: [],
        diagnoses: [{
          diagnosisId: analysisId === 'AN-2' ? 'd-4' : 'd-1',
          flowIds: [analysisId === 'AN-2' ? 'run-4' : 'run-1'],
          nodeId: 'fetch-data',
          failureSignature: 'timeout:fetch-data',
          failureMode: 'timeout',
          severity: 'high',
          reasoning: analysisId === 'AN-2' ? '同类超时再次出现' : '上游服务响应较慢',
          evidenceEventIds: [analysisId === 'AN-2' ? 'EV-4' : 'EV-1'],
          sourceEvidence: [{
            eventId: analysisId === 'AN-2' ? 'EV-4' : 'EV-1',
            eventType: 'node_failed',
            occurredAtMs: 1,
            nodeId: 'fetch-data',
            summary: analysisId === 'AN-2' ? '第二次运行请求超时' : '请求超时',
            missing: false,
          }],
          proposal: { summary: '将超时阈值调整为 90 秒' },
        }],
      },
    },
    isLoading: false,
    isError: false,
  })),
}))

vi.mock('../../../api/hooks', () => ({
  useEvolveDiagnoses: () => ({
    data: {
      diagnoses: [
        {
          id: 1,
          diagnosis_id: 'd-1',
          analysis_id: 'AN-1',
          flow_ids: ['run-1'],
          evidence_event_ids: ['EV-1'],
          flow_id: 'run-1',
          workflow_id: 'wf-1',
          run_id: null,
          node_id: 'fetch-data',
          failure_signature: 'timeout:fetch-data',
          failure_mode: 'timeout',
          executor_type: null,
          weak_node_id: null,
          suggested_fix_kind: 'adjust-timeout',
          lesson_id_hit: null,
          error_text: '请求超时',
          reasoning: '上游服务响应较慢',
          created_by: 'owner-1',
          gmt_create: 300,
          gmt_modified: 300,
        },
        {
          id: 2,
          diagnosis_id: 'd-4',
          analysis_id: 'AN-2',
          flow_ids: ['run-4'],
          evidence_event_ids: ['EV-4'],
          flow_id: 'run-4',
          workflow_id: 'wf-1',
          run_id: null,
          node_id: 'fetch-data',
          failure_signature: 'timeout:fetch-data',
          failure_mode: 'timeout',
          executor_type: null,
          weak_node_id: null,
          suggested_fix_kind: 'adjust-timeout',
          lesson_id_hit: null,
          error_text: '上游服务再次超时',
          reasoning: '同类超时再次出现',
          created_by: 'owner-1',
          gmt_create: 250,
          gmt_modified: 250,
        },
        {
          id: 3,
          diagnosis_id: 'd-2',
          flow_id: 'run-2',
          workflow_id: 'wf-1',
          run_id: null,
          node_id: 'render-report',
          failure_signature: 'output:render-report',
          failure_mode: 'output-contract',
          executor_type: null,
          weak_node_id: null,
          suggested_fix_kind: null,
          lesson_id_hit: null,
          error_text: '输出结构不完整',
          reasoning: null,
          created_by: 'owner-1',
          gmt_create: 200,
          gmt_modified: 200,
        },
        {
          id: 4,
          diagnosis_id: 'd-3',
          flow_id: 'run-3',
          workflow_id: 'wf-1',
          run_id: null,
          node_id: 'write-report',
          failure_signature: 'retry:write-report',
          failure_mode: 'repetitive-retry',
          executor_type: null,
          weak_node_id: null,
          suggested_fix_kind: 'prompt_patch',
          lesson_id_hit: null,
          error_text: '重复尝试相同写入',
          reasoning: null,
          created_by: 'owner-1',
          gmt_create: 100,
          gmt_modified: 100,
        },
      ],
    },
    isLoading: false,
  }),
  useEvolveSuggestions: () => ({
    data: {
      suggestions: [
        {
          id: 's-1',
          diagnosisId: 'd-1',
          weakNode: 'fetch-data',
          signature: 'timeout:fetch-data',
          failureMode: 'timeout',
          kind: 'adjust-timeout',
          impactRuns: 2,
          evidenceRuns: ['run-1', 'run-4'],
          description: '将超时阈值调整为 90 秒',
          status: 'pending',
          proposalDigest: 'a'.repeat(64),
        },
        {
          id: 's-2',
          diagnosisId: 'd-3',
          weakNode: 'write-report',
          signature: 'retry:write-report',
          failureMode: 'repetitive-retry',
          kind: 'prompt_patch',
          impactRuns: 1,
          evidenceRuns: ['run-3'],
          description: '避免无差别重复写入',
          status: 'pending',
          proposalDigest: 'b'.repeat(64),
        },
      ],
    },
    isLoading: false,
    refetch: vi.fn(),
  }),
  useSuggestionApplyTasks: applyTasks,
  useWorkflowAccess: () => ({ data: { canEdit: true } }),
  useRecordSuggestionAction: () => ({ mutate }),
  useEligibleBotsForSuggestion: eligibleBots,
  useApplySuggestion: () => ({ mutateAsync: vi.fn() }),
  useApplySuggestionsBatch: () => ({ mutateAsync: applyBatch }),
  useEvolveLessons: () => ({ data: { lessons: [] }, isLoading: false }),
  useRunEvolutionAnalysis: runAnalysis,
}))

import EvolutionTab from '../EvolutionTab'

describe('issue and optimization flow', () => {
  it('keeps the list compact and opens problem actions in a detail drawer', async () => {
    render(<MemoryRouter><EvolutionTab workflowId="wf-1" section="diagnosis" /></MemoryRouter>)

    const actionableIssue = screen.getByText('fetch-data', { selector: 'span' }).closest('article')
    expect(actionableIssue).not.toBeNull()
    expect(actionableIssue).toHaveAttribute('data-layout', 'compact-issue-row')
    expect(within(actionableIssue!).getByText('将超时阈值调整为 90 秒')).toBeInTheDocument()
    expect(within(actionableIssue!).getByText('将超时阈值调整为 90 秒')).toHaveClass('line-clamp-2')
    expect(within(actionableIssue!).getByText('建议')).toBeInTheDocument()
    expect(within(actionableIssue!).queryByRole('button', { name: '采纳' })).not.toBeInTheDocument()

    await userEvent.click(within(actionableIssue!).getByRole('button', { name: '查看' }))
    const drawer = screen.getByRole('dialog', { name: '问题详情' })
    expect(within(drawer).getByText('聚合结论')).toBeInTheDocument()
    expect(within(drawer).getByText('当前建议')).toBeInTheDocument()
    expect(within(drawer).getByText('相关分析记录')).toBeInTheDocument()
    expect(within(drawer).getByText('所选分析详情')).toBeInTheDocument()
    expect(within(drawer).getByText('判断依据')).toBeInTheDocument()
    expect(within(drawer).queryByText('完整问题')).not.toBeInTheDocument()
    expect(within(drawer).queryByText('完整建议')).not.toBeInTheDocument()
    expect(within(drawer).queryByText('本次分析')).not.toBeInTheDocument()
    expect(within(drawer).getByRole('button', { name: '应用建议' })).toBeInTheDocument()

    const observingIssue = screen.getByText('render-report', { selector: 'span' }).closest('article')
    expect(observingIssue).not.toBeNull()
    expect(within(observingIssue!).getByText(/暂无可执行建议/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '查看建议' })).not.toBeInTheDocument()
  })

  it('includes suggestion impact flows in the related run count and detail links', async () => {
    render(<MemoryRouter><EvolutionTab workflowId="wf-1" section="diagnosis" /></MemoryRouter>)

    const issue = screen.getByText('fetch-data', { selector: 'span' }).closest('article')
    expect(issue).not.toBeNull()
    expect(within(issue!).getByText('影响 2 个运行')).toBeInTheDocument()

    await userEvent.click(within(issue!).getByRole('button', { name: '查看' }))
    const drawer = screen.getByRole('dialog', { name: '问题详情' })
    expect(within(drawer).getByRole('link', { name: /run-1/ })).toHaveAttribute(
      'href',
      '/runs/run-1?from=workspace&workspaceView=diagnosis&issueSignature=timeout%3Afetch-data&analysisId=AN-1',
    )
    expect(within(drawer).getByRole('link', { name: /run-4/ })).toBeInTheDocument()
  })

  it('opens the exact analysis instance from a contextual deep link', () => {
    render(
      <MemoryRouter>
        <EvolutionTab
          workflowId="wf-1"
          runId="run-1"
          analysisId="AN-1"
          issueSignature="timeout:fetch-data"
          section="diagnosis"
        />
      </MemoryRouter>,
    )

    const drawer = screen.getByRole('dialog', { name: '问题详情' })
    expect(within(drawer).getByRole('button', { name: '选择分析 run-1 AN-1' })).toBeInTheDocument()
    expect(within(drawer).getAllByText('请求超时')).toHaveLength(2)
    expect(runAnalysis).toHaveBeenCalledWith('run-1', 'AN-1', true)
  })

  it('switches between related analysis records inside the drawer', async () => {
    render(<MemoryRouter><EvolutionTab workflowId="wf-1" section="diagnosis" /></MemoryRouter>)

    const issue = screen.getByText('fetch-data', { selector: 'span' }).closest('article')
    expect(issue).not.toBeNull()
    await userEvent.click(within(issue!).getByRole('button', { name: '查看' }))

    const drawer = screen.getByRole('dialog', { name: '问题详情' })
    expect(within(drawer).getByText('相关分析记录')).toBeInTheDocument()
    expect(within(drawer).queryByText('运行证据')).not.toBeInTheDocument()

    await userEvent.click(within(drawer).getByRole('button', { name: /run-4.*AN-2/ }))
    expect(runAnalysis).toHaveBeenLastCalledWith('run-4', 'AN-2', true)
    expect(within(drawer).getByText('第二次运行请求超时')).toBeInTheDocument()
  })

  it('shows a clear empty state when the selected analysis has no detail payload', async () => {
    runAnalysis.mockReturnValueOnce({
      data: { analysis: null },
      isLoading: false,
      isError: false,
    })
    render(<MemoryRouter><EvolutionTab workflowId="wf-1" section="diagnosis" /></MemoryRouter>)

    const issue = screen.getByText('fetch-data', { selector: 'span' }).closest('article')
    expect(issue).not.toBeNull()
    await userEvent.click(within(issue!).getByRole('button', { name: '查看' }))

    const drawer = screen.getByRole('dialog', { name: '问题详情' })
    expect(within(drawer).getByText('未找到所选分析详情，请重试或打开关联运行查看。')).toBeInTheDocument()
  })

  it('selects pending suggestions and applies them as one batch', async () => {
    render(<MemoryRouter><EvolutionTab workflowId="wf-1" section="diagnosis" /></MemoryRouter>)

    await userEvent.click(screen.getByRole('checkbox', { name: '选择 fetch-data 的建议' }))
    await userEvent.click(screen.getByRole('checkbox', { name: '选择 write-report 的建议' }))

    expect(screen.getByRole('button', { name: '应用 2 条建议' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '采纳' })).not.toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: '应用 2 条建议' }))

    expect(screen.getByRole('heading', { name: '批量应用 2 条建议' })).toBeInTheDocument()
    expect(screen.getByText(/使用 clawmind-workflow skill/)).toBeInTheDocument()
    expect(eligibleBots).toHaveBeenLastCalledWith('s-1', true)
    expect(screen.getByLabelText('本次修复要求')).toHaveValue('将超时阈值调整为 90 秒\n避免无差别重复写入')

    await userEvent.click(screen.getByRole('radio', { name: /修复 Bot/ }))
    await userEvent.clear(screen.getByLabelText('本次修复要求'))
    await userEvent.type(screen.getByLabelText('本次修复要求'), '仅修改超时与重试，保留其他配置')
    applyBatch.mockResolvedValueOnce({ ok: true, status: 'running', taskId: 'EVAP-1' })
    await userEvent.click(screen.getByRole('button', { name: '确认应用 2 条' }))
    expect(applyBatch).toHaveBeenCalledWith({
      suggestionIds: ['s-1', 's-2'],
      botId: 'bot-1',
      botEnv: 'pre',
      applicationSpec: '仅修改超时与重试，保留其他配置',
    })
  })

  it('shows the selected Bot and current application progress', async () => {
    const now = Date.now()
    applyTasks.mockReturnValueOnce({
      data: {
        tasks: [{
          taskId: 'EVAP-1',
          stepId: 'EVAP-1-step-apply',
          suggestionId: 's-1',
          status: 'dispatched',
          summary: null,
          botId: 'bot-1',
          botName: '修复 Bot',
          botEnv: 'pre',
          errorMessage: null,
          appliedAt: null,
          createdAt: now - 125_000,
          updatedAt: now - 100_000,
          progress: {
            phase: 'editing_workflow',
            message: '正在修改 Workflow',
            elapsedMs: 125_000,
            updatedAtMs: now - 100_000,
            stalled: true,
            history: [{
              phase: 'planning_change',
              message: 'Agent 正在生成修改方案',
              updatedAtMs: now - 120_000,
            }, {
              phase: 'editing_workflow',
              message: '工具调用已返回：workflow_edit',
              updatedAtMs: now - 100_000,
            }],
          },
        }, {
          taskId: 'EVAP-OLD',
          stepId: 'EVAP-OLD-step-apply',
          suggestionId: 's-1',
          status: 'failed',
          summary: null,
          botId: 'bot-old',
          botName: '旧 Bot',
          botEnv: 'pre',
          errorMessage: '历史失败',
          appliedAt: 90,
          createdAt: 90,
          updatedAt: 91,
        }],
      },
    })

    render(<MemoryRouter><EvolutionTab workflowId="wf-1" section="diagnosis" /></MemoryRouter>)

    expect(screen.getByText('Bot 正在执行应用和部署')).toBeInTheDocument()
    expect(screen.getByText(/正在修改 Workflow · 已用时 2分/)).toBeInTheDocument()
    expect(screen.getByText(/超过 90 秒未更新/)).toBeInTheDocument()
    expect(screen.getByText(/Bot: 修复 Bot · pre/)).toBeInTheDocument()
    await userEvent.click(screen.getByText('执行记录（2）'))
    expect(screen.getByText('Agent 正在生成修改方案')).toBeInTheDocument()
    expect(screen.getByText('工具调用已返回：workflow_edit')).toBeInTheDocument()
  })
})
