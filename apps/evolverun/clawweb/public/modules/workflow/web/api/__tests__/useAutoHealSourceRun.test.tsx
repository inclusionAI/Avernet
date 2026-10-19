import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, renderHook } from '@testing-library/react'
import type { PropsWithChildren } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useAutoHealSourceRun, type SourceAutoHealRunRequest } from '../useAutoHealSourceRun'

// Exercise the source fetchJson implementation, not a potentially stale dist build.
vi.mock('@avernet/clawweb-shared/web/api/client', () => import('../../../../../shared/web/api/client'))

let queryClient: QueryClient
const fetchMock = vi.fn<typeof fetch>()
const request: SourceAutoHealRunRequest = { workflowId: 'cron-workflow', sourceFlowId: 'original-cron-flow' }
const result = { ok: true, workflowId: request.workflowId, flowId: 'new-flow' }

function wrapper({ children }: PropsWithChildren) {
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
}

beforeEach(() => {
  // Even if the app opts into mutation retries, sending a task must remain explicit.
  queryClient = new QueryClient({ defaultOptions: { mutations: { retry: 2, retryDelay: 0 } } })
  fetchMock.mockReset().mockResolvedValue(new Response(JSON.stringify(result)))
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => {
  cleanup()
  queryClient.clear()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('useAutoHealSourceRun', () => {
  it('posts the source flow and params through fetchJson, never client bot/stage overrides', async () => {
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries')
    const { result: hook } = renderHook(useAutoHealSourceRun, { wrapper })
    const input = { ...request, params: { limit: 3 }, botId: 'retired-diagnostic-bot', stage: 'untrusted-stage' }

    await act(async () => {
      await expect(hook.current.mutateAsync(input)).resolves.toEqual(result)
    })

    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/auto-heal/run')
    expect(init?.method).toBe('POST')
    expect(JSON.parse(String(init?.body))).toEqual({ ...request, params: { limit: 3 } })
    expect(new Headers(init?.headers).get('Content-Type')).toBe('application/json')
    expect(init?.cache).toBe('no-store')
    expect(invalidate).toHaveBeenCalledTimes(2)
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['runs'] })
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['workflow-types'] })
  })

  it('allows cron runs without params or input.command', async () => {
    const { result: hook } = renderHook(useAutoHealSourceRun, { wrapper })
    await act(async () => { await hook.current.mutateAsync(request) })
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual(request)
  })

  it.each(['', '  ', undefined])('rejects missing sourceFlowId (%s) before HTTP', async (sourceFlowId) => {
    const { result: hook } = renderHook(useAutoHealSourceRun, { wrapper })
    const invalid = { ...request, sourceFlowId } as SourceAutoHealRunRequest
    await act(async () => {
      await expect(hook.current.mutateAsync(invalid)).rejects.toThrow(/sourceFlowId/)
    })
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('preserves HTTP errors without auto-retrying or invalidating success queries', async () => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ message: 'BaaS 400: source bot unavailable' }), { status: 400 }))
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries')
    const { result: hook } = renderHook(useAutoHealSourceRun, { wrapper })
    await act(async () => {
      await expect(hook.current.mutateAsync(request)).rejects.toThrow('BaaS 400: source bot unavailable')
    })
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(invalidate).not.toHaveBeenCalled()
  })
})
