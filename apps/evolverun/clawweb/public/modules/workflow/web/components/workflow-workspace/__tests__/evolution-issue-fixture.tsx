import { render as renderView, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, vi } from 'vitest'
import { repairSignatureKey } from '../../../../server/contracts/repair-workbench'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

// Keep a local render alias for the existing interaction tests.
function render(ui: Parameters<typeof renderView>[0]) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const result = renderView(ui, { wrapper: ({ children }) => <QueryClientProvider client={client}>{children}</QueryClientProvider> })
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
  diff: vi.fn().mockResolvedValue({ files: [], truncated: false }),
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
  repairApi.diff.mockResolvedValue({ files: [], truncated: false })
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

export { render, repairApi, repairItem, repairPage, lifecycle, mutate, applyBatch, eligibleBots, applyTasks, runAnalysis, openIssueRepairs, closeIssue }
