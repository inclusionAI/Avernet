import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, renderHook, waitFor } from '@testing-library/react'
import type { PropsWithChildren } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { aiStudioTrainError, aiStudioTrainRefetchInterval, getAIStudioTrainWatch, pollAIStudioTrainManually, useAIStudioTrainWatch } from '../aistudio-train'
import { trainWatch } from './aistudio-train.fixture'
import { toWatchDto } from '../../../server/contracts/aistudio-train'
import { watchFixture } from '../../../server/contracts/__tests__/aistudio-train.fixture'

vi.mock('@avernet/clawweb-shared/web/hooks/useClientUser', () => ({
  getClientUser: () => ({ userId: 'viewer', nickName: '观察者' }),
}))
let client: QueryClient
const fetchMock = vi.fn<typeof fetch>()
const reply = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status })
function wrapper({ children }: PropsWithChildren) {
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>
}
beforeEach(() => {
  client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: 2 } } })
  fetchMock.mockReset().mockResolvedValue(reply({ watch: trainWatch }))
  vi.stubGlobal('fetch', fetchMock)
})
afterEach(() => { cleanup(); client.clear(); vi.unstubAllGlobals(); vi.restoreAllMocks() })

describe('AIStudio train same-origin contract', () => {
  it('encodes node identity and attempt and reuses user headers', async () => {
    await getAIStudioTrainWatch('flow/1', 'train #1', 2)
    expect(fetchMock.mock.calls[0][0]).toBe('/api/runs/flow%2F1/nodes/train%20%231/aistudio-train?attempt=2')
    const init = fetchMock.mock.calls[0][1]
    expect(new Headers(init?.headers).get('X-User-Id')).toBe('viewer')
    expect(new Headers(init?.headers).get('X-User-Name')).toBe(encodeURIComponent('观察者'))
    expect(init?.credentials).toBe('same-origin')
  })
  it('posts only attempt; mailbox 202 is not a resolved node', async () => {
    const pending = { ok: true, resolved: false, watch: { ...trainWatch, version: 2, manualPollPending: true, manualPollAllowed: false }, requestId: 'request-1' }
    fetchMock.mockResolvedValue(reply(pending, 202))
    expect(await pollAIStudioTrainManually('flow-1', 'train', 2)).toEqual(pending)
    expect(fetchMock.mock.calls[0][0]).toBe('/api/runs/flow-1/nodes/train/aistudio-train/poll-manual')
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual({ attempt: 2 })
  })
})

describe('AIStudio train shared watch hook', () => {
  it('shares row/detail GET and stops interval at resolved while invalidating run lists', async () => {
    const invalidate = vi.spyOn(client, 'invalidateQueries')
    const { result } = renderHook(() => [useAIStudioTrainWatch('flow-1', 'train', 2), useAIStudioTrainWatch('flow-1', 'train', 2)], { wrapper })
    await waitFor(() => expect(result.current[0].query.data?.watch).toEqual(trainWatch))
    expect(fetchMock).toHaveBeenCalledTimes(1)
    fetchMock.mockResolvedValue(reply({ ok: true, resolved: true, watch: { ...trainWatch, version: 2, phase: 'resolved', nextPollAt: null, manualPollAllowed: false } }))
    await act(async () => { await result.current[0].pollNow() })
    await waitFor(() => expect(result.current[1].query.data?.watch.phase).toBe('resolved'))
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['run', 'flow-1'] })
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['runs'] })
  })
  it('preserves a waiting plan on 202 and prevents cross-card duplicate POST', async () => {
    const { result } = renderHook(() => [useAIStudioTrainWatch('flow-1', 'train', 2), useAIStudioTrainWatch('flow-1', 'train', 2)], { wrapper })
    await waitFor(() => expect(result.current[0].query.data).toBeDefined())
    let finish!: (value: Response) => void
    fetchMock.mockImplementation(() => new Promise(resolve => { finish = resolve }))
    let first!: Promise<unknown>
    await act(async () => {
      first = result.current[0].pollNow()
      await result.current[1].pollNow()
    })
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))
    await act(async () => {
      finish(reply({ ok: true, resolved: false, watch: { ...trainWatch, version: 2, manualPollPending: true, manualPollAllowed: false } }, 202))
      await first
    })
    await waitFor(() => expect(result.current[1].query.data?.watch).toEqual({ ...trainWatch, version: 2, manualPollPending: true, manualPollAllowed: false }))
    expect(result.current[1].query.data?.watch.phase).toBe('waiting')
  })
  it('does not retry manual failures even with global retries enabled', async () => {
    const { result } = renderHook(() => useAIStudioTrainWatch('flow-1', 'train', 2), { wrapper })
    await waitFor(() => expect(result.current.query.data).toBeDefined())
    fetchMock.mockResolvedValue(reply({ error: 'AIStudio unavailable' }, 503))
    await act(async () => { await expect(result.current.pollNow()).rejects.toThrow() })
    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(result.current.query.data?.watch).toEqual(trainWatch)
  })
})

