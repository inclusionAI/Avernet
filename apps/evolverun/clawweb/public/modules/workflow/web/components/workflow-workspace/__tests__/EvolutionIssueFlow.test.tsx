import { render as renderView, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { repairSignatureKey } from '../../../../server/contracts/repair-workbench'

// Keep a local render alias for the existing interaction tests.
function render(ui: Parameters<typeof renderView>[0]) {
  const result = renderView(ui)
  return result
}
const repairApi = vi.hoisted(() => ({
  candidates: vi.fn(),
  item: vi.fn(),
  create: vi.fn(),
  disposition: vi.fn(),
  task: vi.fn(),
  revise: vi.fn(),
  cancel: vi.fn(),
  retryDispatch: vi.fn(),
  diff: vi.fn(),
}))
vi.mock('../../../api/repair-batches', () => ({ repairBatches: repairApi }))

const repairItem = (itemId = 'item-1', summary = '将超时阈值调整为 90 秒') => ({
  itemId, groupKey: 'group-1', proposalKey: 'd'.repeat(64), contentRevision: 2,
  previousItemId: null, proposal: { summary }, instruction: '保留原业务分支',
  sources: [{ kind: 'diagnosis_candidate', signature: 'timeout:fetch-data', inputDigest: 'e'.repeat(64),
    candidateId: 'candidate-1', analysisId: 'AN-1', diagnosisId: 'd-1', flowId: 'run-1' }],
  context: { signature: 'timeout:fetch-data' }, workflowId: 'wf-1', episodeKey: 'initial', state: 'pending',
  stateVersion: 0, activeTaskId: null, activeRevision: null, disposition: null, updatedAtMs: 1, sourceAvailable: true,
})

const repairPage = ({ items = [repairItem()], tasks = [], page = 1, total = items.length,
  includeHistorical = false, repairSignatures = [...new Set(items.map(item => item.context.signature))] }:
  { items?: ReturnType<typeof repairItem>[]; tasks?: Array<{ taskId: string; revision: number; phase: string; updatedAtMs: number; itemCount: number }>; page?: number; total?: number; includeHistorical?: boolean; repairSignatures?: string[] } = {}) => ({
  schemaVersion: 'workflow-repair/v2', workflowId: 'wf-1', inputDigest: 'c'.repeat(64), canEdit: true,
  items, tasks, capabilities: { generation: true, diff: true, publication: false, reason: null },
  includeHistorical, activeLookbackDays: 30, repairSignatureKeys: repairSignatures.map(repairSignatureKey),
  counts: { pending: total, processing: 0, awaiting_verification: 0, closed: 0, no_action: 0, all: total },
  page: { page, pageSize: 20, total, totalPages: Math.ceil(total / 20) }, limits: { maxItems: 100, maxRequestBytes: 65536 },
})

const lifecycle = vi.hoisted(() => ({ hideGroups: false, status: 'pending', canEdit: true, groupsLoading: false, groupsError: false, extraRuns: 0 }))
beforeEach(() => {
  sessionStorage.clear()
  lifecycle.hideGroups = false; lifecycle.status = 'pending'; lifecycle.canEdit = true
  lifecycle.groupsLoading = false; lifecycle.groupsError = false
  lifecycle.extraRuns = 0
  Object.values(repairApi).forEach(mock => mock.mockReset())
  repairApi.candidates.mockImplementation(() => new Promise(() => {}))
  repairApi.create.mockResolvedValue({ taskId: 'FIX-2', revision: 1 })
  repairApi.task.mockImplementation(() => new Promise(() => {}))
  repairApi.disposition.mockResolvedValue({})
  repairApi.cancel.mockResolvedValue({})
  repairApi.retryDispatch.mockResolvedValue({ ok: true })
})

vi.mock('../issue-groups', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../issue-groups')>();
  const hooks = await import('../../../api/hooks');
  return { ...actual, useIssueGroups: () => {
    const rows = lifecycle.hideGroups ? [] : hooks.useEvolveDiagnoses().data!.diagnoses;
    for (let i = 0; i < lifecycle.extraRuns; i++) rows.push({ ...rows[0], id: 100 + i, flow_id: `extra-run-${i}`, diagnosis_id: `extra-${i}`, analysis_id: `AN-extra-${i}` });
    const signatures = [...new Set(rows.map(row => row.failure_signature))];
    return { isLoading: lifecycle.groupsLoading, isError: lifecycle.groupsError, data: { groups: signatures.map(signature => ({
      workflowId: 'wf-1', signature, flowIds: rows.filter(r => r.failure_signature === signature).map(r => r.flow_id),
      inputDigest: 'fixture', aggregationStatus: 'not_generated', summary: null, stale: false,
      sources: rows.filter(row => row.failure_signature === signature).map(row => ({
        sourceId: String(row.id), analysisId: row.analysis_id ?? 'legacy', diagnosisId: row.diagnosis_id,
        flowId: row.flow_id, flowIds: [row.flow_id], nodeId: row.node_id, failureMode: row.failure_mode,
        completedAtMs: Number(row.gmt_create), reasoning: row.reasoning ?? row.error_text,
        evidenceEventIds: row.evidence_event_ids ?? [],
      })),
    })) } };
  } };
});

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
          status: lifecycle.status,
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
  useWorkflowAccess: () => ({ data: { canEdit: lifecycle.canEdit } }),
  useRecordSuggestionAction: () => ({ mutate }),
  useEligibleBotsForSuggestion: eligibleBots,
  useApplySuggestion: () => ({ mutateAsync: vi.fn() }),
  useApplySuggestionsBatch: () => ({ mutateAsync: applyBatch }),
  useEvolveLessons: () => ({ data: { lessons: [] }, isLoading: false }),
  useRunEvolutionAnalysis: runAnalysis,
}))

