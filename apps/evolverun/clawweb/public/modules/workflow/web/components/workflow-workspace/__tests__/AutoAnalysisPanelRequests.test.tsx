import { act, fireEvent, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import AutoAnalysisPanel from '../AutoAnalysisPanel'

const requests: { url: URL; method: string; body?: Record<string, unknown> }[] = []
const policy = { workflowId: 'wf/a', enabled: false, autoDraft: false, dailyTime: '09:00', version: 0, dailyConfigured: false, source: 'default', timezone: 'Asia/Shanghai', updatedAtMs: 0 }
let saveError = ''
beforeEach(() => {
  requests.length = 0
  saveError = ''
  vi.stubGlobal('fetch', vi.fn(async (input: string, init?: RequestInit) => {
    const url = new URL(input, 'http://localhost'), method = init?.method ?? 'GET'
    const body = init?.body ? JSON.parse(String(init.body)) : undefined
    requests.push({ url, method, body })
    if (url.pathname.endsWith('/auto-analysis')) return { ok: !saveError, json: async () => saveError ? { error: saveError } : { ...policy, ...body } }
    if (url.pathname.endsWith('/automation-status')) return { ok: true, json: async () => ({ jobs: [], retryableJobs: [], coverage: { total: 32, pending: 0, failed: 0, partial: 24, unknown: 7 } }) }
    if (url.pathname.endsWith('/automation-records')) return { ok: true, json: async () => ({
      jobs: url.searchParams.get('kind') === 'analysis' ? [{ id: 'run-job', kind: 'auto_run', status: 'done', attempts: 1, reason: null, flowId: 'run/a', complete: true }] : [], nextCursor: null,
    }) }
    throw new Error(`Unexpected request: ${method} ${url.pathname}`)
  }))
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