describe('AIStudio watch polling lifecycle', () => {
  it('refetches at five seconds, then stops once the server resolves', async () => {
    vi.useFakeTimers()
    try {
      client.setQueryData(['aistudio-train-watch', 'flow-1', 'train', 2], { watch: trainWatch })
      fetchMock.mockResolvedValue(reply({ watch: { ...trainWatch, version: 2, phase: 'resolved', nextPollAt: null, manualPollAllowed: false } }))
      const { result } = renderHook(() => useAIStudioTrainWatch('flow-1', 'train', 2), { wrapper })
      await act(async () => { await vi.advanceTimersByTimeAsync(4999) })
      expect(fetchMock).not.toHaveBeenCalled()
      await act(async () => { await vi.advanceTimersByTimeAsync(2) })
      expect(fetchMock).toHaveBeenCalledTimes(1)
      await act(async () => { await vi.advanceTimersByTimeAsync(1) })
      expect(result.current.query.data?.watch.phase).toBe('resolved')
      await act(async () => { await vi.advanceTimersByTimeAsync(20000) })
      expect(fetchMock).toHaveBeenCalledTimes(1)
    } finally { vi.useRealTimers() }
  })
  it('refetches pending mailboxes and keeps attempt caches isolated', async () => {
    vi.useFakeTimers()
    try {
      client.setQueryData(['aistudio-train-watch', 'flow-1', 'train', 2], { watch: { ...trainWatch, phase: 'resolved', manualPollPending: true } })
      client.setQueryData(['aistudio-train-watch', 'flow-1', 'train', 3], { watch: { ...trainWatch, attempt: 3, phase: 'resolved' } })
      fetchMock.mockResolvedValue(reply({ watch: { ...trainWatch, version: 2, phase: 'resolved', manualPollAllowed: false } }))
      const { result } = renderHook(() => [useAIStudioTrainWatch('flow-1', 'train', 2), useAIStudioTrainWatch('flow-1', 'train', 3)], { wrapper })
      await act(async () => { await vi.advanceTimersByTimeAsync(5001) })
      expect(fetchMock).toHaveBeenCalledTimes(1)
      expect(String(fetchMock.mock.calls[0][0])).toContain('?attempt=2')
      expect(result.current[0].query.data?.watch).toMatchObject({ version: 2, phase: 'resolved', manualPollPending: false })
      expect(result.current[1].query.data?.watch.attempt).toBe(3)
    } finally { vi.useRealTimers() }
  })
  it('does not auto-poll a missing watch or retry its 404', async () => {
    fetchMock.mockResolvedValue(reply({ error: 'not found' }, 404))
    const { result } = renderHook(() => useAIStudioTrainWatch('flow-1', 'train', 2), { wrapper })
    await waitFor(() => expect(result.current.query.isError).toBe(true))
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })
  it('does not refetch terminal data when the detail observer mounts later', async () => {
    client.setQueryData(['aistudio-train-watch', 'flow-1', 'train', 2], { watch: { ...trainWatch, phase: 'resolved' } }, { updatedAt: Date.now() - 60000 })
    renderHook(() => useAIStudioTrainWatch('flow-1', 'train', 2), { wrapper })
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)) })
    expect(fetchMock).not.toHaveBeenCalled()
  })
  it('cancels a stale GET started while a manual query was in flight', async () => {
    client.setQueryData(['aistudio-train-watch', 'flow-1', 'train', 2], { watch: trainWatch })
    let finishManual!: (value: Response) => void
    let finishGet!: (value: Response) => void
    fetchMock.mockImplementation((_url, init) => new Promise(resolve => {
      if (init?.method === 'POST') finishManual = resolve
      else finishGet = resolve
    }))
    const { result } = renderHook(() => useAIStudioTrainWatch('flow-1', 'train', 2), { wrapper })
    let command!: Promise<unknown>
    await act(async () => { command = result.current.pollNow(); await Promise.resolve() })
    await act(async () => { void result.current.query.refetch(); await Promise.resolve() })
    await act(async () => {
      finishManual(reply({ ok: true, resolved: true, watch: { ...trainWatch, version: 2, phase: 'resolved', nextPollAt: null, manualPollAllowed: false } }))
      await command
    })
    await act(async () => { finishGet(reply({ watch: trainWatch })); await Promise.resolve() })
    await waitFor(() => expect(result.current.query.data?.watch.phase).toBe('resolved'))
  })
})


