import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterAll, beforeAll, beforeEach, expect, it, vi } from 'vitest'
import type { RepairTaskDetail } from '../../../../../server/contracts/repair-workbench'
const api = vi.hoisted(() => ({ deploymentTargets: vi.fn(), publish: vi.fn(), reconcileDeployment: vi.fn(), trials: vi.fn(), trialOptions: vi.fn(), startTrial: vi.fn() }))
vi.mock('../../../../api/repair-batches', () => ({ repairBatches: api }))
import RepairReleaseFlow from '../RepairReleaseFlow'
const revision = { revision: 1, phase: 'review', stateVersion: 1, draft: { candidateCommit: 'a'.repeat(40) } } as RepairTaskDetail['latestAttempt']
const detail = { taskId: 'task', workflowId: 'wf', latestAttempt: revision, latestSuccessful: revision, revisions: [revision],
  capabilities: { publication: true }, release: { approval: null, deployments: [] } } as unknown as RepairTaskDetail
const nativeShowModal = Object.getOwnPropertyDescriptor(HTMLDialogElement.prototype, 'showModal')
beforeAll(() => {
  // jsdom has no native top layer; exercise real focus/inert behavior in Chromium too.
  Object.defineProperty(HTMLDialogElement.prototype, 'showModal', { configurable: true, value(this: HTMLDialogElement) {
    this.setAttribute('open', ''); this.querySelector<HTMLButtonElement>('button')?.focus()
  } })
})
afterAll(() => { if (nativeShowModal) Object.defineProperty(HTMLDialogElement.prototype, 'showModal', nativeShowModal); else Reflect.deleteProperty(HTMLDialogElement.prototype, 'showModal') })
beforeEach(() => {
  vi.resetAllMocks()
  api.trials.mockResolvedValue({ configured: true, trials: [] })
  api.trialOptions.mockResolvedValue({ inputSchema: { properties: { query: { type: 'string' } } }, inputSchemaDigest: 'schema', targets: [{ id: 'bot', label: 'Production Bot' }] })
})
it('uses a single explicit decision to publish the reviewed workflow scope without Bot selection', async () => {
  api.deploymentTargets.mockResolvedValue({ targets: [{ id: 'workflow:pre:wf', scope: 'workflow', label: '工作流 wf', environment: 'pre' }] })
  api.publish.mockResolvedValue({ status: 'running' })
  render(<RepairReleaseFlow detail={detail} selectedRevision={1} canEdit onRefresh={() => {}} />)
  await userEvent.click(screen.getByRole('button', { name: '审阅并发布', exact: true }))
  expect(await screen.findByText(/工作流 wf/)).toBeInTheDocument()
  expect(screen.queryByRole('combobox')).not.toBeInTheDocument()
  expect(api.publish).not.toHaveBeenCalled()
  await userEvent.click(screen.getByRole('checkbox'))
  await userEvent.dblClick(screen.getByRole('button', { name: '确认审阅并发布', exact: true }))
  expect(api.publish).toHaveBeenCalledOnce()
  expect(api.publish).toHaveBeenCalledWith('task', expect.objectContaining({ revision: 1, targetId: 'workflow:pre:wf', acknowledgeUncoveredChecks: true }))
})

it('opens a focused review dialog immediately, even while release information is still loading', async () => {
  api.deploymentTargets.mockReturnValue(new Promise(() => {}))
  render(<RepairReleaseFlow detail={detail} selectedRevision={1} canEdit onRefresh={() => {}} />)
  const trigger = screen.getByRole('button', { name: '审阅并发布', exact: true })
  await userEvent.click(trigger)
  const dialog = screen.getByRole('dialog', { name: '审阅并发布候选 v1' })
  expect(dialog).toContainElement(document.activeElement as HTMLElement)
  expect(within(dialog).getByRole('button', { name: '确认审阅并发布' })).toBeDisabled()
  expect(api.publish).not.toHaveBeenCalled()
  await userEvent.click(within(dialog).getByRole('button', { name: '返回查看改动' }))
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  expect(trigger).toHaveFocus()
})

