import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, it, vi } from 'vitest'
import type { RepairTaskDetail } from '../../../../../server/contracts/repair-workbench'
const api = vi.hoisted(() => ({ deploymentTargets: vi.fn(), confirm: vi.fn(), deploy: vi.fn(), reconcileDeployment: vi.fn() }))
vi.mock('../../../../api/repair-batches', () => ({ repairBatches: api }))
import RepairReleaseFlow from '../RepairReleaseFlow'
const revision = { revision: 1, phase: 'review', stateVersion: 1, draft: { candidateCommit: 'a'.repeat(40) } } as RepairTaskDetail['latestAttempt']
const detail = { taskId: 'task', latestAttempt: revision, latestSuccessful: revision, revisions: [revision],
  capabilities: { publication: true }, release: { approval: null, deployments: [] } } as unknown as RepairTaskDetail
it('confirms without deploying, then requires a second explicit deployment confirmation', async () => {
  const target = { id: 't', label: 'Pre Bot', botId: 'bot', ownerId: 'owner', environment: 'pre' }
  api.deploymentTargets.mockResolvedValue({ targets: [target] }); api.confirm.mockResolvedValue({}); api.deploy.mockResolvedValue({})
  const refresh = vi.fn()
  const view = render(<RepairReleaseFlow detail={detail} selectedRevision={1} canEdit onRefresh={refresh} />)
  await userEvent.click(screen.getByRole('button', { name: '确认候选', exact: true }))
  await userEvent.selectOptions(await screen.findByRole('combobox', { name: '部署目标 Bot' }), 't')
  await userEvent.click(screen.getByRole('checkbox'))
  await userEvent.click(screen.getByRole('button', { name: '仅确认候选' }))
  expect(api.confirm).toHaveBeenCalledOnce(); expect(api.deploy).not.toHaveBeenCalled()
  view.rerender(<RepairReleaseFlow detail={{ ...detail, release: { approval: { id: 'approval', status: 'approved', revision: 1, stateVersion: 0, target } as never, deployments: [] } }} selectedRevision={1} canEdit onRefresh={refresh} />)
  await userEvent.click(screen.getByRole('button', { name: '部署此候选', exact: true }))
  expect(api.deploy).not.toHaveBeenCalled()
  await userEvent.click(screen.getByRole('button', { name: '确认并部署' }))
  expect(api.deploy).toHaveBeenCalledOnce()
})
it('does not offer executable approval for history or read-only users', () => {
  render(<RepairReleaseFlow detail={detail} selectedRevision={0} canEdit={false} onRefresh={() => {}} />)
  expect(screen.getByRole('button', { name: '确认候选' })).toBeDisabled()
  expect(screen.getByText(/当前查看的是历史候选/)).toBeInTheDocument()
})
it('keeps the last successful candidate actionable after a failed feedback round', () => {
  render(<RepairReleaseFlow detail={{ ...detail, latestAttempt: { ...revision, revision: 2, phase: 'failed' } }} selectedRevision={1} canEdit onRefresh={() => {}} />)
  expect(screen.getByRole('button', { name: '确认候选' })).toBeEnabled()
})