describe('AIStudio watch boundary helpers', () => {
  it.each(['waiting', 'polling', 'resolving'] as const)('refreshes active phase %s', phase => {
    expect(aiStudioTrainRefetchInterval({ ...trainWatch, phase })).toBe(5000)
  })
  it('stops without data or at terminal, except an explicitly pending command', () => {
    expect(aiStudioTrainRefetchInterval(undefined)).toBe(false)
    expect(aiStudioTrainRefetchInterval({ ...trainWatch, phase: 'resolved' })).toBe(false)
    expect(aiStudioTrainRefetchInterval({ ...trainWatch, phase: 'resolved', manualPollPending: true })).toBe(5000)
  })
  it.each(['null', '[]', '"proxy error"', '<html>gateway error</html>'])('handles error bodies that are not JSON objects (%s)', body => {
    expect(aiStudioTrainError({ status: 502, body }).message).toContain('上游查询失败')
  })
  it('preserves generic server message, retryAt and network error text', () => {
    expect(aiStudioTrainError({ status: 400, body: '{"message":"invalid attempt","retryAt":"2026-11-16T01:00:00Z"}' })).toEqual({ message: 'invalid attempt', retryAt: '2026-11-16T01:00:00Z' })
    expect(aiStudioTrainError(new Error('network offline')).message).toBe('network offline')
    expect(aiStudioTrainError(null).message).toContain('节点状态不变')
  })
})

