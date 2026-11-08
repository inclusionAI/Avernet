import { act, fireEvent, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import AutoAnalysisPanel from '../AutoAnalysisPanel'

const requests: { url: URL; method: string; body?: Record<string, unknown> }[] = []
const policy = { workflowId: 'wf/a', enabled: false, autoDraft: false, dailyTime: '09:00', version: 0, dailyConfigured: false, source: 'default', timezone: 'Asia/Shanghai', updatedAtMs: 0 }
let saveError = ''
let waitForSave: Promise<void> | undefined
let storedPolicy: Record<string, unknown>
beforeEach(() => {
  requests.length = 0
  saveError = ''
  waitForSave = undefined
  storedPolicy = { ...policy }
  vi.stubGlobal('fetch', vi.fn(async (input: string, init?: RequestInit) => {
    const url = new URL(input, 'http://localhost'), method = init?.method ?? 'GET'
    const body = init?.body ? JSON.parse(String(init.body)) : undefined
    requests.push({ url, method, body })
    if (method === 'PUT') await waitForSave
    if (url.pathname.endsWith('/auto-analysis')) {
      if (method === 'PUT' && !saveError) storedPolicy = { ...storedPolicy, ...body, version: Number(storedPolicy.version) + 1 }
      return { ok: !saveError, json: async () => saveError ? { error: saveError } : { ...storedPolicy } }
    }
    if (url.pathname.endsWith('/automation-status')) return { ok: true, json: async () => ({ jobs: [], retryableJobs: [], coverage: { total: 32, pending: 0, failed: 0, partial: 24, unknown: 7 } }) }
    if (url.pathname.endsWith('/automation-records')) return { ok: true, json: async () => ({
      jobs: url.searchParams.get('kind') === 'analysis' ? [{ id: 'run-job', kind: 'auto_run', status: 'done', attempts: 1, reason: null, flowId: 'run/a', complete: true }] : [], nextCursor: null,
    }) }
    throw new Error(`Unexpected request: ${method} ${url.pathname}`)
  }))
})

it('explicitly saves the displayed daily plan and enables optimization in one request, without saving other drafts', async () => {
  storedPolicy = { ...policy, enabled: true, version: 7, executionEnvironment: 'pre' }
  mount()
  const start = await screen.findByRole('button', { name: '保存计划并开启自动优化' })
  expect(screen.getByText('尚未启用')).toBeVisible()
  expect(screen.queryByRole('checkbox', { name: '自动生成优化草稿' })).not.toBeInTheDocument()
  expect(requests.every(request => request.method === 'GET')).toBe(true)
  fireEvent.change(screen.getByLabelText('每日执行时间'), { target: { value: '10:30' } })
  await userEvent.selectOptions(screen.getByLabelText('最低影响'), 'high')
  await userEvent.type(screen.getByLabelText('通知人'), 'unsaved-recipient')
  expect(screen.getByText(/每天 10:30.*高等及以上/)).toBeVisible()
  await userEvent.click(start)
  expect(await screen.findByRole('checkbox', { name: '自动生成优化草稿' })).toBeChecked()
  expect(requests.filter(request => request.method === 'PUT').map(request => request.body)).toEqual([
    { enabled: true, autoDraft: true, dailyTime: '10:30', minimumImpact: 'high', dailyConfigured: true, expectedVersion: 7 },
  ])
  expect(screen.getByLabelText('通知人')).toHaveValue('unsaved-recipient')
  expect(screen.getByRole('combobox', { name: '自动任务执行环境' })).toHaveValue('pre')
})

it('acknowledges combined activation immediately, blocks duplicate writes and retains the plan after rejection', async () => {
  storedPolicy = { ...policy, enabled: true }
  mount()
  const start = await screen.findByRole('button', { name: '保存计划并开启自动优化' })
  fireEvent.change(screen.getByLabelText('每日执行时间'), { target: { value: '11:45' } })
  let release!: () => void
  waitForSave = new Promise(resolve => { release = resolve })
  saveError = '计划保存失败'
  await userEvent.click(start)
  expect(screen.getByRole('status')).toHaveTextContent('正在保存计划并开启自动优化')
  expect(screen.getByRole('button', { name: '开启中…' })).toBeDisabled()
  await userEvent.click(screen.getByRole('button', { name: '开启中…' }))
  expect(requests.filter(request => request.method === 'PUT')).toHaveLength(1)
  await act(async () => { release() })
  expect(await screen.findByRole('alert')).toHaveTextContent('计划保存失败')
  expect(screen.getByLabelText('每日执行时间')).toHaveValue('11:45')
  expect(screen.queryByRole('checkbox', { name: '自动生成优化草稿' })).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: '保存计划并开启自动优化' })).toBeEnabled()
})

it('offers an analysis enable action without implicitly activating the daily plan or optimization', async () => {
  mount()
  await userEvent.click(await screen.findByRole('button', { name: '开启自动分析' }))
  expect(await screen.findByRole('button', { name: '保存计划并开启自动优化' })).toBeEnabled()
  expect(requests.filter(request => request.method === 'PUT').map(request => request.body)).toEqual([
    { enabled: true, autoDraft: false, dailyTime: '09:00', dailyConfigured: false, expectedVersion: 0 },
  ])
})