it('keeps preparation errors in the review dialog with read-only retry, not an unknown deployment warning', async () => {
  api.deploymentTargets.mockRejectedValueOnce({ status: 500, body: JSON.stringify({ code: 'INTERNAL_ERROR', error: 'Repair request failed', requestId: 'release-read-123' }) })
    .mockResolvedValueOnce({ targets: [{ id: 'workflow:pre:wf', scope: 'workflow', label: '工作流 wf', environment: 'pre' }] })
  render(<RepairReleaseFlow detail={detail} selectedRevision={1} canEdit onRefresh={() => {}} />)
  await userEvent.click(screen.getByRole('button', { name: '审阅并发布', exact: true }))
  const dialog = screen.getByRole('dialog', { name: '审阅并发布候选 v1' })
  expect(await within(dialog).findByRole('alert')).toHaveTextContent('尚未提交发布')
  expect(within(dialog).getByRole('alert')).toHaveTextContent('release-read-123')
  expect(within(dialog).queryByText(/操作结果尚未确认/)).not.toBeInTheDocument()
  await userEvent.click(within(dialog).getByRole('button', { name: '重新读取发布信息' }))
  expect(await within(dialog).findByText('工作流 wf')).toBeInTheDocument()
  await userEvent.click(within(dialog).getByRole('checkbox'))
  expect(within(dialog).getByRole('button', { name: '确认审阅并发布' })).toBeEnabled()
  expect(api.publish).not.toHaveBeenCalled()
})

it('explains a missing authorized workflow scope without pretending publication was attempted', async () => {
  api.deploymentTargets.mockResolvedValue({ targets: [] })
  render(<RepairReleaseFlow detail={detail} selectedRevision={1} canEdit onRefresh={() => {}} />)
  await userEvent.click(screen.getByRole('button', { name: '审阅并发布', exact: true }))
  expect(await screen.findByRole('alert')).toHaveTextContent('没有可发布的工作流范围')
  expect(screen.getByRole('alert')).toHaveTextContent('尚未提交发布')
  expect(api.publish).not.toHaveBeenCalled()
})
it('keeps read-only and historical candidates non-executable', () => {
  render(<RepairReleaseFlow detail={detail} selectedRevision={0} canEdit={false} onRefresh={() => {}} />)
  expect(screen.getByRole('button', { name: '审阅并发布' })).toBeDisabled()
})
it('reconciles an uncertain request instead of offering another publish', () => {
  render(<RepairReleaseFlow detail={{ ...detail, release: { approval: null, deployments: [{ id: 'd', revision: 1, status: 'uncertain', target: {} } as never] } }} selectedRevision={1} canEdit onRefresh={() => {}} />)
  expect(screen.getByRole('button', { name: '核对发布状态' })).toBeEnabled()
  expect(screen.queryByRole('button', { name: '审阅并发布' })).not.toBeInTheDocument()
})
it('freezes the entire publish request across an approval-only commit and lost response', async () => {
  api.deploymentTargets.mockResolvedValue({ targets: [{ id: 'workflow:pre:wf', scope: 'workflow', label: '工作流 wf', environment: 'pre' }] })
  api.publish.mockReset(); api.publish.mockRejectedValueOnce(new Error('connection lost')).mockResolvedValueOnce({ status: 'running' })
  const view = render(<RepairReleaseFlow detail={detail} selectedRevision={1} canEdit onRefresh={() => {}} />)
  await userEvent.click(screen.getByRole('button', { name: '审阅并发布', exact: true }))
  await screen.findByText('工作流 wf')
  await userEvent.click(screen.getByRole('checkbox')); await userEvent.click(screen.getByRole('button', { name: '确认审阅并发布', exact: true }))
  await screen.findByRole('alert')
  const changed = { ...revision, stateVersion: 2 }
  view.rerender(<RepairReleaseFlow detail={{ ...detail, latestAttempt: changed, latestSuccessful: changed }} selectedRevision={1} canEdit onRefresh={() => {}} />)
  await userEvent.click(screen.getByRole('button', { name: '确认审阅并发布', exact: true }))
  expect(api.publish.mock.calls[1]).toEqual(api.publish.mock.calls[0])
})
it('publishes the complete automatic round when its final batch needs no additional change', () => {
  const final = { ...revision, phase: 'no_change', input: { automation: { roundId: 'round' } } } as unknown as RepairTaskDetail['latestAttempt'];
  render(<RepairReleaseFlow detail={{ ...detail, latestAttempt: final, latestSuccessful: final, revisions: [final],
    automaticProgress: { completedBatches: 3, totalBatches: 3, changedItems: 225, publishable: true } }} selectedRevision={1} canEdit onRefresh={() => {}} />);
  expect(screen.getByRole('button', { name: '审阅并发布' })).toBeEnabled();
  expect(screen.queryByText('本轮无需重新发布')).not.toBeInTheDocument();
});
it('keeps intermediate automatic candidates and failed-round fallbacks non-executable', () => {
  const intermediate = { ...revision, input: { automation: { roundId: 'round' } } } as unknown as RepairTaskDetail['latestAttempt'];
  const view = render(<RepairReleaseFlow detail={{ ...detail, latestAttempt: intermediate, latestSuccessful: intermediate, revisions: [intermediate],
    automaticProgress: { completedBatches: 1, totalBatches: 3, changedItems: 100, publishable: false } }} selectedRevision={1} canEdit onRefresh={() => {}} />);
  expect(screen.getByRole('button', { name: '审阅并发布' })).toBeDisabled();
  view.rerender(<RepairReleaseFlow detail={{ ...detail, latestAttempt: { ...intermediate, revision: 2, phase: 'failed' }, latestSuccessful: intermediate, revisions: [intermediate],
    automaticProgress: { completedBatches: 1, totalBatches: 3, changedItems: 100, publishable: false } }} selectedRevision={1} canEdit onRefresh={() => {}} />);
  expect(screen.getByRole('button', { name: '审阅并发布' })).toBeDisabled();
});