describe('newer server observations supersede command snapshots', () => {
  it.each(['waiting', 'resolved'] as const)('clears a stale manual error after a newer successful %s GET', async phase => {
    const { result } = renderHook(() => useAIStudioTrainWatch('flow-1', 'train', 2), { wrapper })
    await waitFor(() => expect(result.current.query.data).toBeDefined())
    fetchMock.mockResolvedValue(reply({ error: 'temporarily unavailable' }, 503))
    await act(async () => { await expect(result.current.pollNow()).rejects.toThrow() })
    await waitFor(() => expect(result.current.operationError).toBeTruthy())
    fetchMock.mockResolvedValue(reply({ watch: { ...trainWatch, version: 2, phase, nextPollAt: phase === 'resolved' ? null : trainWatch.nextPollAt } }))
    await act(async () => { await result.current.query.refetch() })
    await waitFor(() => expect(result.current.operationError).toBeNull())
  })
  it('shows a new GET error instead of an older manual failure', async () => {
    const { result } = renderHook(() => useAIStudioTrainWatch('flow-1', 'train', 2), { wrapper })
    await waitFor(() => expect(result.current.query.data).toBeDefined())
    fetchMock.mockResolvedValue(reply({ error: 'unavailable' }, 503))
    await act(async () => { await expect(result.current.pollNow()).rejects.toThrow() })
    await waitFor(() => expect(result.current.operationError).toBeTruthy())
    fetchMock.mockResolvedValue(reply({ error: 'watch missing' }, 404))
    await act(async () => { await result.current.query.refetch() })
    await waitFor(() => expect(result.current.operationError).toBeNull())
    expect(aiStudioTrainError(result.current.query.error).message).toContain('未找到')
  })
  it.each(['waiting', 'polling', 'resolving'] as const)('preserves a newer %s GET from a late detail observer over a delayed 202', async phase => {
    vi.useFakeTimers()
    try {
      const key = ['aistudio-train-watch', 'flow-1', 'train', 2]
      client.setQueryData(key, { watch: trainWatch })
      const retryAt = new Date(Date.now() + 60000).toISOString()
      const newer = { ...trainWatch, version: 3, phase, lastStatus: phase === 'resolving' ? 'SUCCESS' : 'RUNNING', manualPollPending: false, manualPollAllowed: false, manualPollRetryAt: retryAt }
      let finishManual!: (response: Response) => void
      fetchMock.mockImplementation((_url, init) => init?.method === 'POST'
        ? new Promise(resolve => { finishManual = resolve })
        : Promise.resolve(reply({ watch: newer })))
      const row = renderHook(() => useAIStudioTrainWatch('flow-1', 'train', 2), { wrapper })
      let command!: Promise<unknown>
      await act(async () => { command = row.result.current.pollNow(); await vi.advanceTimersByTimeAsync(1) })
      expect(finishManual).toBeTypeOf('function')
      await act(async () => { await vi.advanceTimersByTimeAsync(6000) })
      expect(fetchMock).toHaveBeenCalledTimes(1) // Interval GETs pause while the POST is in flight.
      const detail = renderHook(() => useAIStudioTrainWatch('flow-1', 'train', 2), { wrapper })
      await act(async () => { await vi.advanceTimersByTimeAsync(1) })
      expect(fetchMock).toHaveBeenCalledTimes(2)
      expect(client.getQueryData(key)).toEqual({ watch: newer })
      await act(async () => {
        finishManual(reply({ ok: true, resolved: false, watch: { ...trainWatch, version: 2, manualPollPending: true, manualPollAllowed: false } }, 202))
        await command
        await vi.advanceTimersByTimeAsync(1)
      })
      expect(client.getQueryData(key)).toEqual({ watch: newer })
      expect(row.result.current.query.data?.watch).toEqual(newer)
      expect(detail.result.current.query.data?.watch).toEqual(newer)
      expect(row.result.current.isPending).toBe(false)
      expect(detail.result.current.retryAt).toBe(retryAt)
      await act(async () => { await detail.result.current.pollNow() })
      expect(fetchMock).toHaveBeenCalledTimes(2) // The newer server cooldown still applies.
      fetchMock.mockImplementation(() => Promise.resolve(reply({ watch: { ...newer, version: 4, phase: 'resolved', nextPollAt: null } })))
      await act(async () => { await vi.advanceTimersByTimeAsync(5001) })
      expect(fetchMock).toHaveBeenCalledTimes(3)
      expect(client.getQueryData(key)).toMatchObject({ watch: { phase: 'resolved', manualPollPending: false } })
      await act(async () => { await vi.advanceTimersByTimeAsync(20000) })
      expect(fetchMock).toHaveBeenCalledTimes(3)
    } finally { vi.useRealTimers() }
  })
  it.each([false, true])('accepts a higher-version POST after an identical pre-commit GET at the same timestamp (resolved=%s)', async resolved => {
    vi.useFakeTimers()
    try {
      const key = ['aistudio-train-watch', 'flow-1', 'train', 2]
      const observed = { watch: { ...trainWatch, manualPollPending: false } }
      client.setQueryData(key, observed)
      const updatedAt = client.getQueryState(key)?.dataUpdatedAt
      let finishManual!: (response: Response) => void
      fetchMock.mockImplementation((_url, init) => init?.method === 'POST'
        ? new Promise(resolve => { finishManual = resolve })
        : Promise.resolve(reply(observed)))
      const { result } = renderHook(() => useAIStudioTrainWatch('flow-1', 'train', 2), { wrapper })
      let command!: Promise<unknown>
      await act(async () => { command = result.current.pollNow(); await Promise.resolve() })
      expect(finishManual).toBeTypeOf('function')
      await act(async () => { await result.current.query.refetch() })
      // Neither identical data nor equal client timestamps imply this GET includes the POST commit.
      expect(client.getQueryData(key)).toBe(observed)
      expect(client.getQueryState(key)?.dataUpdatedAt).toBe(updatedAt)
      const committed = { ...trainWatch, version: 2, phase: resolved ? 'resolved' : 'waiting',
        manualPollPending: !resolved, manualPollAllowed: false,
        manualPollRetryAt: new Date(Date.now() + 60000).toISOString() }
      await act(async () => {
        finishManual(reply({ ok: true, resolved, watch: committed }, resolved ? 200 : 202))
        await command
        await vi.advanceTimersByTimeAsync(1)
      })
      expect(client.getQueryData(key)).toEqual({ watch: committed })
      expect(result.current.query.data?.watch).toEqual(committed)
      expect(result.current.isPending).toBe(false)
    } finally { vi.useRealTimers() }
  })
  it('applies an unopposed 202 pending snapshot and cooldown, then resumes shared five-second GETs', async () => {
    vi.useFakeTimers()
    try {
      const key = ['aistudio-train-watch', 'flow-1', 'train', 2]
      client.setQueryData(key, { watch: trainWatch })
      const retryAt = new Date(Date.now() + 60000).toISOString()
      const accepted = { ...trainWatch, version: 2, manualPollPending: true, manualPollAllowed: false, manualPollRetryAt: retryAt }
      const consumed = { ...accepted, version: 3, manualPollPending: false }
      fetchMock.mockImplementation((_url, init) => Promise.resolve(init?.method === 'POST'
        ? reply({ ok: true, resolved: false, watch: accepted }, 202)
        : reply({ watch: consumed })))
      const { result } = renderHook(() => [useAIStudioTrainWatch('flow-1', 'train', 2), useAIStudioTrainWatch('flow-1', 'train', 2)], { wrapper })
      await act(async () => { await result.current[0].pollNow(); await vi.advanceTimersByTimeAsync(1) })
      expect(fetchMock).toHaveBeenCalledTimes(1)
      expect(client.getQueryData(key)).toEqual({ watch: accepted })
      expect(result.current[1].query.data?.watch).toEqual(accepted)
      expect(result.current[1].retryAt).toBe(retryAt)
      await act(async () => { await result.current[1].pollNow(); await vi.advanceTimersByTimeAsync(4998) })
      expect(fetchMock).toHaveBeenCalledTimes(1)
      await act(async () => { await vi.advanceTimersByTimeAsync(3) })
      expect(fetchMock).toHaveBeenCalledTimes(2)
      expect(client.getQueryData(key)).toEqual({ watch: consumed })
      expect(result.current[0].query.data?.watch.manualPollPending).toBe(false)
      await act(async () => { await result.current[0].pollNow() })
      expect(fetchMock).toHaveBeenCalledTimes(2) // Consuming the mailbox does not lift the cooldown.
    } finally { vi.useRealTimers() }
  })
  it('lets committed 202 win over a pre-commit detail GET, rejecting stale GETs until a higher server version', async () => {
    vi.useFakeTimers()
    try {
      const key = ['aistudio-train-watch', 'flow-1', 'train', 2]
      client.setQueryData(key, { watch: trainWatch })
      const retryAt = new Date(Date.now() + 60000).toISOString()
      const accepted = { ...trainWatch, version: 2, manualPollPending: true, manualPollAllowed: false, manualPollRetryAt: retryAt }
      let getWatch = trainWatch
      let finishManual!: (response: Response) => void
      fetchMock.mockImplementation((_url, init) => init?.method === 'POST'
        ? new Promise(resolve => { finishManual = resolve })
        : Promise.resolve(reply({ watch: getWatch })))
      const row = renderHook(() => useAIStudioTrainWatch('flow-1', 'train', 2), { wrapper })
      let command!: Promise<unknown>
      await act(async () => { command = row.result.current.pollNow(); await vi.advanceTimersByTimeAsync(6001) })
      const detail = renderHook(() => useAIStudioTrainWatch('flow-1', 'train', 2), { wrapper })
      await act(async () => { await vi.advanceTimersByTimeAsync(1) })
      expect(fetchMock).toHaveBeenCalledTimes(2)
      expect(client.getQueryData(key)).toEqual({ watch: trainWatch }) // GET read before the mailbox commit.
      await act(async () => {
        finishManual(reply({ ok: true, resolved: false, watch: accepted }, 202))
        await command
        await vi.advanceTimersByTimeAsync(1)
      })
      expect(client.getQueryData(key)).toEqual({ watch: accepted })
      expect(row.result.current.query.data?.watch).toEqual(accepted)
      expect(detail.result.current.query.data?.watch).toEqual(accepted)
      expect(detail.result.current.retryAt).toBe(retryAt)
      await act(async () => { await detail.result.current.pollNow(); await vi.advanceTimersByTimeAsync(5001) })
      expect(fetchMock).toHaveBeenCalledTimes(3) // One GET, no duplicate POST.
      expect(client.getQueryData(key)).toEqual({ watch: accepted }) // Delayed v1 GET cannot drop confirmation.
      getWatch = accepted
      await act(async () => { await vi.advanceTimersByTimeAsync(5001) })
      expect(fetchMock).toHaveBeenCalledTimes(4)
      expect(client.getQueryData(key)).toEqual({ watch: accepted }) // Equal v2 remains pending.
      getWatch = { ...accepted, version: 3, manualPollPending: false }
      await act(async () => { await vi.advanceTimersByTimeAsync(5001) })
      expect(client.getQueryData(key)).toEqual({ watch: getWatch })
      expect(detail.result.current.query.data?.watch.manualPollPending).toBe(false)
      expect(detail.result.current.retryAt).toBe(retryAt)
      await act(async () => { await detail.result.current.pollNow() })
      expect(fetchMock).toHaveBeenCalledTimes(5) // A consumed mailbox still honors cooldown.
    } finally { vi.useRealTimers() }
  })
  it.each(['pending', 'resolved'] as const)('does not downgrade a %s server snapshot on lower-version GETs', async state => {
    vi.useFakeTimers()
    try {
      const key = ['aistudio-train-watch', 'flow-1', 'train', 2]
      const current = { ...trainWatch, version: 3, phase: state === 'resolved' ? 'resolved' : 'waiting',
        nextPollAt: state === 'resolved' ? null : trainWatch.nextPollAt, manualPollPending: state === 'pending',
        manualPollAllowed: false, manualPollRetryAt: new Date(Date.now() + 60000).toISOString() }
      client.setQueryData(key, { watch: current })
      fetchMock.mockImplementation(() => Promise.resolve(reply({ watch: trainWatch })))
      const { result } = renderHook(() => useAIStudioTrainWatch('flow-1', 'train', 2), { wrapper })
      await act(async () => { await result.current.query.refetch(); await vi.advanceTimersByTimeAsync(1) })
      expect(client.getQueryData(key)).toEqual({ watch: current })
      expect(result.current.query.data?.watch).toEqual(current)
      expect(result.current.retryAt).toBe(current.manualPollRetryAt)
      if (state === 'resolved') {
        await act(async () => { await vi.advanceTimersByTimeAsync(20000) })
        expect(fetchMock).toHaveBeenCalledTimes(1)
      }
    } finally { vi.useRealTimers() }
  })
  it('refreshes same-version server cooldown eligibility and allows a new command after expiry', async () => {
    vi.useFakeTimers()
    try {
      const now = Date.parse('2026-11-16T00:09:00Z') / 1000
      vi.setSystemTime(now * 1000)
      const key = ['aistudio-train-watch', 'flow-1', 'train', 2]
      const record = watchFixture({ version: 4, attempt: 2, lastManualRequestedAt: now - 30,
        firstPollAt: now + 600, nextPollAt: now + 600, timeoutAt: now + 3600 })
      const before = toWatchDto(record, now)
      client.setQueryData(key, { watch: before })
      fetchMock.mockImplementation((_url, init) => {
        const serverNow = Math.floor(Date.now() / 1000)
        return Promise.resolve(init?.method === 'POST'
          ? reply({ ok: true, resolved: false, watch: toWatchDto({ ...record, version: 5,
            manualRequestedAt: serverNow, lastManualRequestedAt: serverNow }, serverNow) }, 202)
          : reply({ watch: toWatchDto(record, serverNow) }))
      })
      const { result } = renderHook(() => [useAIStudioTrainWatch('flow-1', 'train', 2), useAIStudioTrainWatch('flow-1', 'train', 2)], { wrapper })
      expect(result.current[0].query.data?.watch.manualPollAllowed).toBe(false)
      await act(async () => { await result.current[0].pollNow(); await vi.advanceTimersByTimeAsync(29999) })
      expect(result.current[1].query.data?.watch.manualPollAllowed).toBe(false)
      await act(async () => { await vi.advanceTimersByTimeAsync(2) })
      const eligible = toWatchDto(record, now + 30)
      expect(client.getQueryData(key)).toEqual({ watch: eligible })
      expect(result.current[1].query.data?.watch).toMatchObject({ version: 4, manualPollPending: false, manualPollAllowed: true })
      expect(result.current[1].retryAt).toBeNull()
      expect(fetchMock).toHaveBeenCalledTimes(6) // Only five-second observation GETs, not the ten-minute engine poll.
      await act(async () => { await result.current[1].pollNow(); await vi.advanceTimersByTimeAsync(1) })
      expect(fetchMock).toHaveBeenCalledTimes(7)
      expect(fetchMock.mock.calls[6][1]?.method).toBe('POST')
      expect(client.getQueryData(key)).toMatchObject({ watch: { version: 5, manualPollPending: true, manualPollAllowed: false } })
    } finally { vi.useRealTimers() }
  })
  it('refreshes same-version server eligibility when timeout passes without a persisted transition', async () => {
    vi.useFakeTimers()
    try {
      const now = Date.parse('2026-11-16T00:09:00Z') / 1000
      vi.setSystemTime(now * 1000)
      const key = ['aistudio-train-watch', 'flow-1', 'train', 2]
      const record = watchFixture({ version: 4, attempt: 2, firstPollAt: now + 600, nextPollAt: now + 600, timeoutAt: now + 5 })
      client.setQueryData(key, { watch: toWatchDto(record, now) })
      fetchMock.mockImplementation(() => Promise.resolve(reply({ watch: toWatchDto(record, Math.floor(Date.now() / 1000)) })))
      const { result } = renderHook(() => useAIStudioTrainWatch('flow-1', 'train', 2), { wrapper })
      expect(result.current.query.data?.watch.manualPollAllowed).toBe(true)
      await act(async () => { await vi.advanceTimersByTimeAsync(5001) })
      expect(client.getQueryData(key)).toEqual({ watch: toWatchDto(record, now + 5) })
      expect(result.current.query.data?.watch).toMatchObject({ version: 4, phase: 'waiting', manualPollPending: false, manualPollAllowed: false })
      await act(async () => { await result.current.pollNow() })
      expect(fetchMock).toHaveBeenCalledTimes(1)
    } finally { vi.useRealTimers() }
  })
  it('never overwrites a newer terminal GET with a delayed accepted-mailbox POST', async () => {
    client.setQueryData(['aistudio-train-watch', 'flow-1', 'train', 2], { watch: trainWatch })
    let finishManual!: (response: Response) => void
    fetchMock.mockImplementation((_url, init) => init?.method === 'POST'
      ? new Promise(resolve => { finishManual = resolve })
      : Promise.resolve(reply({ watch: { ...trainWatch, version: 3, phase: 'resolved', nextPollAt: null, manualPollAllowed: false } })))
    const { result } = renderHook(() => useAIStudioTrainWatch('flow-1', 'train', 2), { wrapper })
    await waitFor(() => expect(result.current.query.data?.watch.phase).toBe('waiting'))
    let command!: Promise<unknown>
    await act(async () => { command = result.current.pollNow() })
    await waitFor(() => expect(finishManual).toBeDefined())
    await act(async () => { await result.current.query.refetch() })
    await waitFor(() => expect(result.current.query.data?.watch.phase).toBe('resolved'))
    await act(async () => {
      finishManual(reply({ ok: true, resolved: false, watch: { ...trainWatch, version: 2, manualPollPending: true, manualPollAllowed: false } }, 202))
      await command
    })
    // Inspect cache too: React Query observer notifications can lag behind mutateAsync.
    expect(client.getQueryData(['aistudio-train-watch', 'flow-1', 'train', 2])).toMatchObject({ watch: { phase: 'resolved', nextPollAt: null } })
    await waitFor(() => expect(result.current.query.data?.watch.phase).toBe('resolved'))
  })
})


it('retires recovered error feedback without discarding an unexpired server cooldown', async () => {
  const { result } = renderHook(() => useAIStudioTrainWatch('flow-1', 'train', 2), { wrapper })
  await waitFor(() => expect(result.current.query.data).toBeDefined())
  const retryAt = new Date(Date.now() + 60000).toISOString()
  fetchMock.mockResolvedValue(reply({ error: 'cooldown', manualPollRetryAt: retryAt }, 429))
  await act(async () => { await expect(result.current.pollNow()).rejects.toThrow() })
  await waitFor(() => expect(result.current.operationError).toBeTruthy())
  fetchMock.mockResolvedValue(reply({ watch: trainWatch }))
  await act(async () => { await result.current.query.refetch() })
  await waitFor(() => expect(result.current.operationError).toBeNull())
  expect(result.current.retryAt).toBe(retryAt)
  await act(async () => { await result.current.pollNow() })
  expect(fetchMock).toHaveBeenCalledTimes(3)
})
