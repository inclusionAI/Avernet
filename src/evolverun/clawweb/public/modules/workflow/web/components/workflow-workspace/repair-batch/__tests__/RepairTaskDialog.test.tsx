import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { RepairTaskDetail } from '../../../../../server/contracts/repair-workbench'

const repairApi = vi.hoisted(() => ({
  task: vi.fn(),
  candidates: vi.fn(),
  retryDispatch: vi.fn(),
  cancel: vi.fn(),
  revise: vi.fn(),
  diff: vi.fn(),
}))
vi.mock('../../../../api/repair-batches', () => ({ repairBatches: repairApi }))

import RepairTaskDialog from '../RepairTaskDialog'

function taskDetail(phase: 'drafting' | 'review', summary?: string, itemIds: string[] = []): RepairTaskDetail {
  const revision = {
    workflowId: 'wf-1', taskId: 'FIX-1', revision: 1, phase, stateVersion: 1,
    requestId: 'request-1', requestDigest: 'f'.repeat(64),
    input: {
      schemaVersion: 'workflow-repair/v2', taskId: 'FIX-1', revision: 1,
      baseline: { workflowId: 'wf-1', packId: 'pack', releaseRevision: 1, activeDeployNumber: null,
        specDigest: 'a'.repeat(64), repoId: 'repo', specPath: 'workflow.yaml', packCommit: 'b'.repeat(40), packDigest: 'c'.repeat(40) },
      items: itemIds.map((itemId, index) => ({
        itemId, groupKey: 'group-1', proposalKey: String(index + 1).repeat(64), contentRevision: 1,
        previousItemId: null, proposal: { summary: `修复建议 ${index + 1}` }, instruction: '', sources: [],
      })), excludedSources: { count: 0, digest: '0'.repeat(64) }, instructions: '',
      parentCandidateCommit: null, feedback: '', previousReportRef: null, taskBranch: 'repair/FIX-1',
    },
    draft: summary ? { candidateCommit: 'd'.repeat(40), summary } : null,
    checks: summary ? {} : null, candidateDigest: summary ? '1'.repeat(64) : null,
    checksDigest: summary ? '2'.repeat(64) : null, error: null, createdAtMs: 1, updatedAtMs: 2,
  } as RepairTaskDetail['latestAttempt']
  return {
    workflowId: 'wf-1', taskId: 'FIX-1',
    capabilities: { generation: true, diff: true, publication: false, reason: null },
    execution: phase === 'drafting'
      ? { status: 'running', errorCode: null, jobId: 'job-1', attempt: 1, executionId: 'exec-1', rawStatus: 'running' }
      : { status: 'succeeded', errorCode: null, jobId: 'job-1', attempt: 1, executionId: 'exec-1', rawStatus: 'success' },
    latestAttempt: revision, latestSuccessful: phase === 'review' ? revision : null, revisions: [revision],
  }
}

const candidates = () => ({
  schemaVersion: 'workflow-repair/v2' as const, workflowId: 'wf-1', inputDigest: 'c'.repeat(64), canEdit: true,
  items: [{
    itemId: 'item-3', groupKey: 'group-1', proposalKey: '3'.repeat(64), contentRevision: 1,
    previousItemId: null, proposal: { summary: '新出现的修复建议' }, instruction: '', sources: [],
    workflowId: 'wf-1', episodeKey: 'current', state: 'pending' as const, stateVersion: 0, activeTaskId: null,
    activeRevision: null, disposition: null, updatedAtMs: 3, sourceAvailable: true,
  }], tasks: [], capabilities: { generation: true, diff: true, publication: false as const, reason: null },
  includeHistorical: false, activeLookbackDays: 30,
  counts: { pending: 1, processing: 0, awaiting_verification: 0, closed: 0, no_action: 0, all: 1 },
  page: { page: 1, pageSize: 20, total: 1, totalPages: 1 }, limits: { maxItems: 100, maxRequestBytes: 65536 },
})

beforeEach(() => Object.values(repairApi).forEach(mock => mock.mockReset()))
afterEach(() => vi.useRealTimers())

