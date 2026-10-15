import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, it, vi } from 'vitest'
import type { RepairTaskDetail } from '../../../../../server/contracts/repair-workbench'
const api = vi.hoisted(() => ({ deploymentTargets: vi.fn(), publish: vi.fn(), reconcileDeployment: vi.fn() }))
vi.mock('../../../../api/repair-batches', () => ({ repairBatches: api }))
import RepairReleaseFlow from '../RepairReleaseFlow'
const revision = { revision: 1, phase: 'review', stateVersion: 1, draft: { candidateCommit: 'a'.repeat(40) } } as RepairTaskDetail['latestAttempt']
const detail = { taskId: 'task', workflowId: 'wf', latestAttempt: revision, latestSuccessful: revision, revisions: [revision],
  capabilities: { publication: true }, release: { approval: null, deployments: [] } } as unknown as RepairTaskDetail
it('uses a single explicit decision to publish the reviewed workflow scope without Bot selection', async () => {
  api.deploymentTargets.mockResolvedValue({ targets: [{ id: 'workflow:pre:wf', scope: 'workflow', label: '工作流 wf', environment: 'pre' }] })
  api.publish.mockResolvedValue({ status: 'running' })
  render(<RepairReleaseFlow detail={detail} selectedRevision={1} canEdit onRefresh={() => {}} />)
  await userEvent.click(screen.getByRole('button', { name: '发布此版本', exact: true }))
  expect(await screen.findByText(/工作流 wf/)).toBeInTheDocument()
  expect(screen.queryByRole('combobox')).not.toBeInTheDocument()
  expect(api.publish).not.toHaveBeenCalled()
  await userEvent.click(screen.getByRole('checkbox'))
  await userEvent.dblClick(screen.getByRole('button', { name: '确认发布', exact: true }))
  expect(api.publish).toHaveBeenCalledOnce()
  expect(api.publish).toHaveBeenCalledWith('task', expect.objectContaining({ revision: 1, targetId: 'workflow:pre:wf', acknowledgeUncoveredChecks: true }))
})
it('keeps read-only and historical candidates non-executable', () => {
  render(<RepairReleaseFlow detail={detail} selectedRevision={0} canEdit={false} onRefresh={() => {}} />)
  expect(screen.getByRole('button', { name: '发布此版本' })).toBeDisabled()
})
it('reconciles an uncertain request instead of offering another publish', () => {
  render(<RepairReleaseFlow detail={{ ...detail, release: { approval: null, deployments: [{ id: 'd', revision: 1, status: 'uncertain', target: {} } as never] } }} selectedRevision={1} canEdit onRefresh={() => {}} />)
  expect(screen.getByRole('button', { name: '核对发布状态' })).toBeEnabled()
  expect(screen.queryByRole('button', { name: '发布此版本' })).not.toBeInTheDocument()
})
it('freezes the entire publish request across an approval-only commit and lost response', async () => {
  api.deploymentTargets.mockResolvedValue({ targets: [{ id: 'workflow:pre:wf', scope: 'workflow', label: '工作流 wf', environment: 'pre' }] })
  api.publish.mockReset(); api.publish.mockRejectedValueOnce(new Error('connection lost')).mockResolvedValueOnce({ status: 'running' })
  const view = render(<RepairReleaseFlow detail={detail} selectedRevision={1} canEdit onRefresh={() => {}} />)
  await userEvent.click(screen.getByRole('button', { name: '发布此版本', exact: true }))
  await screen.findByText('工作流 wf')
  await userEvent.click(screen.getByRole('checkbox')); await userEvent.click(screen.getByRole('button', { name: '确认发布', exact: true }))
  await screen.findByRole('alert')
  const changed = { ...revision, stateVersion: 2 }
  view.rerender(<RepairReleaseFlow detail={{ ...detail, latestAttempt: changed, latestSuccessful: changed }} selectedRevision={1} canEdit onRefresh={() => {}} />)
  await userEvent.click(screen.getByRole('button', { name: '确认发布', exact: true }))
  expect(api.publish.mock.calls[1]).toEqual(api.publish.mock.calls[0])
})
