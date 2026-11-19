import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { clearCachedClientUserForTests, useClientUser } from '@avernet/clawweb-shared/web/hooks/useClientUser'
import WorkflowWorkspace from '../../WorkflowWorkspace'
import { dashboardApi } from '../api'
import { mockOverview, mockWorkflowHealth } from '../mock'

const listRuns = vi.hoisted(() => vi.fn())
vi.mock('@avernet/clawweb-shared/web/api/client', async (importOriginal) => {
  const original = await importOriginal<typeof import('@avernet/clawweb-shared/web/api/client')>()
  return { ...original, api: { ...original.api, runs: { ...original.api.runs, list: listRuns } } }
})
vi.mock('../../../api/hooks', () => ({
  useWorkflowTypes: () => ({ isError: true, error: new Error('Workflow list unavailable'), refetch: vi.fn() }),
  useCreateWorkflow: () => ({ isPending: false }),
  useCloneWorkflow: () => ({ isPending: false }),
}))
vi.mock('../../../components/workflow-workspace/OverviewTab', () => ({ default: () => null }))
vi.mock('../../../components/workflow-workspace/EditorTab', () => ({ default: () => null }))
vi.mock('../../../components/workflow-workspace/ManagementTab', () => ({ default: () => null }))
vi.mock('../../../components/workflow-workspace/EvolutionTab', () => ({ default: () => null }))
vi.mock('../../../components/workflow-workspace/ApprovalWorkbench', () => ({ default: () => null }))
vi.mock('echarts-for-react', () => ({ default: () => null }))

const NOW = 1_800_000_000
const DEMO_FLAG = 'clawweb_dashboard_demo'
let client: QueryClient
let user: { userId: string; isAdmin: boolean } | null
let authResponse: Promise<Response> | undefined
const fetchMock = vi.fn(async (input: string) => {
  const url = new URL(input, window.location.origin)
  if (url.pathname === '/api/auth/me') return authResponse ?? (user ? Response.json(user) : new Response(null, { status: 401 }))
  if (!user) throw new Error(`Request without a signed-in user: ${input}`)
  const from = Number(url.searchParams.get('from'))
  const to = Number(url.searchParams.get('to'))
  const title = user.isAdmin ? 'Admin-only workflow' : `Scoped workflow: ${user.userId}`
  const totalRuns = user.isAdmin ? 987 : user.userId === 'staff' ? 12 : 23
  switch (url.pathname) {
    case '/api/dashboard/overview': return Response.json({ ...mockOverview(from, to), totalRuns })
    case '/api/dashboard/workflow-health': return Response.json({ from, to, workflows: [{ ...mockWorkflowHealth(from, to).workflows[0], workflowId: 'wf', workflowTitle: title }] })
    case '/api/dashboard/daily-trend': return Response.json({ dates: [] })
    case '/api/dashboard/workflow-release-stats': return Response.json({ available: true, workflows: [] })
    case '/api/dashboard/failure-hotspots': return Response.json({ stages: [], workflows: [] })
    case '/api/dashboard/release-efficiency': return Response.json({ available: true, releaseCount: 2 })
    case '/api/dashboard/release-quality-trend': return Response.json({ available: true, points: [] })
    case '/api/dashboard/metric-trend': return Response.json({ available: true, points: [] })
    default: throw new Error(`Unexpected request: ${input}`)
  }
})

function signIn(userId: string, isAdmin: boolean) {
  window.__TERN__ = { user: { outUserNo: userId, clientUser: { isAdmin } } }
  user = { userId, isAdmin }
}
function IdentityProbe() {
  // Simulate another app page leaving a recently resolved identity in the shared hook cache.
  const { user: appUser } = useClientUser()
  return <output aria-label="app-role">{String(appUser?.isAdmin)}</output>
}
function Location() {
  return <output aria-label="location">{useLocation().search}</output>
}
function mount(search = '?tab=dashboard', withAppIdentity = false) {
  return render(<QueryClientProvider client={client}>
    <MemoryRouter initialEntries={[`/workflows/workspace${search}`]}><WorkflowWorkspace /><Location />{withAppIdentity && <IdentityProbe />}</MemoryRouter>
  </QueryClientProvider>)
}
function clickButton(name: string | RegExp) {
  fireEvent.click(screen.getByRole('button', { name }))
}
function expectDashboardRequest(path: string) {
  expect(fetchMock).toHaveBeenCalledWith(expect.stringMatching(new RegExp(`^/api/dashboard/${path}\\?`)), expect.anything())
}