it('hides candidate execution when the Host has no runtime capability while publication stays available', async () => {
  api.trials.mockResolvedValueOnce({ configured: false, trials: [] })
  render(<RepairReleaseFlow detail={detail} selectedRevision={1} canEdit onRefresh={() => {}} />)
  await waitFor(() => expect(api.trials).toHaveBeenCalledWith('task', 0))
  expect(screen.queryByRole('button', { name: '先试跑' })).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: '审阅并发布' })).toBeEnabled()
})

it('does not count a successful legacy saved run as successful verification of the reviewed candidate', async () => {
  api.trials.mockResolvedValueOnce({ configured: true, trials: [{ mode: 'saved', id: 'legacy', revision: 1,
    candidateCommit: 'a'.repeat(40), status: 'succeeded', target: { label: 'Bot' }, createdAtMs: 1, input: {} }] as never[] })
  api.deploymentTargets.mockResolvedValue({ targets: [{ id: 'workflow:pre:wf', scope: 'workflow', label: '工作流 wf', environment: 'pre' }] })
  render(<RepairReleaseFlow detail={detail} selectedRevision={1} canEdit onRefresh={() => {}} />)
  await screen.findByText('兼容执行：远端保存版 · 运行成功')
  await userEvent.click(screen.getByRole('button', { name: '审阅并发布', exact: true }))
  expect(await screen.findByText(/此候选尚无成功试跑记录/)).toBeInTheDocument()
})