describe('RepairTaskDialog', () => {
  it('polls an active generation until the task becomes reviewable', async () => {
    vi.useFakeTimers()
    repairApi.task.mockResolvedValueOnce(taskDetail('drafting')).mockResolvedValueOnce(taskDetail('review', '候选已生成'))

    render(<RepairTaskDialog workflowId="wf-1" taskId="FIX-1" includeHistorical={false}
      canEdit onClose={() => {}} onChanged={() => {}} />)
    await act(async () => {})
    expect(screen.getByText(/最近尝试：v1 · 生成中/)).toBeInTheDocument()

    await act(async () => { await vi.runOnlyPendingTimersAsync() })
    expect(screen.getByText('候选已生成')).toBeInTheDocument()
    expect(repairApi.task).toHaveBeenCalledTimes(2)
  })

  it('keeps polling an active task after a transient read failure', async () => {
    vi.useFakeTimers()
    repairApi.task.mockResolvedValueOnce(taskDetail('drafting'))
      .mockRejectedValueOnce(new Error('temporary unavailable'))
      .mockResolvedValueOnce(taskDetail('review', '恢复后候选已生成'))

    render(<RepairTaskDialog workflowId="wf-1" taskId="FIX-1" includeHistorical={false}
      canEdit onClose={() => {}} onChanged={() => {}} />)
    await act(async () => {})
    await act(async () => { await vi.runOnlyPendingTimersAsync() })
    await act(async () => { await vi.runOnlyPendingTimersAsync() })

    expect(screen.getByText('恢复后候选已生成')).toBeInTheDocument()
    expect(repairApi.task).toHaveBeenCalledTimes(3)
  })

  it('lets feedback revisions reconfirm which frozen items remain selected', async () => {
    repairApi.task.mockResolvedValue(taskDetail('review', '候选已生成', ['item-1', 'item-2']))
    repairApi.candidates.mockResolvedValue(candidates())
    repairApi.revise.mockResolvedValueOnce({})

    render(<RepairTaskDialog workflowId="wf-1" taskId="FIX-1" includeHistorical={false}
      canEdit onClose={() => {}} onChanged={() => {}} />)
    await userEvent.click(await screen.findByRole('button', { name: '反馈并生成下一版' }))
    expect(screen.getByRole('checkbox', { name: '选择修订项 修复建议 1' })).toBeChecked()
    expect(screen.getByRole('checkbox', { name: '选择修订项 修复建议 2' })).toBeChecked()
    const newItem = await screen.findByRole('checkbox', { name: '选择修订项 新出现的修复建议' })
    expect(newItem).not.toBeChecked()

    await userEvent.click(screen.getByRole('checkbox', { name: '选择修订项 修复建议 2' }))
    await userEvent.click(newItem)
    await userEvent.type(screen.getByRole('textbox', { name: '修改反馈' }), '只保留第一项')
    await userEvent.click(screen.getByRole('button', { name: '确认生成下一版' }))

    await waitFor(() => expect(repairApi.revise).toHaveBeenCalledWith('FIX-1', expect.objectContaining({ itemIds: ['item-1', 'item-3'] })))
  })

  it('reuses a request ID only while the revision payload is unchanged', async () => {
    repairApi.task.mockResolvedValue(taskDetail('review', '候选已生成', ['item-1']))
    repairApi.candidates.mockResolvedValue(candidates())
    repairApi.revise.mockRejectedValueOnce(new Error('response lost'))
      .mockRejectedValueOnce(new Error('response lost again'))
      .mockResolvedValueOnce({})

    render(<RepairTaskDialog workflowId="wf-1" taskId="FIX-1" includeHistorical={false}
      canEdit onClose={() => {}} onChanged={() => {}} />)
    await userEvent.click(await screen.findByRole('button', { name: '反馈并生成下一版' }))
    await screen.findByRole('checkbox', { name: '选择修订项 新出现的修复建议' })
    const feedback = screen.getByRole('textbox', { name: '修改反馈' })
    await userEvent.type(feedback, '第一次反馈')

    await userEvent.click(screen.getByRole('button', { name: '确认生成下一版' }))
    await waitFor(() => expect(repairApi.revise).toHaveBeenCalledTimes(1))
    const firstId = repairApi.revise.mock.calls[0][1].requestId
    await userEvent.click(screen.getByRole('button', { name: '确认生成下一版' }))
    await waitFor(() => expect(repairApi.revise).toHaveBeenCalledTimes(2))
    expect(repairApi.revise.mock.calls[1][1].requestId).toBe(firstId)

    await userEvent.type(feedback, '，调整范围')
    await userEvent.click(screen.getByRole('button', { name: '确认生成下一版' }))
    await waitFor(() => expect(repairApi.revise).toHaveBeenCalledTimes(3))
    expect(repairApi.revise.mock.calls[2][1].requestId).not.toBe(firstId)
  })
})