beforeEach(() => {
  // Fresh cache entries make accidental cross-scope reuse observable without refetch races.
  client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } })
  vi.spyOn(Date, 'now').mockReturnValue(NOW * 1000)
  fetchMock.mockClear()
  listRuns.mockReset().mockResolvedValue({ total: 1, runs: [{ flow_id: 'private-run', workflow_title: 'Admin-only run', status: 'failed', started_at: NOW }] })
  vi.stubGlobal('fetch', fetchMock)
  localStorage.setItem(DEMO_FLAG, '0')
  user = null
  authResponse = undefined
})
afterEach(() => {
  cleanup()
  client.clear()
  clearCachedClientUserForTests()
  delete window.__TERN__
  localStorage.removeItem(DEMO_FLAG)
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('Workspace data dashboard access', () => {
  it('opens a real scoped deep link despite denials in the workflow list and a stored demo flag', async () => {
    localStorage.setItem(DEMO_FLAG, '1')
    signIn('staff', false)
    // Ignore both a premature TERN role flag and a request-supplied role.
    window.__TERN__ = { user: { outUserNo: 'staff', clientUser: { isAdmin: true } } }
    let completeAuth!: (response: Response) => void
    authResponse = new Promise<Response>((resolve) => { completeAuth = resolve })
    mount('?tab=dashboard&role=admin&demo=1')
    expect(within(screen.getByRole('main')).getByRole('status')).toHaveTextContent('正在确认数据大盘访问权限')
    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual(['/api/auth/me'])
    completeAuth(Response.json(user))

    expect(await screen.findByRole('heading', { name: '数据大盘' })).toBeInTheDocument()
    expect(screen.getByText(/仅统计您有权查看的工作流/)).toBeInTheDocument()
    expect(screen.queryByText('示例数据')).not.toBeInTheDocument()
    expect(screen.queryByText('Workflow list unavailable')).not.toBeInTheDocument()
    const row = await screen.findByRole('row', { name: /Scoped workflow: staff/ })
    expect(row).not.toHaveAttribute('tabindex')
    fireEvent.click(row)
    expect(screen.getByLabelText('location')).toHaveTextContent('tab=dashboard')

    clickButton('查看异常结束')
    const drawer = screen.getByRole('dialog', { name: '异常结束明细' })
    expect(within(drawer).getByText('每日异常结束趋势')).toBeInTheDocument()
    expect(within(drawer).queryByText('运行明细')).not.toBeInTheDocument()
    expect(listRuns).not.toHaveBeenCalled()
    clickButton('关闭')

    clickButton(/运行中/)
    const live = screen.getByRole('dialog', { name: '运行中明细' })
    expect(within(live).getByText('当前状态存量')).toBeInTheDocument()
    expect(within(live).getByText('当前范围仅展示聚合统计，不提供逐条运行明细。')).toBeInTheDocument()
    expect(listRuns).not.toHaveBeenCalled()
    clickButton('关闭')

    clickButton(/运行成功率.*成功 \/ 全部已结束运行/)
    const ranked = screen.getByRole('dialog', { name: '运行成功率明细' })
    expect(within(ranked).getByText('Scoped workflow: staff')).toBeInTheDocument()
    expect(within(ranked).queryByRole('button', { name: /Scoped workflow/ })).not.toBeInTheDocument()
    clickButton('关闭')

    clickButton(/研发效能/)
    expect(await screen.findByRole('row', { name: /Scoped workflow: staff/ })).not.toHaveAttribute('tabindex')
    clickButton(/活跃工作流/)
    await waitFor(() => {
      expectDashboardRequest('release-quality-trend')
      expectDashboardRequest('metric-trend')
    })
    const keys = client.getQueryCache().getAll().map((q) => q.queryKey)
    expect(keys.length).toBeGreaterThan(0)
    expect(keys.every(([prefix, scope]) => prefix === 'dashboard' && JSON.stringify(scope) === JSON.stringify({ userId: 'staff', isAdmin: false, demo: false }))).toBe(true)
    expect(fetchMock.mock.calls.some(([url]) => /workflow-metrics|scene-breakdown|subsystem-summary/.test(url))).toBe(false)
  })

  it('keeps admin drilldowns and separates the same user downgraded to viewer and a second viewer', async () => {
    signIn('staff', true)
    const admin = mount('?tab=dashboard', true)
    await waitFor(() => expect(screen.getByLabelText('app-role')).toHaveTextContent('true'))
    expect(await screen.findByRole('button', { name: /Admin-only workflow/ })).toHaveAttribute('tabindex', '0')
    clickButton('查看异常结束')
    expect(await screen.findByRole('button', { name: /Admin-only run/ })).toBeInTheDocument()
    expect(listRuns).toHaveBeenCalledTimes(1)
    clickButton('关闭')
    clickButton(/运行成功率.*成功 \/ 全部已结束运行/)
    expect(within(screen.getByRole('dialog')).getByRole('button', { name: /Admin-only workflow/ })).toBeInTheDocument()
    clickButton('关闭')
    clickButton(/研发效能/)
    clickButton(/活跃工作流/)
    await waitFor(() => {
      expectDashboardRequest('release-quality-trend')
      expectDashboardRequest('metric-trend')
    })
    admin.unmount()
    fetchMock.mockClear()

    // The app's shared auth promise still has the admin role available for reuse.
    signIn('staff', false)
    const viewer = mount('')
    clickButton('数据大盘')
    expect(screen.getByLabelText('location')).toHaveTextContent('tab=dashboard')
    expect(await screen.findByRole('row', { name: /Scoped workflow: staff/ })).toBeInTheDocument()
    expect(screen.queryByText('Admin-only workflow')).not.toBeInTheDocument()
    clickButton('查看异常结束')
    expect(screen.queryByText('Admin-only run')).not.toBeInTheDocument()
    expect(listRuns).toHaveBeenCalledTimes(1)
    clickButton('关闭')
    clickButton(/研发效能/)
    clickButton(/活跃工作流/)
    await waitFor(() => {
      expectDashboardRequest('release-quality-trend')
      expectDashboardRequest('metric-trend')
    })
    viewer.unmount()

    signIn('other', false)
    mount()
    expect(await screen.findByRole('row', { name: /Scoped workflow: other/ })).toBeInTheDocument()
    expect(screen.queryByText('Scoped workflow: staff')).not.toBeInTheDocument()
  })

  it('keeps admin demo data and legacy API defaults separate from real admin data', async () => {
    signIn('staff', true)
    const real = mount()
    expect(await screen.findByText('Admin-only workflow')).toBeInTheDocument()
    real.unmount()
    fetchMock.mockClear()
    localStorage.setItem(DEMO_FLAG, '1')
    mount()
    expect(await screen.findByText('示例数据')).toBeInTheDocument()
    expect(await screen.findByRole('table')).toBeInTheDocument()
    expect(screen.queryByText('Admin-only workflow')).not.toBeInTheDocument()
    expect((await dashboardApi.overview(NOW - 86400, NOW)).totalRuns).not.toBe(987)
    expect(fetchMock.mock.calls.every(([url]) => url === '/api/auth/me')).toBe(true)
  })

  it('asks a signed-out visitor to sign in without sending dashboard requests', async () => {
    mount()
    expect(await screen.findByText('请登录后查看数据大盘')).toBeInTheDocument()
    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual(['/api/auth/me'])
  })
})