it('keeps configured optimization toggles separate from unsaved daily-plan edits', async () => {
  storedPolicy = { ...policy, enabled: true, dailyConfigured: true, minimumImpact: 'medium' }
  mount()
  const toggle = await screen.findByRole('checkbox', { name: '自动生成优化草稿' })
  fireEvent.change(screen.getByLabelText('每日执行时间'), { target: { value: '14:30' } })
  await userEvent.selectOptions(screen.getByLabelText('最低影响'), 'high')
  await userEvent.click(toggle)
  expect(toggle).toBeChecked()
  expect(requests.filter(request => request.method === 'PUT').map(request => request.body)).toEqual([
    { enabled: true, autoDraft: true, dailyTime: '09:00', dailyConfigured: true, expectedVersion: 0 },
  ])
  expect(screen.getByLabelText('每日执行时间')).toHaveValue('14:30')
  expect(screen.getByLabelText('最低影响')).toHaveValue('high')
})
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals() })
function mount() {
  const query = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return render(<QueryClientProvider client={query}><AutoAnalysisPanel workflowId="wf/a" /></QueryClientProvider>)
}

it('opens settings without writes or hidden history/status requests, and loads each record kind on demand', async () => {
  mount()
  expect(await screen.findByRole('combobox', { name: '自动任务执行环境' })).toHaveValue('prod')
  expect(requests.map(request => [request.method, request.url.pathname])).toEqual([['GET', '/api/task-guard/workflows/wf%2Fa/auto-analysis']])
  expect(screen.queryByRole('link', { name: '查看分析' })).not.toBeInTheDocument()
  expect(screen.queryByText(/分析记录 32/)).not.toBeInTheDocument()
  const analysis = within(screen.getByRole('region', { name: '自动分析设置' }))
  const optimization = within(screen.getByRole('region', { name: '自动优化' }))
  await userEvent.click(analysis.getByRole('button', { name: '查看记录' }))
  expect(await analysis.findByRole('link', { name: '查看分析' })).toHaveAttribute('href', '/runs/run%2Fa')
  expect(requests.filter(request => request.url.pathname.endsWith('/automation-records')).map(request => request.url.searchParams.get('kind'))).toEqual(['analysis'])
  expect(requests.find(request => request.url.pathname.endsWith('/automation-records'))!.url.searchParams.get('limit')).toBe('20')
  await userEvent.click(analysis.getByRole('button', { name: '收起记录' }))
  expect(screen.queryByRole('link', { name: '查看分析' })).not.toBeInTheDocument()
  const count = requests.length
  vi.useFakeTimers()
  await act(async () => { await vi.advanceTimersByTimeAsync(45000) })
  expect(requests).toHaveLength(count)
  vi.useRealTimers()
  await userEvent.click(optimization.getByRole('button', { name: '查看记录' }))
  expect(await optimization.findByText('暂无自动优化记录。')).toBeVisible()
  expect(requests.filter(request => request.url.pathname.endsWith('/automation-records')).map(request => request.url.searchParams.get('kind'))).toEqual(['analysis', 'optimization'])
  expect(requests.every(request => request.method === 'GET')).toBe(true)
  expect(requests.every(request => /\/(auto-analysis|automation-status|automation-records)$/.test(request.url.pathname))).toBe(true)
})

it('preserves a configured pre-production environment when opening settings', async () => {
  vi.mocked(fetch).mockResolvedValueOnce({ ok: true, json: async () => ({ ...policy, executionEnvironment: 'pre' }) } as Response)
  mount()
  expect(await screen.findByRole('combobox', { name: '自动任务执行环境' })).toHaveValue('pre')
  expect(fetch).toHaveBeenCalledOnce()
  expect(vi.mocked(fetch).mock.calls[0][1]?.method).toBeUndefined()
})

it('shows immediate environment saves beside the selector and clears success after 2.5 seconds', async () => {
  mount()
  const select = await screen.findByRole('combobox', { name: '自动任务执行环境' })
  vi.useFakeTimers()
  await act(async () => {
    fireEvent.change(select, { target: { value: 'pre' } })
    expect(within(screen.getByRole('region', { name: '自动任务执行环境' })).getByRole('status')).toHaveTextContent('执行环境保存中')
    await vi.advanceTimersByTimeAsync(10)
  })
  expect(requests.find(request => request.method === 'PUT')?.body).toMatchObject({ executionEnvironment: 'pre', enabled: false, dailyConfigured: false })
  const feedback = within(screen.getByRole('region', { name: '自动任务执行环境' })).getByRole('status')
  expect(feedback).toHaveTextContent('执行环境已保存')
  expect(feedback.className).not.toMatch(/sticky|fixed/)
  await act(async () => { await vi.advanceTimersByTimeAsync(2000) })
  expect(feedback).toBeVisible()
  await act(async () => { await vi.advanceTimersByTimeAsync(1000) })
  expect(screen.queryByText('执行环境已保存')).not.toBeInTheDocument()
})

it('keeps a rejected environment save visible beside the selector and preserves the saved environment', async () => {
  mount()
  const select = await screen.findByRole('combobox', { name: '自动任务执行环境' })
  saveError = '环境保存失败'
  vi.useFakeTimers()
  await act(async () => { fireEvent.change(select, { target: { value: 'pre' } }); await vi.advanceTimersByTimeAsync(10) })
  const feedback = within(screen.getByRole('region', { name: '自动任务执行环境' })).getByRole('alert')
  expect(feedback).toHaveTextContent('环境保存失败')
  await act(async () => { await vi.advanceTimersByTimeAsync(4000) })
  expect(feedback).toBeVisible()
  expect(select).toHaveValue('prod')
  expect(requests.filter(request => request.method === 'PUT')).toHaveLength(1)
})
