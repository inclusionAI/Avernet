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
  const target = { id: 't', label: '生产 Bot', botId: 'bot', ownerId: 'owner', environment: 'prod' }
  api.deploymentTargets.mockResolvedValue({ targets: [target] }); api.confirm.mockResolvedValue({}); api.deploy.mockResolvedValue({})
  const refresh = vi.fn()
  const view = render(<RepairReleaseFlow detail={detail} selectedRevision={1} canEdit onRefresh={refresh} />)
  await userEvent.click(screen.getByRole('button', { name: '审阅完成，选择生产 Bot', exact: true }))
  await userEvent.selectOptions(await screen.findByRole('combobox', { name: '部署目标 Bot' }), 't')
  await userEvent.click(screen.getByRole('checkbox'))
  await userEvent.click(screen.getByRole('button', { name: '确认候选', exact: true }))
  expect(api.confirm).toHaveBeenCalledOnce(); expect(api.deploy).not.toHaveBeenCalled()
  view.rerender(<RepairReleaseFlow detail={{ ...detail, release: { approval: { id: 'approval', status: 'approved', revision: 1, stateVersion: 0, target } as never, deployments: [] } }} selectedRevision={1} canEdit onRefresh={refresh} />)
  await userEvent.click(screen.getByRole('button', { name: '部署到生产 Bot', exact: true }))
  expect(api.deploy).not.toHaveBeenCalled()
  await userEvent.click(screen.getByRole('button', { name: '确认部署', exact: true }))
  expect(api.deploy).toHaveBeenCalledOnce()
})
it('does not offer executable approval for history or read-only users', () => {
  render(<RepairReleaseFlow detail={detail} selectedRevision={0} canEdit={false} onRefresh={() => {}} />)
  expect(screen.getByRole('button', { name: '审阅完成，选择生产 Bot' })).toBeDisabled()
  expect(screen.getByText(/当前查看的是历史候选/)).toBeInTheDocument()
})
it('keeps the last successful candidate actionable after a failed feedback round', () => {
  render(<RepairReleaseFlow detail={{ ...detail, latestAttempt: { ...revision, revision: 2, phase: 'failed' } }} selectedRevision={1} canEdit onRefresh={() => {}} />)
  expect(screen.getByRole('button', { name: '审阅完成，选择生产 Bot' })).toBeEnabled()
})

it('shows a retryable target-loading error without an empty selectable Bot control', async () => {
  api.deploymentTargets.mockRejectedValue({ status: 503, body: JSON.stringify({ code: 'CAPABILITY_UNAVAILABLE', error: '发布配置未启用' }) })
  render(<RepairReleaseFlow detail={detail} selectedRevision={1} canEdit onRefresh={() => {}} />)
  await userEvent.click(screen.getByRole('button', { name: '审阅完成，选择生产 Bot' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('发布配置未启用')
  expect(screen.queryByRole('combobox', { name: '部署目标 Bot' })).not.toBeInTheDocument()
  expect(screen.getByRole('link', { name: '查看发布配置' })).toHaveAttribute('href', '/app-config')
  expect(screen.getByRole('button', { name: '确认候选', exact: true })).toBeDisabled()
})

it('allows choosing a new production target after a confirmed failed pre deployment', async () => {
  api.deploymentTargets.mockResolvedValue({ targets: [{ id: 'prod-target', environment: 'prod', label: '生产 Bot' },
    { id: 'pre-target', environment: 'pre', label: '预发 Bot' }] })
  const failed = { ...detail, release: { approval: { id: 'old', status: 'approved', revision: 1,
    target: { environment: 'pre', label: '旧预发 Bot' } }, deployments: [{ id: 'failed', approvalId: 'old', revision: 1, status: 'failed', error: 'Node 不存在' }] } } as RepairTaskDetail
  render(<RepairReleaseFlow detail={failed} selectedRevision={1} canEdit onRefresh={() => {}} />)
  expect(screen.queryByRole('button', { name: '部署到生产 Bot' })).not.toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: '更换目标并重新确认' }))
  const select = await screen.findByRole('combobox', { name: '部署目标 Bot' })
  expect(select).toHaveTextContent('生产 Bot')
  expect(select).not.toHaveTextContent('预发 Bot')
})

it('does not offer target replacement or a new deployment while the result is uncertain', () => {
  const target = { id: 'prod', environment: 'prod', label: '生产 Bot' }
  const uncertain = { ...detail, release: { approval: { id: 'approval', status: 'approved', revision: 1, target },
    deployments: [{ id: 'pending', revision: 1, status: 'uncertain', target }] } } as RepairTaskDetail
  render(<RepairReleaseFlow detail={uncertain} selectedRevision={1} canEdit onRefresh={() => {}} />)
  expect(screen.getByRole('button', { name: '核对部署状态' })).toBeEnabled()
  expect(screen.queryByRole('button', { name: /更换.*目标/ })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: '部署到生产 Bot', exact: true })).not.toBeInTheDocument()
})
