import { act, cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  capabilities: vi.fn(), preview: vi.fn(), create: vi.fn(), batch: vi.fn(), stop: vi.fn(),
  identity: vi.fn(),
  user: { userId: 'admin', nickName: '显示用户', isAdmin: false },
  refetch: vi.fn(),
}))
vi.mock('../../../api/callback-compensation', () => ({
  callbackCompensationService: {
    capabilities: mocks.capabilities, preview: mocks.preview, create: mocks.create, batch: mocks.batch, stop: mocks.stop,
  },
}))
vi.mock('../../../hooks/callback-compensation/useCallbackCompensationIdentity', () => ({
  useCallbackCompensationIdentity: mocks.identity,
}))
vi.mock('@avernet/clawweb-shared/web/hooks/useClientUser', () => ({
  getClientUser: () => mocks.user,
  useClientUser: () => mocks.user,
}))
vi.mock('../../../api/hooks', () => ({
  useWorkflowHealth: () => ({ data: null, isError: false }),
  useWorkflowHealthTrend: () => ({ data: [] }),
  useFlowRuns: () => ({
    data: { runs: [], total: 0, statusCounts: {} }, isLoading: false, isPending: false,
    isError: false, error: null, refetch: mocks.refetch, isFetching: false,
  }),
  useAnalysisProgress: () => ({ data: null, isError: false }),
  useAnalyzeRun: () => ({ isPending: false, isError: false, error: null, mutate: vi.fn(), reset: vi.fn() }),
  useEligibleBotsForAnalyze: () => ({ data: { bots: [] }, isLoading: false }),
}))
vi.mock('../../AnalyzeRunBotModal', () => ({ default: () => null }))
vi.mock('../../AutoHealPanel', () => ({ default: () => null }))
vi.mock('../../ApprovalSlidePanel', () => ({ default: () => null }))
vi.mock('@avernet/clawweb-shared/web/api/hooks', () => ({
  useDeleteFlowRun: () => ({ mutate: vi.fn(), isPending: false }),
  useRerunFlowRun: () => ({ mutate: vi.fn(), isPending: false }),
  useRunArchive: () => ({ data: null, isLoading: false, isError: false, error: null }),
  useFlowApprovals: () => ({ data: { items: [] }, isLoading: false }),
}))
vi.mock('../../SuccessTrendCard', () => ({ SuccessTrendCard: () => <div>成功率趋势图</div> }))
vi.mock('../../RunCountTrendCard', () => ({ RunCountTrendCard: () => <div>运行实例趋势图</div> }))
vi.mock('../../NodeAnalysisPanel', () => ({
  NodeAnalysisPanel: () => <div>原生节点分析内容</div>,
  default: () => <div>原生节点分析内容</div>,
}))

// Deliberately do not mock CallbackCompensation or its own state hooks/dialog.
import OverviewTab from '../OverviewTab'

const workflow = {
  workflow_id: 'wf/a', workflow_title: '真实工作流', run_count: 0,
  last_status: 'succeeded', last_run_at: 1, updated_at: 1,
}
const capabilities = {
  workflowId: workflow.workflow_id, environment: 'PRE', canPreview: true,
  executionEnabled: true, blockedReasons: [],
  configs: [{ configId: 'cfg', name: '通知接收者', receiverId: 'receiver', targetOrigin: 'https://receiver.example' }],
}
function overview(selected = workflow) {
  return <MemoryRouter><OverviewTab workflow={selected} /></MemoryRouter>
}
function expectNoMutations() {
  expect(mocks.preview).not.toHaveBeenCalled()
  expect(mocks.create).not.toHaveBeenCalled()
  expect(mocks.stop).not.toHaveBeenCalled()
}

beforeEach(() => {
  vi.clearAllMocks()
  sessionStorage.clear()
  localStorage.clear()
  mocks.user.isAdmin = false
  mocks.identity.mockReturnValue({ actorId: 'admin', scopeKey: `normal:${window.location.origin}`, generation: 1 })
  mocks.capabilities.mockResolvedValue(capabilities)
})
afterEach(() => cleanup())

