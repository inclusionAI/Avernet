import { act, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { RepairTaskDetail } from '../../../../../server/contracts/repair-workbench'

const repairApi = vi.hoisted(() => ({
  task: vi.fn(),
  retryDispatch: vi.fn(),
  cancel: vi.fn(),
  revise: vi.fn(),
  diff: vi.fn(),
}))
vi.mock('../../../../api/repair-batches', () => ({ repairBatches: repairApi }))

import RepairTaskDialog from '../RepairTaskDialog'

function taskDetail(phase: 'drafting' | 'review', summary?: string): RepairTaskDetail {
  const revision = {
    workflowId: 'wf-1', taskId: 'FIX-1', revision: 1, phase, stateVersion: 1,
    requestId: 'request-1', requestDigest: 'f'.repeat(64),
    input: {
      schemaVersion: 'workflow-repair/v2', taskId: 'FIX-1', revision: 1,
      baseline: { workflowId: 'wf-1', packId: 'pack', releaseRevision: 1, activeDeployNumber: null,
        specDigest: 'a'.repeat(64), repoId: 'repo', specPath: 'workflow.yaml', packCommit: 'b'.repeat(40), packDigest: 'c'.repeat(40) },
      items: [], excludedSources: { count: 0, digest: '0'.repeat(64) }, instructions: '',
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

afterEach(() => vi.useRealTimers())

describe('RepairTaskDialog', () => {
  it('polls an active generation until the task becomes reviewable', async () => {
    vi.useFakeTimers()
    repairApi.task.mockResolvedValueOnce(taskDetail('drafting')).mockResolvedValueOnce(taskDetail('review', '候选已生成'))

    render(<RepairTaskDialog workflowId="wf-1" taskId="FIX-1" inputDigest={'c'.repeat(64)} includeHistorical={false}
      canEdit onClose={() => {}} onChanged={() => {}} />)
    await act(async () => {})
    expect(screen.getByText(/最近尝试：v1 · 生成中/)).toBeInTheDocument()

    await act(async () => { await vi.runOnlyPendingTimersAsync() })
    expect(screen.getByText('候选已生成')).toBeInTheDocument()
    expect(repairApi.task).toHaveBeenCalledTimes(2)
  })
})