it('opens an immediately focused modal, blocks publication and preserves inputs on close/reopen without executing', async () => {
  const user = userEvent.setup()
  render(<RepairReleaseFlow detail={detail} selectedRevision={1} canEdit onRefresh={() => {}} />)
  const trigger = await screen.findByRole('button', { name: '先试跑' })
  await user.click(trigger)
  const dialog = await screen.findByRole('dialog', { name: '先试跑候选 v1' })
  expect(dialog).toContainElement(document.activeElement as HTMLElement)
  await user.tab({ shift: true })
  expect(within(dialog).getByRole('button', { name: '返回审阅' })).toHaveFocus()
  await user.tab()
  expect(within(dialog).getByRole('button', { name: '关闭' })).toHaveFocus()
  expect(screen.getByRole('button', { name: '审阅并发布' })).toBeDisabled()
  await user.selectOptions(await within(dialog).findByRole('combobox', { name: '执行 Bot' }), 'bot')
  await user.type(within(dialog).getByRole('textbox', { name: 'query' }), 'keep this input')
  expect(within(dialog).queryByRole('checkbox', { name: '使用旧插件兼容模式' })).not.toBeInTheDocument()
  await user.click(within(dialog).getByRole('button', { name: '返回审阅' }))
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  expect(trigger).toHaveFocus()
  await user.click(trigger)
  const reopened = screen.getByRole('dialog')
  expect(within(reopened).getByRole('combobox', { name: '执行 Bot' })).toHaveValue('bot')
  expect(within(reopened).getByRole('textbox', { name: 'query' })).toHaveValue('keep this input')
  expect(api.startTrial).not.toHaveBeenCalled()
  expect(api.publish).not.toHaveBeenCalled()
  expect(api.deploymentTargets).not.toHaveBeenCalled()
})

it('allows closing during submission and reopens the same unresolved request without automatic redispatch', async () => {
  let reject!: (reason: Error) => void
  api.startTrial.mockReturnValueOnce(new Promise((_resolve, fail) => { reject = fail })).mockResolvedValueOnce({})
  const user = userEvent.setup()
  render(<RepairReleaseFlow detail={detail} selectedRevision={1} canEdit onRefresh={() => {}} />)
  const trigger = await screen.findByRole('button', { name: '先试跑' })
  await user.click(trigger)
  await user.selectOptions(await screen.findByRole('combobox', { name: '执行 Bot' }), 'bot')
  await user.type(screen.getByRole('textbox', { name: 'query' }), 'original input')
  await user.dblClick(screen.getByRole('button', { name: '开始试跑' }))
  expect(api.startTrial).toHaveBeenCalledOnce()
  await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: '关闭' }))
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  reject(new Error('response lost'))
  await screen.findByRole('alert')
  await user.click(trigger)
  expect(api.startTrial).toHaveBeenCalledOnce()
  expect(screen.getByRole('textbox', { name: 'query' })).toHaveValue('original input')
  expect(screen.getByRole('textbox', { name: 'query' })).toBeDisabled()
  await user.click(screen.getByRole('button', { name: '核对并重试原请求' }))
  await waitFor(() => expect(api.startTrial).toHaveBeenCalledTimes(2))
  expect(api.startTrial.mock.calls[1]).toEqual(api.startTrial.mock.calls[0])
  expect(api.publish).not.toHaveBeenCalled()
})

it('resets the dialog and inputs when the selected candidate changes', async () => {
  const user = userEvent.setup()
  const view = render(<RepairReleaseFlow detail={detail} selectedRevision={1} canEdit onRefresh={() => {}} />)
  await user.click(await screen.findByRole('button', { name: '先试跑' }))
  await user.selectOptions(await screen.findByRole('combobox', { name: '执行 Bot' }), 'bot')
  await user.type(screen.getByRole('textbox', { name: 'query' }), 'old input')
  const next = { ...revision, revision: 2, draft: { candidateCommit: 'b'.repeat(40) } } as RepairTaskDetail['latestAttempt']
  view.rerender(<RepairReleaseFlow detail={{ ...detail, latestAttempt: next, latestSuccessful: next, revisions: [revision, next] }} selectedRevision={2} canEdit onRefresh={() => {}} />)
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  await user.click(await screen.findByRole('button', { name: '先试跑' }))
  const dialog = screen.getByRole('dialog', { name: '先试跑候选 v2' })
  expect(await within(dialog).findByRole('combobox', { name: '执行 Bot' })).toHaveValue('')
  expect(within(dialog).getByRole('textbox', { name: 'query' })).toHaveValue('')
  expect(api.startTrial).not.toHaveBeenCalled()
})
