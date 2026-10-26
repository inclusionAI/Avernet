import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import type { RepairTaskDetail } from '../../../../server/contracts/repair-workbench'
import { render, repairApi, repairItem, repairPage } from './evolution-issue-fixture'
import EvolutionTab from '../EvolutionTab'

beforeEach(() => { vi.spyOn(window, 'scrollTo').mockImplementation(() => {}) })
afterEach(() => vi.restoreAllMocks())

function taskDetail(workflowId: string, taskId: string): RepairTaskDetail {
  const revision: RepairTaskDetail['latestAttempt'] = {
    workflowId, taskId, revision: 1, phase: 'drafting', stateVersion: 1,
    requestId: 'request-1', requestDigest: 'f'.repeat(64),
    input: { schemaVersion: 'workflow-repair/v2', taskId, revision: 1,
      baseline: { workflowId, packId: 'pack', releaseRevision: 1, activeDeployNumber: null, specDigest: 'a'.repeat(64),
        repoId: 'repo', specPath: 'workflow.yaml', packCommit: 'b'.repeat(40), packDigest: 'c'.repeat(40) },
      items: [repairItem()], excludedSources: { count: 0, digest: '0'.repeat(64) }, instructions: '',
      parentCandidateCommit: null, feedback: '', previousReportRef: null, taskBranch: `repair/${taskId}` },
    draft: null, checks: null, candidateDigest: null, checksDigest: null, error: null, createdAtMs: 1, updatedAtMs: 2,
  }
  return { workflowId, taskId, capabilities: { generation: true, diff: true, publication: false, reason: null },
    execution: { status: 'running', errorCode: null, jobId: 'job-1', attempt: 1, executionId: 'exec-1', rawStatus: 'running' },
    latestAttempt: revision, latestSuccessful: null, revisions: [revision] }
}

function LocationProbe() { return <output aria-label="task-location">{useLocation().search}</output> }
function open(path: string) {
  return render(<MemoryRouter initialEntries={[path]}><EvolutionTab workflowId="wf-1" section="diagnosis" /><LocationProbe /></MemoryRouter>)
}

it('prefers a valid router task link over cached selection and removes the link when returning to issues', async () => {
  sessionStorage.setItem('workflow-repair:wf-1', JSON.stringify({ selectedTaskId: 'OLD' }))
  repairApi.candidates.mockResolvedValue(repairPage())
  repairApi.task.mockResolvedValue(taskDetail('wf-1', 'FIX-1'))
  open('/workflows/workspace?workflowId=wf-1&tab=evolution&repairTaskId=FIX-1')
  await waitFor(() => expect(repairApi.task).toHaveBeenCalledWith('FIX-1'))
  expect(repairApi.task).not.toHaveBeenCalledWith('OLD')
  await userEvent.click(await screen.findByRole('button', { name: /返回问题与优化/ }))
  expect(screen.getByLabelText('task-location')).not.toHaveTextContent('repairTaskId=')
  await waitFor(() => expect(JSON.parse(sessionStorage.getItem('workflow-repair:wf-1')!).selectedTaskId).toBe(''))
})

it.each([false, true])('discards only a confirmed foreign task and leaves unrelated workflow state intact (linked=%s)', async linked => {
  sessionStorage.setItem('workflow-repair:wf-1', JSON.stringify({ selectedTaskId: 'OLD', includeHistorical: true }))
  const other = JSON.stringify({ selectedTaskId: 'VALID-OTHER' })
  sessionStorage.setItem('workflow-repair:wf-2', other)
  repairApi.candidates.mockResolvedValue(repairPage())
  repairApi.task.mockResolvedValue(taskDetail('wf-2', 'OLD'))
  const path = '/workflows/workspace?workflowId=wf-1&tab=evolution' + (linked ? '&repairTaskId=OLD' : '')
  const first = open(path)
  await screen.findByText('原任务不属于当前工作流，已返回当前工作流的问题列表。')
  await waitFor(() => expect(screen.getByLabelText('task-location')).not.toHaveTextContent('repairTaskId='))
  await waitFor(() => expect(JSON.parse(sessionStorage.getItem('workflow-repair:wf-1')!)).toMatchObject({ selectedTaskId: '', includeHistorical: true }))
  expect(sessionStorage.getItem('workflow-repair:wf-2')).toBe(other)
  first.unmount()
  repairApi.task.mockClear()
  open('/workflows/workspace?workflowId=wf-1&tab=evolution')
  await waitFor(() => expect(screen.getAllByRole('button', { name: '问题详情' }).length).toBeGreaterThan(0))
  expect(repairApi.task).not.toHaveBeenCalled()
})

it('does not discard a task or erase permission errors merely because its read was denied', async () => {
  sessionStorage.setItem('workflow-repair:wf-1', JSON.stringify({ selectedTaskId: 'FIX-1' }))
  repairApi.candidates.mockResolvedValue(repairPage())
  repairApi.task.mockRejectedValue(Object.assign(new Error('Forbidden'), { status: 403 }))
  open('/workflows/workspace?workflowId=wf-1&repairTaskId=FIX-1')
  await screen.findByRole('alert')
  expect(screen.queryByText('原任务不属于当前工作流，已返回当前工作流的问题列表。')).not.toBeInTheDocument()
  expect(JSON.parse(sessionStorage.getItem('workflow-repair:wf-1')!).selectedTaskId).toBe('FIX-1')
  expect(screen.getByLabelText('task-location')).toHaveTextContent('repairTaskId=FIX-1')
})
