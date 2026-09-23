import { cleanup, render, screen, within, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { RepairCandidatesResponse, RepairInboxItem, RepairTaskDetail } from '../../../../../server/contracts/repair-workbench'
import type { RepairRevision } from '../../../../../server/contracts/repair-batch'
import RepairWorkbench from '../RepairWorkbench'

const api = vi.hoisted(() => ({ candidates: vi.fn(), task: vi.fn(), create: vi.fn(), revise: vi.fn(), disposition: vi.fn(), cancel: vi.fn(), diff: vi.fn(), retryDispatch: vi.fn() }))
vi.mock('../../../../api/repair-batches', () => ({ repairBatches: api }))

function item(id: string, state: RepairInboxItem['state'] = 'pending', sourceAvailable = true): RepairInboxItem {
  return { itemId: id, groupKey: 'timeout', proposalKey: id, contentRevision: 1, previousItemId: null,
    proposal: { summary: `修复 ${id}` }, instruction: `处理 ${id}`, sources: [{ kind: 'suggestion', suggestionId: id, proposalDigest: null, instructionDigest: 'digest' }],
    workflowId: 'wf-1', episodeKey: id, state, stateVersion: 2, activeTaskId: state === 'processing' ? 'task-1' : null,
    activeRevision: state === 'processing' ? 1 : null, disposition: null, updatedAtMs: 1, sourceAvailable }
}
function candidates(items = [item('a'), item('b', 'processing'), item('c', 'no_action'), item('d', 'pending', false)]): RepairCandidatesResponse {
  return { schemaVersion: 'workflow-repair/v2', workflowId: 'wf-1', inputDigest: 'digest', items, tasks: [],
    capabilities: { generation: true, diff: true, publication: false, reason: null }, limits: { maxItems: 100, maxRequestBytes: 65536 }, canEdit: true }
}
function revision(n: number, phase: RepairRevision['phase']): RepairRevision {
  return { workflowId: 'wf-1', taskId: 'task-1', revision: n, phase, stateVersion: 1, requestId: 'request', requestDigest: 'digest',
    input: { schemaVersion: 'workflow-repair/v2', taskId: 'task-1', revision: n,
      baseline: { workflowId: 'wf-1', packId: 'pack', releaseRevision: 1, activeDeployNumber: 1, specDigest: 'digest', repoId: 'repo', specPath: 'workflows/wf-1.yaml', packCommit: 'base', packDigest: 'digest' },
      items: [item('a')], excludedSourceRefs: [], instructions: '', parentCandidateCommit: null, feedback: '', previousReportRef: null, taskBranch: 'repair/wf-1/task-1' },
    draft: phase === 'review' ? { candidateCommit: 'candidate-v1', summary: '保留的候选稿', itemResults: [{ itemId: 'a', status: 'changed', reason: '增加重试' }] } : null,
    checks: phase === 'review' ? { static: { status: 'passed' }, mock: { status: 'not_covered' }, coverage: [{ itemId: 'a', mode: 'none', assertionCount: 0 }], uncoveredChecks: ['external_resources'] } : null,
    candidateDigest: null, checksDigest: null, error: phase === 'failed' ? { message: 'AIS 服务失败' } : null, createdAtMs: 1, updatedAtMs: 2 }
}
function detail(): RepairTaskDetail {
  return { workflowId: 'wf-1', taskId: 'task-1', latestAttempt: revision(2, 'failed'), latestSuccessful: revision(1, 'review'), revisions: [revision(1, 'review'), revision(2, 'failed')], capabilities: candidates().capabilities }
}
const execution = (status: NonNullable<RepairTaskDetail['execution']>['status'], errorCode: string | null = null): NonNullable<RepairTaskDetail['execution']> =>
  ({ status, errorCode, jobId: status === 'created' || status === 'dispatch_failed' ? null : 'job-1', attempt: 1, executionId: 'exec-1', rawStatus: null })

beforeEach(() => { vi.resetAllMocks(); sessionStorage.clear(); api.candidates.mockResolvedValue(candidates()); api.task.mockResolvedValue(detail()) })
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe('RepairWorkbench', () => {
  it('uses readable context for hash group headings without merging groups that share a label', async () => {
    const first = { ...item('a'), groupKey: 'a'.repeat(64), context: { signature: 'timeout:fetch-data' } }
    const sibling = { ...item('b'), groupKey: 'a'.repeat(64), context: { signature: 'timeout:fetch-data' } }
    const distinct = { ...item('c'), groupKey: 'b'.repeat(64), context: { signature: 'timeout:fetch-data' } }
    api.candidates.mockResolvedValue(candidates([first, sibling, distinct]))
    render(<RepairWorkbench workflowId="wf-1" />)
    const groups = await screen.findAllByRole('region', { name: '问题组 timeout:fetch-data' })
    expect(groups).toHaveLength(2)
    expect(within(groups[0]).getAllByRole('checkbox')).toHaveLength(2)
    expect(within(groups[1]).getAllByRole('checkbox')).toHaveLength(1)
    expect(screen.queryByText('a'.repeat(64))).not.toBeInTheDocument()
  })
  it('keeps unpersisted legacy preview read-only but permits dispositions on persisted history', async () => {
    const preview = { ...item('preview', 'pending', false), updatedAtMs: 0 }
    const ignoredPreview = { ...item('ignored-preview', 'no_action', false), updatedAtMs: 0 }
    const history = { ...item('history', 'no_action', false), updatedAtMs: 123 }
    api.candidates.mockResolvedValue(candidates([preview, ignoredPreview, history]))
    const user = userEvent.setup(); render(<RepairWorkbench workflowId="wf-1" />)
    await user.click(await screen.findByRole('button', { name: '全部 3' }))
    const previewRow = screen.getByRole('checkbox', { name: '选择 修复 preview' }).closest('article')!
    expect(within(previewRow).getByText('历史预览，请在原处理流程查看')).toBeInTheDocument()
    expect(within(previewRow).queryByText('原始来源已不可用')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '暂不处理 修复 preview' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '恢复 修复 ignored-preview' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '恢复 修复 history' })).toBeEnabled()
  })
  it.each(['dispatch_failed', 'created'] as const)('shows %s dispatch separately and manually retries the same frozen revision', async status => {
    const data = candidates(); data.tasks = [{ taskId: 'task-1', revision: 2, phase: 'drafting', updatedAtMs: 2, itemCount: 1 }]
    const task = detail(); task.latestAttempt = revision(2, 'drafting'); task.execution = execution(status, status === 'dispatch_failed' ? 'DISPATCH_TIMEOUT' : null)
    api.candidates.mockResolvedValue(data); api.task.mockResolvedValue(task)
    const user = userEvent.setup(); render(<RepairWorkbench workflowId="wf-1" />)
    await user.click(await screen.findByRole('button', { name: /打开任务 task-1/ }))
    expect(await screen.findByText(status === 'dispatch_failed' ? '派发未确认' : '等待派发')).toBeInTheDocument()
    if (status === 'dispatch_failed') expect(screen.getByText('DISPATCH_TIMEOUT')).toBeInTheDocument()
    expect(api.retryDispatch).not.toHaveBeenCalled()
    api.retryDispatch.mockResolvedValue({ ok: true })
    api.task.mockResolvedValue({ ...task, execution: execution('dispatched') })
    await user.click(screen.getByRole('button', { name: '重试派发' }))
    expect(await screen.findByText('已派发，等待生成结果')).toBeInTheDocument()
    expect(api.retryDispatch).toHaveBeenCalledWith('task-1', 2)
    expect(api.create).not.toHaveBeenCalled(); expect(api.revise).not.toHaveBeenCalled()
    expect(screen.getByText('最近成功候选：v1')).toBeInTheDocument()
  })
  it('does not offer repeat dispatch while the delivery is in flight', async () => {
    const data = candidates(); data.tasks = [{ taskId: 'task-1', revision: 2, phase: 'drafting', updatedAtMs: 2, itemCount: 1 }]
    const task = detail(); task.latestAttempt = revision(2, 'drafting'); task.execution = execution('dispatching')
    api.candidates.mockResolvedValue(data); api.task.mockResolvedValue(task)
    const user = userEvent.setup(); render(<RepairWorkbench workflowId="wf-1" />)
    await user.click(await screen.findByRole('button', { name: /打开任务 task-1/ }))
    expect(await screen.findByText('正在派发；状态未确认时不要重复提交')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '重试派发' })).not.toBeInTheDocument()
    expect(api.retryDispatch).not.toHaveBeenCalled()
  })
  it.each(['readonly', 'unavailable'] as const)('prevents dispatch retries when %s', async boundary => {
    const data = candidates(); data.canEdit = boundary !== 'readonly'; data.capabilities.generation = boundary !== 'unavailable'
    data.tasks = [{ taskId: 'task-1', revision: 2, phase: 'drafting', updatedAtMs: 2, itemCount: 1 }]
    const task = detail(); task.capabilities = data.capabilities; task.latestAttempt = revision(2, 'drafting'); task.execution = execution('dispatch_failed', 'DISPATCH_TIMEOUT')
    api.candidates.mockResolvedValue(data); api.task.mockResolvedValue(task)
    const user = userEvent.setup(); render(<RepairWorkbench workflowId="wf-1" />)
    await user.click(await screen.findByRole('button', { name: /打开任务 task-1/ }))
    expect(await screen.findByRole('button', { name: '重试派发' })).toBeDisabled()
  })
  it('sends the expected revision to the retry-dispatch endpoint', async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ ok: true }), { status: 200 }))
    vi.stubGlobal('fetch', fetch)
    const { repairBatches } = await vi.importActual<typeof import('../../../../api/repair-batches')>('../../../../api/repair-batches')
    await repairBatches.retryDispatch('task-1', 7)
    expect(fetch).toHaveBeenCalledWith('/api/workflow-repairs/task-1/retry-dispatch', expect.objectContaining({ method: 'POST', body: JSON.stringify({ expectedRevision: 7 }) }))
  })
  it('selects only eligible pending items and explains exclusions without flattening mixed group states', async () => {
    const user = userEvent.setup(); render(<RepairWorkbench workflowId="wf-1" />)
    expect(await screen.findByRole('checkbox', { name: '选择 修复 a' })).toBeChecked()
    await user.click(screen.getByRole('button', { name: /全部/ }))
    expect(screen.getByRole('checkbox', { name: '选择 修复 b' })).toBeDisabled()
    expect(screen.getByRole('checkbox', { name: '选择 修复 c' })).toBeDisabled()
    expect(screen.getByRole('checkbox', { name: '选择 修复 d' })).toBeDisabled()
    expect(screen.getByText('混合状态')).toBeInTheDocument()
    expect(screen.getByText('原始来源已不可用')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '生成修复候选稿' }))
    const dialog = screen.getByRole('dialog'); expect(within(dialog).getByText(/已选择 1 项/)).toBeInTheDocument()
    api.create.mockResolvedValue(revision(1, 'drafting'))
    await user.type(within(dialog).getByLabelText('修复说明'), '只处理超时')
    await user.click(within(dialog).getByRole('button', { name: '确认生成' }))
    await waitFor(() => expect(api.create).toHaveBeenCalledWith(expect.objectContaining({ workflowId: 'wf-1', itemIds: ['a'], instructions: '只处理超时', inputDigest: 'digest' })))
    expect(await screen.findByText('修复任务 task-1')).toBeInTheDocument()
  })
  it('caps default selection at 100 and prevents adding item 101', async () => {
    api.candidates.mockResolvedValue(candidates(Array.from({ length: 101 }, (_, i) => item(String(i)))))
    render(<RepairWorkbench workflowId="wf-1" />)
    expect(await screen.findByRole('checkbox', { name: '选择 修复 100' })).not.toBeChecked()
    expect(screen.getByRole('checkbox', { name: '选择 修复 100' })).toBeDisabled()
    expect(screen.getByText(/已选择 100 项/)).toBeInTheDocument()
  })
  it('keeps the inbox and audited no_action available when generation is unavailable', async () => {
    const data = candidates(); data.capabilities.generation = false; data.capabilities.reason = '修复服务未配置'; api.candidates.mockResolvedValue(data)
    const user = userEvent.setup(); render(<RepairWorkbench workflowId="wf-1" />)
    expect(await screen.findByRole('button', { name: '生成修复候选稿' })).toBeDisabled()
    expect(screen.getByText('修复服务未配置')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '暂不处理 修复 a' }))
    await user.type(screen.getByLabelText('处置原因'), '确认无需修改')
    api.disposition.mockResolvedValue(item('a', 'no_action'))
    await user.click(screen.getByRole('button', { name: '确认处置' }))
    await waitFor(() => expect(api.disposition).toHaveBeenCalledWith('a', expect.objectContaining({ expectedStateVersion: 2, contentRevision: 1, reason: '确认无需修改', action: 'no_action' })))
  })
  it('never offers mutations to a readonly viewer', async () => {
    const data = candidates(); data.canEdit = false; api.candidates.mockResolvedValue(data)
    render(<RepairWorkbench workflowId="wf-1" />)
    expect(await screen.findByRole('button', { name: '生成修复候选稿' })).toBeDisabled()
    expect(screen.queryByRole('button', { name: '暂不处理 修复 a' })).not.toBeInTheDocument()
    expect(screen.getByRole('checkbox', { name: '选择 修复 a' })).toBeDisabled()
  })
  it('restores a no_action item even with generation disabled and persists the filter on reopening', async () => {
    const data = candidates(); data.capabilities.generation = false; api.candidates.mockResolvedValue(data)
    const user = userEvent.setup(); const view = render(<RepairWorkbench workflowId="wf-1" />)
    await user.click(await screen.findByRole('button', { name: '暂不处理 1' }))
    view.unmount(); render(<RepairWorkbench workflowId="wf-1" />)
    expect(await screen.findByRole('button', { name: '暂不处理 1' })).toHaveAttribute('aria-pressed', 'true')
    await user.click(screen.getByRole('button', { name: '恢复 修复 c' }))
    await user.type(screen.getByLabelText('处置原因'), '重新确认需要修复')
    api.disposition.mockResolvedValue(item('c'))
    await user.click(screen.getByRole('button', { name: '确认处置' }))
    await waitFor(() => expect(api.disposition).toHaveBeenCalledWith('c', expect.objectContaining({ action: 'restore', expectedStateVersion: 2, contentRevision: 1 })))
  })
  it('retains the successful candidate and feedback when a new revision fails', async () => {
    const data = candidates([item('a', 'processing'), item('extra')]); data.tasks = [{ taskId: 'task-1', revision: 2, phase: 'failed', updatedAtMs: 2, itemCount: 1 }]; api.candidates.mockResolvedValue(data)
    const user = userEvent.setup(); render(<RepairWorkbench workflowId="wf-1" />)
    await user.click(await screen.findByRole('button', { name: /打开任务 task-1/ }))
    expect(await screen.findByText('最近尝试：v2 · 失败')).toBeInTheDocument()
    expect(screen.getByText('最近成功候选：v1')).toBeInTheDocument()
    expect(screen.getByText('candidate-v1')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '反馈并生成下一版' }))
    expect(within(screen.getByRole('dialog')).getByRole('checkbox', { name: '选择 修复 a' })).toBeChecked()
    await user.click(within(screen.getByRole('dialog')).getByRole('checkbox', { name: '选择 修复 extra' }))
    await user.type(screen.getByLabelText('修改反馈'), '保留重试并增加上限')
    api.revise.mockRejectedValue(new Error('服务暂时不可用'))
    await user.click(screen.getByRole('button', { name: '确认生成下一版' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('服务暂时不可用')
    expect(screen.getByLabelText('修改反馈')).toHaveValue('保留重试并增加上限')
    expect(screen.getByText('candidate-v1')).toBeInTheDocument()
    expect(api.revise).toHaveBeenCalledWith('task-1', expect.objectContaining({ expectedAttemptRevision: 2, parentCandidateCommit: 'candidate-v1', itemIds: ['a', 'extra'] }))
  })
  it('shows loading and errors explicitly and resets task/filter on workflow change', async () => {
    api.candidates.mockRejectedValueOnce(new Error('读取失败'))
    const user = userEvent.setup(); const view = render(<RepairWorkbench workflowId="wf-1" />)
    expect(screen.getByRole('status')).toHaveTextContent('加载修复收件箱')
    expect(await screen.findByRole('alert')).toHaveTextContent('读取失败')
    await user.click(screen.getByRole('button', { name: '重试' }))
    await user.click(await screen.findByRole('button', { name: '暂不处理 1' }))
    view.rerender(<RepairWorkbench workflowId="wf-2" />)
    expect(await screen.findByRole('button', { name: /待处理/ })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.queryByText('修复任务 task-1')).not.toBeInTheDocument()
  })
  it('blocks a second batch while review is active and lets an editor cancel the task', async () => {
    const data = candidates(); data.tasks = [{ taskId: 'task-1', revision: 1, phase: 'review', updatedAtMs: 2, itemCount: 1 }]
    const task = detail(); task.latestAttempt = revision(1, 'review'); task.revisions = [task.latestAttempt]
    api.candidates.mockResolvedValue(data); api.task.mockResolvedValue(task)
    const user = userEvent.setup(); render(<RepairWorkbench workflowId="wf-1" />)
    expect(await screen.findByRole('button', { name: '生成修复候选稿' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: /打开任务 task-1/ }))
    await user.click(await screen.findByRole('button', { name: '取消修复任务' }))
    api.cancel.mockResolvedValue({ cancelled: true })
    await user.click(screen.getByRole('button', { name: '确认取消任务' }))
    await waitFor(() => expect(api.cancel).toHaveBeenCalledWith('task-1', 1))
  })
  it.each(['cancelled', 'no_change', 'published'] as const)('prevents feedback after terminal %s', async phase => {
    const data = candidates(); data.tasks = [{ taskId: 'task-1', revision: 2, phase, updatedAtMs: 2, itemCount: 1 }]
    const task = detail(); task.latestAttempt = revision(2, phase)
    api.candidates.mockResolvedValue(data); api.task.mockResolvedValue(task)
    const user = userEvent.setup(); render(<RepairWorkbench workflowId="wf-1" />)
    await user.click(await screen.findByRole('button', { name: /打开任务 task-1/ }))
    expect(await screen.findByRole('button', { name: '反馈并生成下一版' })).toBeDisabled()
    expect(screen.queryByRole('button', { name: '取消修复任务' })).not.toBeInTheDocument()
  })
  it('shows bound item outcomes and switches diff bases without retaining stale diff after error', async () => {
    const data = candidates(); data.tasks = [{ taskId: 'task-1', revision: 2, phase: 'review', updatedAtMs: 2, itemCount: 1 }]
    const task = detail(); const latest = revision(2, 'review'); latest.input.parentCandidateCommit = 'candidate-v0'
    task.latestAttempt = latest; task.latestSuccessful = latest; task.revisions = [revision(1, 'review'), latest]
    api.candidates.mockResolvedValue(data); api.task.mockResolvedValue(task)
    api.diff.mockResolvedValueOnce({ baseCommit: 'base', candidateCommit: 'candidate-v1', files: [{ path: 'workflow.yaml', status: 'modified', before: 'before-line', after: 'after-line', binary: false, truncated: false }], truncated: false })
    const user = userEvent.setup(); render(<RepairWorkbench workflowId="wf-1" />)
    await user.click(await screen.findByRole('button', { name: /打开任务 task-1/ }))
    expect(await screen.findByText('增加重试')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '查看差异' }))
    expect(await screen.findByText('after-line')).toBeInTheDocument()
    expect(api.diff).toHaveBeenCalledWith('task-1', 2, 'baseline')
    api.diff.mockRejectedValueOnce(new Error('diff暂不可用'))
    await user.selectOptions(screen.getByLabelText('比较基准'), 'parent')
    expect(await screen.findByRole('alert')).toHaveTextContent('diff暂不可用')
    expect(screen.queryByText('after-line')).not.toBeInTheDocument()
    expect(api.diff).toHaveBeenLastCalledWith('task-1', 2, 'parent')
  })
})