describe('callback compensation in the native workflow run overview', () => {
  it('places the real authorized entry left of Refresh, hides it on nodes and restores it on runs', async () => {
    render(overview())
    const entry = await screen.findByRole('button', { name: '成功回调补偿' })
    const refresh = screen.getByRole('button', { name: '刷新' })
    expect(entry.compareDocumentPosition(refresh) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(mocks.capabilities).toHaveBeenCalledWith(workflow.workflow_id, expect.any(AbortSignal))
    expectNoMutations()
    await userEvent.click(screen.getByRole('button', { name: '节点分析', exact: true }))
    expect(screen.getByText('原生节点分析内容')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '成功回调补偿' })).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '运行列表', exact: true }))
    expect(await screen.findByRole('button', { name: '成功回调补偿' })).toBeEnabled()
    await userEvent.click(screen.getByRole('button', { name: '刷新' }))
    expect(mocks.refetch).toHaveBeenCalledTimes(2)
    expectNoMutations()
  })

  it('opens the actual native compensation dialog rather than a mocked entry component', async () => {
    render(overview())
    await userEvent.click(await screen.findByRole('button', { name: '成功回调补偿' }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByRole('combobox', { name: '接收配置' })).toHaveDisplayValue('通知接收者')
    expect(dialog).toHaveTextContent('wf/a')
    expect(dialog).toHaveTextContent('PRE')
    expect(dialog).toHaveTextContent(/不重跑/)
    expectNoMutations()
  })

  it('does not expose an entry or issue compensation requests without a selected workflow', async () => {
    await act(async () => { render(overview({ ...workflow, workflow_id: '' })) })
    expect(screen.queryByRole('button', { name: '成功回调补偿' })).not.toBeInTheDocument()
    expect(mocks.capabilities).not.toHaveBeenCalled()
    expect(mocks.batch).not.toHaveBeenCalled()
    expectNoMutations()
  })

  it('discards the entry when selection is cleared and makes no new compensation request', async () => {
    const view = render(overview())
    await screen.findByRole('button', { name: '成功回调补偿' })
    const requests = mocks.capabilities.mock.calls.length
    view.rerender(overview({ ...workflow, workflow_id: '' }))
    expect(screen.queryByRole('button', { name: '成功回调补偿' })).not.toBeInTheDocument()
    expect(mocks.capabilities).toHaveBeenCalledTimes(requests)
    expectNoMutations()
  })

  it.each([false, true])('uses capabilities, not local isAdmin/canEdit=%s, as the authorization gate', async (editable) => {
    mocks.user.isAdmin = editable
    mocks.capabilities.mockRejectedValue(Object.assign(new Error('private administrator denial'), {
      status: 403, data: { code: 'FORBIDDEN', message: 'secret-denied-receiver-config' },
    }))
    await act(async () => { render(overview({ ...workflow, ...{ canEdit: editable, isAdmin: editable } })) })
    await waitFor(() => expect(mocks.capabilities).toHaveBeenCalledOnce())
    expect(screen.queryByRole('button', { name: '成功回调补偿' })).not.toBeInTheDocument()
    expect(screen.queryByRole('combobox', { name: '接收配置' })).not.toBeInTheDocument()
    expect(document.body).not.toHaveTextContent('secret-denied-receiver-config')
    expect(document.body).not.toHaveTextContent('private administrator denial')
    expect(document.body).not.toHaveTextContent('通知接收者')
    expectNoMutations()
  })

  it('does not require workflow editing permission or a local administrator flag after server authorization', async () => {
    render(overview({ ...workflow, ...{ canEdit: false, isAdmin: false } }))
    expect(await screen.findByRole('button', { name: '成功回调补偿' })).toBeEnabled()
    expect(mocks.capabilities).toHaveBeenCalledOnce()
    expectNoMutations()
  })

  it('renders a generic unavailable state for 503 without displaying raw error data or receiver metadata', async () => {
    mocks.capabilities.mockRejectedValue(Object.assign(new Error('secret-raw-upstream-message'), {
      status: 503, data: { code: 'UNAVAILABLE', message: 'secret-raw-upstream-body' }, body: 'secret-raw-upstream-body',
    }))
    render(overview())
    expect(await screen.findByText(/补偿服务不可用|补偿服务暂不可用/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '成功回调补偿' })).not.toBeInTheDocument()
    expect(document.body).not.toHaveTextContent('secret-raw-upstream')
    expect(document.body).not.toHaveTextContent('通知接收者')
    expectNoMutations()
  })

  it('keeps authorized review-only hosts scannable even when execution is disabled', async () => {
    mocks.capabilities.mockResolvedValue({ ...capabilities, executionEnabled: false, blockedReasons: ['MISSING_EVIDENCE'] })
    mocks.preview.mockResolvedValue({
      workflowId: workflow.workflow_id, environment: 'PRE', previewId: 'preview-review',
      receiver: capabilities.configs[0], expiresAt: '2099-11-28T10:00:00Z',
      items: [], summary: { eligible: 0, delivered: 0, review: 0, ineligible: 0 },
    })
    render(overview())
    await userEvent.click(await screen.findByRole('button', { name: '成功回调补偿' }))
    const dialog = await screen.findByRole('dialog')
    const scan = within(dialog).getByRole('button', { name: /生成预览|扫描/ })
    expect(scan).toBeEnabled()
    await userEvent.click(scan)
    await waitFor(() => expect(mocks.preview).toHaveBeenCalledOnce())
    expect(mocks.preview).toHaveBeenCalledWith(
      { workflowId: workflow.workflow_id, environment: 'PRE' },
      expect.objectContaining({ configId: 'cfg', from: expect.any(String), to: expect.any(String) }),
      expect.any(AbortSignal),
    )
    expect(mocks.create).not.toHaveBeenCalled()
    expect(mocks.stop).not.toHaveBeenCalled()
  })
})