import EvolutionTab from '../EvolutionTab'

async function openIssueRepairs(expand = true) {
  if (lifecycle.hideGroups || lifecycle.groupsLoading || lifecycle.groupsError) {
    if (!screen.queryByRole('dialog', { name: '全部建议与历史' })) await userEvent.click(screen.getByRole('button', { name: '全部建议与历史' }))
  } else {
    if (!screen.queryByRole('dialog', { name: '问题详情' })) await userEvent.click(screen.getAllByRole('button', { name: '问题详情' })[0])
    await userEvent.click(screen.getByRole('button', { name: '修复建议' }))
  }
  await screen.findByRole('region', { name: '选择修复建议' })
  await screen.findAllByRole('checkbox', { name: /选择 / })
  if (expand) await userEvent.click((await screen.findAllByRole('button', { name: '查看修改与依据' }))[0])
}
async function closeIssue() {
  const drawer = screen.queryByRole('dialog', { name: '问题详情' }) ?? screen.queryByRole('dialog', { name: '全部建议与历史' })
  if (drawer) await userEvent.click(within(drawer).getByRole('button', { name: '关闭' }))
}
describe('issue and optimization flow', () => {
  it('keeps the issue list primary and defers repair loading until explicit entry', async () => {
    renderView(<MemoryRouter><EvolutionTab workflowId="wf-1" section="diagnosis" /></MemoryRouter>)
    expect(screen.queryByRole('region', { name: '修复收件箱' })).not.toBeInTheDocument()
    expect(screen.queryByText('诊断证据与历史应用')).not.toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: '问题详情' }).length).toBeGreaterThan(0)
    expect(repairApi.candidates).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button', { name: '进入修复处理' }))
    expect(repairApi.candidates).toHaveBeenCalledTimes(1)
  })
  it('stores and restores repair controls per workflow', async () => {
    repairApi.candidates.mockResolvedValue(repairPage())
    const first = render(<MemoryRouter><EvolutionTab workflowId="wf-1" section="diagnosis" /></MemoryRouter>)
    await userEvent.click(screen.getByRole('button', { name: '进入修复处理' }))
    await openIssueRepairs(false)
    await userEvent.selectOptions(screen.getByRole('combobox', { name: '建议状态' }), 'pending')
    await userEvent.click(await screen.findByRole('checkbox', { name: '包含历史未复现' }))
    await waitFor(() => expect(JSON.parse(sessionStorage.getItem('workflow-repair:wf-1')!)).toMatchObject({
      repairOpen: true, includeHistorical: true,
    }))
    first.unmount()
    repairApi.candidates.mockClear()

    render(<MemoryRouter><EvolutionTab workflowId="wf-1" section="diagnosis" /></MemoryRouter>)
    expect(screen.queryByRole('button', { name: '进入修复处理' })).not.toBeInTheDocument()
    await waitFor(() => expect(repairApi.candidates).toHaveBeenCalledWith('wf-1', expect.objectContaining({
      state: 'all', page: 1, pageSize: 1, includeHistorical: true,
    })))
  })
  it('opens legacy evidence directly for a workflow issue deep link', () => {
    renderView(<MemoryRouter><EvolutionTab workflowId="wf-1" issueSignature="timeout:fetch-data" section="diagnosis" /></MemoryRouter>)
    expect(screen.getByRole('dialog', { name: '问题详情' })).toBeInTheDocument()
  })
  it.each(['pending', 'applying', 'applied_unverified'])('preserves suggestion-only controls for %s', async (status) => {
    lifecycle.hideGroups = true
    lifecycle.status = status
    render(<MemoryRouter><EvolutionTab workflowId="wf-1" section="diagnosis" /></MemoryRouter>)
    const row = screen.getByText('将超时阈值调整为 90 秒').closest('article')!
    expect(row).not.toBeNull()
    expect(screen.queryByText(/当前工作流暂无已记录异常/)).not.toBeInTheDocument()
    if (status === 'pending') {
      expect(within(row).getByRole('button', { name: '应用建议' })).toBeInTheDocument()
    } else if (status === 'applied_unverified') {
      expect(within(row).getByRole('button', { name: '确认有效' })).toBeInTheDocument()
      expect(within(row).getByRole('button', { name: '未达预期' })).toBeInTheDocument()
      const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
      await userEvent.click(within(row).getByRole('button', { name: '确认有效' }))
      expect(mutate).toHaveBeenLastCalledWith(expect.objectContaining({ suggestionId: 's-1', action: 'verified' }), expect.anything())
      confirm.mockRestore()
    } else {
      expect(within(row).getByText('应用中')).toBeInTheDocument()
      expect(within(row).queryByRole('button', { name: '应用建议' })).not.toBeInTheDocument()
    }
  })
  it('preserves legacy apply when Pack generation is unavailable', async () => {
    repairApi.candidates.mockResolvedValueOnce({
      ...repairPage(),
      capabilities: { generation: false, diff: false, publication: false, reason: 'AIS unavailable' },
    })
    render(<MemoryRouter><EvolutionTab workflowId="wf-1" section="diagnosis" /></MemoryRouter>)
    await userEvent.click(screen.getByRole('button', { name: '进入修复处理' }))

    const issue = screen.getByText('fetch-data', { selector: 'span' }).closest('article')!
    await userEvent.click(within(issue).getByRole('button', { name: '问题详情' }))
    await userEvent.click(screen.getByRole('button', { name: '证据与历史' }))
    await userEvent.click(screen.getByText('历史建议与任务（独立于本次修复）'))
    await userEvent.click(await screen.findByRole('button', { name: '应用建议' }))
    expect(screen.getByRole('dialog', { name: '选择 Bot 自动应用建议' })).toBeInTheDocument()
  })
  it('does not restore legacy apply for a Pack item on another server page', async () => {
    lifecycle.hideGroups = true
    repairApi.candidates.mockResolvedValueOnce(repairPage({ items: [], total: 21, repairSignatures: ['timeout:fetch-data'] }))
    render(<MemoryRouter><EvolutionTab workflowId="wf-1" section="diagnosis" /></MemoryRouter>)
    await userEvent.click(screen.getByRole('button', { name: '进入修复处理' }))

    const followup = await screen.findByRole('region', { name: '已有建议跟进' })
    const offPageSuggestion = within(followup).getByText('将超时阈值调整为 90 秒').closest('article')!
    expect(offPageSuggestion).not.toBeNull()
    expect(within(offPageSuggestion).queryByRole('button', { name: '应用建议' })).not.toBeInTheDocument()
  })
  it('keeps suggestion-only controls read-only without workflow edit permission', () => {
    lifecycle.hideGroups = true
    lifecycle.canEdit = false
    render(<MemoryRouter><EvolutionTab workflowId="wf-1" section="diagnosis" /></MemoryRouter>)
    expect(screen.getByText('将超时阈值调整为 90 秒')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '应用建议' })).not.toBeInTheDocument()
  })
  it('keeps the list compact and opens problem actions in a detail drawer', async () => {
    render(<MemoryRouter><EvolutionTab workflowId="wf-1" section="diagnosis" /></MemoryRouter>)

    const actionableIssue = screen.getByText('fetch-data', { selector: 'span' }).closest('article')
    expect(actionableIssue).not.toBeNull()
    expect(actionableIssue).toHaveAttribute('data-layout', 'compact-issue-row')
    expect(within(actionableIssue!).getByText('节点')).toBeInTheDocument()
    expect(within(actionableIssue!).getByText('问题类型')).toBeInTheDocument()
    expect(within(actionableIssue!).getByText('执行超时')).toBeInTheDocument()
    expect(within(actionableIssue!).queryByText('将超时阈值调整为 90 秒')).not.toBeInTheDocument()
    expect(screen.queryByRole('region', { name: '已有建议跟进' })).not.toBeInTheDocument()
    expect(within(actionableIssue!).queryByRole('button', { name: '采纳' })).not.toBeInTheDocument()

    await userEvent.click(within(actionableIssue!).getByRole('button', { name: '问题详情' }))
    const drawer = screen.getByRole('dialog', { name: '问题详情' })
    expect(within(drawer).getByRole('heading', { name: '问题原因' })).toBeInTheDocument()
    expect(within(drawer).queryByText('所选分析详情')).not.toBeInTheDocument()
    await userEvent.click(within(drawer).getByRole('button', { name: '证据与历史' }))
    expect(within(drawer).queryByText('本次修复范围')).not.toBeInTheDocument()
    expect(within(drawer).getByText('已有建议')).toBeInTheDocument()
    expect(within(drawer).getByText('相关分析记录')).toBeInTheDocument()
    expect(within(drawer).getByText('所选分析详情')).toBeInTheDocument()
    expect(within(drawer).getByText('判断依据')).toBeInTheDocument()
    expect(within(drawer).queryByText('完整问题')).not.toBeInTheDocument()
    expect(within(drawer).queryByText('完整建议')).not.toBeInTheDocument()
    expect(within(drawer).queryByText('本次分析')).not.toBeInTheDocument()
    await userEvent.click(within(drawer).getByText('历史建议与任务（独立于本次修复）'))
    expect(within(drawer).queryByRole('button', { name: '应用建议' })).not.toBeInTheDocument()
    await closeIssue()

    const observingIssue = screen.getByText('render-report', { selector: 'span' }).closest('article')
    expect(observingIssue).not.toBeNull()
    expect(within(observingIssue!).getByRole('button', { name: '问题详情' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '查看建议' })).not.toBeInTheDocument()
  })

  it('includes suggestion impact flows in the related run count and detail links', async () => {
    render(<MemoryRouter><EvolutionTab workflowId="wf-1" section="diagnosis" /></MemoryRouter>)

    const issue = screen.getByText('fetch-data', { selector: 'span' }).closest('article')
    expect(issue).not.toBeNull()
    expect(within(issue!).getByText('问题累计涉及 2 个运行')).toBeInTheDocument()

    await userEvent.click(within(issue!).getByRole('button', { name: '问题详情' }))
    const drawer = screen.getByRole('dialog', { name: '问题详情' })
    await userEvent.click(within(drawer).getByRole('button', { name: '证据与历史' }))
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
    expect(within(drawer).getAllByText('请求超时')).toHaveLength(1)
    expect(runAnalysis).toHaveBeenCalledWith('run-1', 'AN-1', true)
  })

  it('switches between related analysis records inside the drawer', async () => {
    render(<MemoryRouter><EvolutionTab workflowId="wf-1" section="diagnosis" /></MemoryRouter>)

    const issue = screen.getByText('fetch-data', { selector: 'span' }).closest('article')
    expect(issue).not.toBeNull()
    await userEvent.click(within(issue!).getByRole('button', { name: '问题详情' }))

    const drawer = screen.getByRole('dialog', { name: '问题详情' })
    await userEvent.click(within(drawer).getByRole('button', { name: '证据与历史' }))
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
    await userEvent.click(within(issue!).getByRole('button', { name: '问题详情' }))

    const drawer = screen.getByRole('dialog', { name: '问题详情' })
    await userEvent.click(within(drawer).getByRole('button', { name: '证据与历史' }))
    expect(within(drawer).getByText('未找到所选分析详情，请重试或打开关联运行查看。')).toBeInTheDocument()
  })

  it('does not expose the legacy group repair and direct Bot deployment path', async () => {
    render(<MemoryRouter><EvolutionTab workflowId="wf-1" section="diagnosis" /></MemoryRouter>)

    expect(screen.queryByRole('checkbox', { name: '选择 fetch-data 的建议' })).not.toBeInTheDocument()
    const issue = screen.getByText('fetch-data', { selector: 'span' }).closest('article')!
    await userEvent.click(within(issue).getByRole('button', { name: '问题详情' }))
    expect(screen.queryByRole('region', { name: '本次修复范围' })).not.toBeInTheDocument()
    expect(screen.queryByText('确认修复并部署')).not.toBeInTheDocument()
    expect(screen.queryByText('选择 Bot 自动应用建议')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '应用 2 条建议' })).not.toBeInTheDocument()
  })

  it('merges task activity and repair items into the original issue list and only generates a Pack draft', async () => {
    repairApi.task.mockResolvedValueOnce({
      workflowId: 'wf-1', taskId: 'FIX-1', capabilities: { generation: true, diff: true, publication: false, reason: null },
      execution: { status: 'succeeded', errorCode: null, jobId: 'job-1', attempt: 1, executionId: 'exec-1', rawStatus: 'success' },
      latestAttempt: {
        workflowId: 'wf-1', taskId: 'FIX-1', revision: 2, phase: 'review', stateVersion: 1, requestId: 'request-1', requestDigest: 'f'.repeat(64),
        input: { schemaVersion: 'workflow-repair/v2', taskId: 'FIX-1', revision: 2,
          baseline: { workflowId: 'wf-1', packId: 'pack', releaseRevision: 1, activeDeployNumber: null, specDigest: 'a'.repeat(64), repoId: 'repo', specPath: 'workflow.yaml', packCommit: 'b'.repeat(40), packDigest: 'c'.repeat(40) },
          items: [{ itemId: 'item-1', groupKey: 'group-1', proposalKey: 'd'.repeat(64), contentRevision: 2, previousItemId: null, proposal: { summary: '将超时阈值调整为 90 秒' }, instruction: '保留原业务分支', sources: [{ kind: 'diagnosis_candidate', signature: 'timeout:fetch-data', inputDigest: 'e'.repeat(64), candidateId: 'candidate-1', analysisId: 'AN-1', diagnosisId: 'd-1', flowId: 'run-1' }] }],
          excludedSources: { count: 0, digest: '0'.repeat(64) }, instructions: '保留原业务分支', parentCandidateCommit: null, feedback: '', previousReportRef: null, taskBranch: 'repair/FIX-1' },
        draft: { candidateCommit: 'f'.repeat(40), summary: '候选修复已生成' }, checks: {}, candidateDigest: '1'.repeat(64), checksDigest: '2'.repeat(64), error: null, createdAtMs: 1, updatedAtMs: 2,
      },
      latestSuccessful: null, revisions: [],
    })
    repairApi.candidates.mockResolvedValueOnce(repairPage({
      tasks: [{ taskId: 'FIX-1', revision: 2, phase: 'review', updatedAtMs: 2, itemCount: 1 }],
    }))
    render(<MemoryRouter><EvolutionTab workflowId="wf-1" section="diagnosis" /></MemoryRouter>)
    await userEvent.click(screen.getByRole('button', { name: '进入修复处理' }))

    expect(await screen.findByRole('region', { name: '处理任务' })).toHaveTextContent('FIX-1')
    const issue = screen.getByText('fetch-data', { selector: 'span' }).closest('article')!
    expect(within(issue).queryByRole('checkbox')).not.toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: '查看任务' }))
    const taskDialog = await screen.findByRole('dialog', { name: '修复任务详情' })
    expect(within(taskDialog).getByText('候选修复已生成')).toBeInTheDocument()
    expect(within(taskDialog).getByText(/尚未应用到工作流或部署/)).toBeInTheDocument()
    await userEvent.click(within(taskDialog).getByRole('button', { name: '关闭' }))

    expect(screen.getByRole('button', { name: '生成 Pack 草稿（0）' })).toBeDisabled()
    expect(screen.getByText('已有可继续的修复任务，请先在现有任务中审阅、反馈或取消。')).toBeInTheDocument()
  })



  it('starts with no selected suggestions and requires an explicit selection before generation', async () => {
    repairApi.candidates.mockResolvedValue(repairPage())
    render(<MemoryRouter><EvolutionTab workflowId="wf-1" section="diagnosis" /></MemoryRouter>)
    await userEvent.click(screen.getByRole('button', { name: '进入修复处理' }))
    await openIssueRepairs(false)
    const checkbox = await screen.findByRole('checkbox', { name: '选择 将超时阈值调整为 90 秒' })
    expect(checkbox).not.toBeChecked()
    expect(screen.getByRole('button', { name: '生成 Pack 草稿（0）' })).toBeDisabled()
    await userEvent.click(checkbox)
    expect(screen.getByRole('button', { name: '生成 Pack 草稿（1）' })).toBeEnabled()
  })

  it.each(['loading', 'error'])('keeps repair controls usable when issue summaries are %s', async state => {
    lifecycle.groupsLoading = state === 'loading'; lifecycle.groupsError = state === 'error'
    lifecycle.hideGroups = true
    repairApi.candidates.mockResolvedValue(repairPage())
    render(<MemoryRouter><EvolutionTab workflowId="wf-1" section="diagnosis" /></MemoryRouter>)
    await userEvent.click(screen.getByRole('button', { name: '进入修复处理' }))
    await openIssueRepairs(false)
    expect(await screen.findByRole('checkbox', { name: '选择 将超时阈值调整为 90 秒' })).toBeVisible()
    expect(screen.getByRole('button', { name: '生成 Pack 草稿（0）' })).toBeDisabled()
    expect(screen.queryByText(/当前工作流暂无已记录异常/)).not.toBeInTheDocument()
  })

  it('retries a failed detail locally and can inspect standalone historical suggestions', async () => {
    lifecycle.hideGroups = true
    repairApi.candidates.mockResolvedValue(repairPage())
    repairApi.item.mockRejectedValueOnce(new Error('读取超时')).mockResolvedValueOnce(repairItem())
    render(<MemoryRouter><EvolutionTab workflowId="wf-1" section="diagnosis" /></MemoryRouter>)
    await userEvent.click(screen.getByRole('button', { name: '进入修复处理' }))
    await openIssueRepairs()
    const drawer = screen.getByRole('dialog', { name: '全部建议与历史' })
    expect(await within(drawer).findByRole('alert')).toHaveTextContent('读取超时')
    await userEvent.click(within(drawer).getByRole('button', { name: '重试详情' }))
    expect(await within(drawer).findByText('技术详情（来源标识与完整载荷）')).toBeInTheDocument()
    expect(within(drawer).queryByRole('alert')).not.toBeInTheDocument()
    expect(repairApi.item).toHaveBeenCalledTimes(2)
    expect(repairApi.create).not.toHaveBeenCalled()
  })

  it('opens one suggestion in the shared drawer without expanding evidence in the problem list', async () => {
    repairApi.candidates.mockResolvedValue(repairPage({ items: [repairItem(), repairItem('item-2', '增加重试')] }))
    const completePrompt = '完整修改目标'.repeat(80)
    repairApi.item.mockResolvedValue({ ...repairItem(), proposal: { summary: '将超时阈值调整为 90 秒',
      operations: [{ op: 'replace', nodeId: 'fetch-data', path: '/executor/prompt', value: completePrompt }] },
      context: { signature: 'timeout:fetch-data', diagnoses: [{ nodeId: 'fetch-data', reasoning: '诊断正文', flowId: 'run-1', analysisId: 'AN-1' }] } })
    render(<MemoryRouter><EvolutionTab workflowId="wf-1" section="diagnosis" /></MemoryRouter>)
    await userEvent.click(screen.getByRole('button', { name: '进入修复处理' }))
    await openIssueRepairs(false)
    const checkbox = await screen.findByRole('checkbox', { name: '选择 将超时阈值调整为 90 秒' })
    const candidate = checkbox.closest('article')!
    expect(repairApi.item).not.toHaveBeenCalled()
    expect(within(candidate).queryByText('查看建议与证据')).not.toBeInTheDocument()
    await openIssueRepairs()
    const drawer = screen.getByRole('dialog', { name: '问题详情' })
    expect(within(drawer).getByRole('button', { name: '修复建议' })).toHaveAttribute('aria-pressed', 'true')
    expect(within(drawer).getByRole('checkbox', { name: '选择 增加重试' })).toBeInTheDocument()
    expect(await within(drawer).findByText(completePrompt)).toBeInTheDocument()
    expect(within(candidate).queryByText('诊断正文')).not.toBeVisible()
    await userEvent.click(within(drawer).getByRole('button', { name: '关闭' }))
    await openIssueRepairs()
    expect(repairApi.item).toHaveBeenCalledTimes(1)
  })

  it('keeps a diagnosis cluster when its incompatible legacy repair source was skipped', async () => {
    repairApi.candidates.mockResolvedValueOnce(repairPage({ items: [], total: 0, repairSignatures: [] }))
    render(<MemoryRouter><EvolutionTab workflowId="wf-1" section="diagnosis" /></MemoryRouter>)

    await userEvent.click(screen.getByRole('button', { name: '进入修复处理' }))
    expect(await screen.findByRole('region', { name: '处理任务' })).toBeInTheDocument()
    const issue = screen.getByText('write-report', { selector: 'span' }).closest('article')!
    expect(issue).not.toBeNull()
    await userEvent.click(within(issue).getByRole('button', { name: '问题详情' }))
    const drawer = screen.getByRole('dialog', { name: '问题详情' })
    await userEvent.click(within(drawer).getByRole('button', { name: '证据与历史' }))
    await userEvent.click(within(drawer).getByText('历史建议与任务（独立于本次修复）'))
    expect(within(drawer).getByRole('button', { name: '应用建议' })).toBeInTheDocument()
  })

  it('loads the selected repair state from the server before paginating it', async () => {
    repairApi.candidates.mockImplementation((_workflowId: string, query: { state?: string }) => Promise.resolve(query.state === 'pending'
      ? repairPage({ items: [repairItem('pending-on-later-page', '后续页待处理建议')], total: 1 })
      : repairPage({ items: [{ ...repairItem('processing-first-page', '首页处理中建议'), state: 'processing' }], total: 21 })))
    render(<MemoryRouter><EvolutionTab workflowId="wf-1" section="diagnosis" /></MemoryRouter>)
    await userEvent.click(screen.getByRole('button', { name: '进入修复处理' }))

    await openIssueRepairs(false)
    expect(await screen.findByText('首页处理中建议')).toBeInTheDocument()
    await userEvent.selectOptions(screen.getByRole('combobox', { name: '建议状态' }), 'pending')
    expect(await screen.findByText('后续页待处理建议')).toBeInTheDocument()
  })

  it('lets users explicitly include historical repair items', async () => {
    repairApi.candidates.mockImplementation((_workflowId: string, query: { includeHistorical?: boolean }) => Promise.resolve(query.includeHistorical
      ? repairPage({ items: [repairItem('historical-item', '历史未复现建议')], includeHistorical: true })
      : repairPage({ items: [], total: 0 })))
    render(<MemoryRouter><EvolutionTab workflowId="wf-1" section="diagnosis" /></MemoryRouter>)
    await userEvent.click(screen.getByRole('button', { name: '进入修复处理' }))

    const history = await screen.findByRole('checkbox', { name: '包含历史未复现' })
    await userEvent.click(history)
    await openIssueRepairs(false)
    expect(await screen.findByText('历史未复现建议')).toBeInTheDocument()
    expect(repairApi.candidates).toHaveBeenLastCalledWith('wf-1', expect.objectContaining({ includeHistorical: true, page: 1, pageSize: 20 }))
  })

  it('drops the old selection while a changed history scope is loading', async () => {
    repairApi.candidates.mockImplementation((_workflowId, query) => query.includeHistorical
      ? new Promise(() => {}) : Promise.resolve(repairPage()))
    render(<MemoryRouter><EvolutionTab workflowId="wf-1" section="diagnosis" /></MemoryRouter>)
    await userEvent.click(screen.getByRole('button', { name: '进入修复处理' }))

    await openIssueRepairs(false)
    await userEvent.click(await screen.findByRole('checkbox', { name: '选择 将超时阈值调整为 90 秒' }))
    expect(screen.getByRole('button', { name: '生成 Pack 草稿（1）' })).toBeEnabled()
    await closeIssue()
    await userEvent.click(screen.getByRole('checkbox', { name: '包含历史未复现' }))

    expect(screen.queryByRole('button', { name: /生成 Pack 草稿/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('region', { name: '处理任务' })).not.toBeInTheDocument()
  })

  it('loads full evidence only when a repair item is expanded', async () => {
    repairApi.candidates.mockResolvedValue(repairPage())
    repairApi.item.mockResolvedValueOnce({ ...repairItem(), context: { signature: 'timeout:fetch-data', evidencePayload: '完整证据载荷' } })
    render(<MemoryRouter><EvolutionTab workflowId="wf-1" section="diagnosis" /></MemoryRouter>)
    await userEvent.click(screen.getByRole('button', { name: '进入修复处理' }))

    await openIssueRepairs()
    expect(await screen.findByText(/\u5b8c\u6574\u8bc1\u636e\u8f7d\u8377/)).toBeInTheDocument()
    expect(repairApi.item).toHaveBeenCalledWith('wf-1', 'item-1')
  })

  it('drops repair state immediately when the workflow changes', async () => {
    repairApi.candidates.mockImplementation((workflowId: string) => workflowId === 'wf-1'
      ? Promise.resolve(repairPage({ items: [repairItem('old-item', '旧工作流修复项')] }))
      : new Promise(() => {}))
    const view = render(<MemoryRouter><EvolutionTab workflowId="wf-1" section="diagnosis" /></MemoryRouter>)
    await userEvent.click(screen.getByRole('button', { name: '进入修复处理' }))
    await openIssueRepairs(false)
    expect(await screen.findByText('旧工作流修复项')).toBeInTheDocument()

    view.rerender(<MemoryRouter><EvolutionTab workflowId="wf-2" section="diagnosis" /></MemoryRouter>)
    expect(screen.queryByText('旧工作流修复项')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '进入修复处理' })).toBeInTheDocument()
  })

  it('creates a Pack draft when no active repair task exists', async () => {
    repairApi.candidates.mockResolvedValue(repairPage())
    render(<MemoryRouter><EvolutionTab workflowId="wf-1" section="diagnosis" /></MemoryRouter>)
    await userEvent.click(screen.getByRole('button', { name: '进入修复处理' }))

    await openIssueRepairs(false)
    await userEvent.click(await screen.findByRole('checkbox', { name: '选择 将超时阈值调整为 90 秒' }))
    await closeIssue()
    await userEvent.click(screen.getByRole('button', { name: '生成 Pack 草稿（1）' }))
    const dialog = screen.getByRole('dialog', { name: '生成 Pack 草稿' })
    expect(within(dialog).getByText(/只生成可审阅候选，不会应用或部署/)).toBeInTheDocument()
    await userEvent.click(within(dialog).getByRole('button', { name: '确认生成' }))
    await waitFor(() => expect(repairApi.create).toHaveBeenCalledWith(expect.objectContaining({
      workflowId: 'wf-1', itemIds: ['item-1'], inputDigest: 'c'.repeat(64),
    })))
    expect(await screen.findByRole('dialog', { name: '修复任务详情' })).toBeVisible()
    expect(repairApi.task).toHaveBeenCalledWith('FIX-2')
  })

  it('rejects an oversized Pack draft before posting it', async () => {
    repairApi.candidates.mockResolvedValue({ ...repairPage(), limits: { maxItems: 100, maxRequestBytes: 300 } })
    render(<MemoryRouter><EvolutionTab workflowId="wf-1" section="diagnosis" /></MemoryRouter>)
    await userEvent.click(screen.getByRole('button', { name: '进入修复处理' }))
    await openIssueRepairs(false)
    await userEvent.click(await screen.findByRole('checkbox', { name: '选择 将超时阈值调整为 90 秒' }))
    await closeIssue()
    await userEvent.click(screen.getByRole('button', { name: '生成 Pack 草稿（1）' }))
    const dialog = screen.getByRole('dialog', { name: '生成 Pack 草稿' })
    await userEvent.type(within(dialog).getByRole('textbox', { name: '本次修复要求' }), '修'.repeat(100))
    await userEvent.click(within(dialog).getByRole('button', { name: '确认生成' }))

    expect(within(dialog).getByRole('alert')).toHaveTextContent('请求内容过大，请缩短说明或减少选择。')
    expect(repairApi.create).not.toHaveBeenCalled()
  })

  it('reuses a draft request ID only for an exact retry', async () => {
    repairApi.candidates.mockResolvedValue(repairPage())
    repairApi.create.mockRejectedValueOnce(new Error('response lost'))
      .mockRejectedValueOnce(new Error('response lost again'))
      .mockResolvedValueOnce({ taskId: 'FIX-2', revision: 1 })
    render(<MemoryRouter><EvolutionTab workflowId="wf-1" section="diagnosis" /></MemoryRouter>)
    await userEvent.click(screen.getByRole('button', { name: '进入修复处理' }))

    await openIssueRepairs(false)
    await userEvent.click(await screen.findByRole('checkbox', { name: '选择 将超时阈值调整为 90 秒' }))
    await closeIssue()
    await userEvent.click(screen.getByRole('button', { name: '生成 Pack 草稿（1）' }))
    const dialog = screen.getByRole('dialog', { name: '生成 Pack 草稿' })
    const instructions = within(dialog).getByRole('textbox', { name: '本次修复要求' })
    await userEvent.type(instructions, '保留现有分支')
    await userEvent.click(within(dialog).getByRole('button', { name: '确认生成' }))
    await waitFor(() => expect(repairApi.create).toHaveBeenCalledTimes(1))
    const firstId = repairApi.create.mock.calls[0][0].requestId
    await userEvent.click(within(dialog).getByRole('button', { name: '确认生成' }))
    await waitFor(() => expect(repairApi.create).toHaveBeenCalledTimes(2))
    expect(repairApi.create.mock.calls[1][0].requestId).toBe(firstId)
    await userEvent.type(instructions, '，并增加超时')
    await userEvent.click(within(dialog).getByRole('button', { name: '确认生成' }))
    await waitFor(() => expect(repairApi.create).toHaveBeenCalledTimes(3))
    expect(repairApi.create.mock.calls[2][0].requestId).not.toBe(firstId)
  })

  it.each([
    { state: 'pending', action: 'no_action', label: '暂不处理', nextState: 'no_action' },
    { state: 'no_action', action: 'restore', label: '恢复', nextState: 'pending' },
  ])('closes stale details after $action removes the item from the $state filter', async ({ state, action, label, nextState }) => {
    let changed = false
    const item = { ...repairItem(), state, stateVersion: 3 }
    const updated = { ...item, state: nextState, stateVersion: 4 }
    repairApi.candidates.mockImplementation((_workflowId, query) => Promise.resolve(repairPage({
      items: query.state === 'all' || query.state === (changed ? nextState : state) ? [changed ? updated : item] : [],
    })))
    repairApi.item.mockImplementation(() => Promise.resolve(changed ? updated : item))
    repairApi.disposition.mockImplementation(() => {
      changed = true
      return Promise.resolve({ itemId: item.itemId, state: nextState, stateVersion: 4, disposition: null })
    })
    render(<MemoryRouter><EvolutionTab workflowId="wf-1" section="diagnosis" /></MemoryRouter>)
    await userEvent.click(screen.getByRole('button', { name: '进入修复处理' }))
    await openIssueRepairs(false)
    await userEvent.selectOptions(screen.getByRole('combobox', { name: '建议状态' }), state)
    await userEvent.click((await screen.findAllByRole('button', { name: '查看修改与依据' }))[0])
    await userEvent.click(await screen.findByRole('button', { name: `${label} 将超时阈值调整为 90 秒` }))
    const dialog = screen.getByRole('dialog', { name: action === 'restore' ? '恢复待处理' : '暂不处理' })
    await userEvent.type(within(dialog).getByRole('textbox', { name: '处置原因' }), '重新评估处理范围')
    await userEvent.click(within(dialog).getByRole('button', { name: '确认' }))

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    await openIssueRepairs(false)
    await userEvent.selectOptions(screen.getByRole('combobox', { name: '建议状态' }), nextState)
    await userEvent.click((await screen.findAllByRole('button', { name: '查看修改与依据' }))[0])
    const reopened = screen.getByRole('dialog', { name: '问题详情' })
    await waitFor(() => expect(within(reopened).getByText(/"stateVersion": 4/)).toBeInTheDocument())
    expect(within(reopened).getByRole('button', { name: `${action === 'restore' ? '暂不处理' : '恢复'} 将超时阈值调整为 90 秒` })).toBeEnabled()
  })

  it('reuses a disposition request ID only for an exact retry', async () => {
    repairApi.candidates.mockResolvedValue(repairPage())
    repairApi.disposition.mockRejectedValueOnce(new Error('response lost'))
      .mockRejectedValueOnce(new Error('response lost again'))
      .mockResolvedValueOnce({})
    render(<MemoryRouter><EvolutionTab workflowId="wf-1" section="diagnosis" /></MemoryRouter>)
    await userEvent.click(screen.getByRole('button', { name: '进入修复处理' }))

    await openIssueRepairs()
    await userEvent.click(await screen.findByRole('button', { name: '暂不处理 将超时阈值调整为 90 秒' }))
    const dialog = screen.getByRole('dialog', { name: '暂不处理' })
    const reason = within(dialog).getByRole('textbox', { name: '处置原因' })
    await userEvent.type(reason, '等待更多证据')
    await userEvent.click(within(dialog).getByRole('button', { name: '确认' }))
    await waitFor(() => expect(repairApi.disposition).toHaveBeenCalledTimes(1))
    const firstId = repairApi.disposition.mock.calls[0][1].requestId
    await userEvent.click(within(dialog).getByRole('button', { name: '确认' }))
    await waitFor(() => expect(repairApi.disposition).toHaveBeenCalledTimes(2))
    expect(repairApi.disposition.mock.calls[1][1].requestId).toBe(firstId)
    await userEvent.type(reason, '，下周复核')
    await userEvent.click(within(dialog).getByRole('button', { name: '确认' }))
    await waitFor(() => expect(repairApi.disposition).toHaveBeenCalledTimes(3))
    expect(repairApi.disposition.mock.calls[2][1].requestId).not.toBe(firstId)
  })

  it('keeps the problem list usable when repair item loading fails', async () => {
    repairApi.candidates.mockRejectedValueOnce(new Error('Repair request failed'))
    render(<MemoryRouter><EvolutionTab workflowId="wf-1" section="diagnosis" /></MemoryRouter>)
    await userEvent.click(screen.getByRole('button', { name: '进入修复处理' }))
    expect(await screen.findByText('修复任务与处理状态加载失败；问题与证据仍可查看。')).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: '问题详情' }).length).toBeGreaterThan(0)
  })

  it('distinguishes forbidden reads and removes stale statistics after a failed refresh', async () => {
    repairApi.candidates.mockResolvedValueOnce(repairPage({ total: 223 }))
      .mockRejectedValueOnce(Object.assign(new Error('API 403'), { status: 403,
        body: JSON.stringify({ code: 'FORBIDDEN', requestId: '8c8ed247-2578-4439-8857-e6a6a83e1211' }) }))
    render(<MemoryRouter><EvolutionTab workflowId="wf-1" section="diagnosis" /></MemoryRouter>)
    await userEvent.click(screen.getByRole('button', { name: '进入修复处理' }))
    expect(await screen.findByRole('region', { name: '处理任务' })).toBeInTheDocument()
    await userEvent.click(screen.getByRole('checkbox', { name: '包含历史未复现' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('访问被拒绝（403）')
    expect(screen.getByRole('alert')).toHaveTextContent('8c8ed247-2578-4439-8857-e6a6a83e1211')
    expect(screen.queryByText('223')).not.toBeInTheDocument()

    expect(screen.queryByRole('button', { name: /生成 Pack 草稿/ })).not.toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: '问题详情' }).length).toBeGreaterThan(0)
  })

  it.each([false, true])('shows application progress with diagnosis groups removed=%s', async (hideGroups) => {
    lifecycle.hideGroups = hideGroups
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



  it('invalidates selection on source changes without silently selecting the new page', async () => {
    let changed = false
    repairApi.candidates.mockImplementation((_workflowId, query) => {
      if (query.page === 2) changed = true
      return Promise.resolve(changed ? {
        ...repairPage({ items: [repairItem('new-item', '新来源建议')], page: query.page, total: 21 }), inputDigest: 'new-digest',
      } : repairPage({ total: 21 }))
    })
    render(<MemoryRouter><EvolutionTab workflowId="wf-1" section="diagnosis" /></MemoryRouter>)
    await userEvent.click(screen.getByRole('button', { name: '进入修复处理' }))
    await openIssueRepairs(false)
    await userEvent.click(await screen.findByRole('checkbox', { name: '选择 将超时阈值调整为 90 秒' }))
    await userEvent.click(screen.getByRole('button', { name: '下一页建议' }))
    expect(await screen.findByText('建议来源已更新，原选择已清空，请重新确认范围。')).toBeInTheDocument()
    expect(await screen.findByRole('checkbox', { name: '选择 新来源建议' })).not.toBeChecked()
    expect(screen.getByRole('button', { name: '生成 Pack 草稿（0）' })).toBeDisabled()
  })



  it('lets users navigate to evidence beyond the ten newest runs', async () => {
    lifecycle.extraRuns = 12
    render(<MemoryRouter><EvolutionTab workflowId="wf-1" section="diagnosis" /></MemoryRouter>)
    await userEvent.click(screen.getAllByRole('button', { name: '问题详情' })[0])
    await userEvent.click(screen.getByRole('button', { name: '证据与历史' }))
    await userEvent.click(screen.getByRole('button', { name: '下一页分析' }))
    expect(screen.getByRole('button', { name: /选择分析 extra-run-11/ })).toBeVisible()
  })
})
