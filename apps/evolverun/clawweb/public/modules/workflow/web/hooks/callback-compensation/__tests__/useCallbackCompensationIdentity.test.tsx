import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, it, expect, vi } from 'vitest'
import { useCallbackCompensationIdentity } from '../useCallbackCompensationIdentity'

vi.mock('@avernet/clawweb-shared/web/hooks/useClientUser', () => ({ isDevAuthMode: () => false }))
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers() })
const auth = (userId: unknown, isAdmin = true) => ({ ok: true, json: async () => ({ userId, isAdmin }) })

it('observes the native server actor, not spoofable display headers or a client role flag', async () => {
  const fetchMock = vi.fn().mockResolvedValue(auth('actor-a', false))
  vi.stubGlobal('fetch', fetchMock)
  const { result } = renderHook(() => useCallbackCompensationIdentity())
  await waitFor(() => expect(result.current?.actorId).toBe('actor-a'))
  expect(result.current?.scopeKey).toBe(JSON.stringify([window.location.origin, 'normal']))
  expect(fetchMock).toHaveBeenCalledWith('/api/auth/me', expect.objectContaining({
    credentials: 'include', cache: 'no-store', signal: expect.any(AbortSignal),
  }))
  // False isAdmin still reaches the capabilities check; this observer is not authorization.
  expect(result.current?.generation).toBe(1)
})

it('suspends on focus and ignores an earlier late identity response', async () => {
  let resolve!: (value: unknown) => void
  const fetchMock = vi.fn().mockImplementationOnce(() => new Promise(done => { resolve = done }))
    .mockResolvedValueOnce(auth('new-actor'))
  vi.stubGlobal('fetch', fetchMock)
  const { result } = renderHook(() => useCallbackCompensationIdentity())
  act(() => window.dispatchEvent(new Event('focus')))
  await waitFor(() => expect(result.current?.actorId).toBe('new-actor'))
  expect(fetchMock.mock.calls[0][1].signal.aborted).toBe(true)
  await act(async () => resolve(auth('old-actor')))
  expect(result.current?.actorId).toBe('new-actor')
})

it('changes generation for actor and role changes, then fails closed on expired auth', async () => {
  vi.useFakeTimers()
  vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(auth('a')).mockResolvedValueOnce(auth('b'))
    .mockResolvedValueOnce(auth('b', false)).mockResolvedValueOnce({ ok: false }))
  const { result } = renderHook(() => useCallbackCompensationIdentity())
  await act(async () => { await Promise.resolve(); await Promise.resolve() })
  expect(result.current?.generation).toBe(1)
  await act(async () => { await vi.advanceTimersByTimeAsync(5000) })
  expect(result.current?.actorId).toBe('b')
  expect(result.current?.generation).toBe(2)
  await act(async () => { await vi.advanceTimersByTimeAsync(5000) })
  expect(result.current?.generation).toBe(3)
  await act(async () => { await vi.advanceTimersByTimeAsync(5000) })
  expect(result.current).toBeNull()
})

it('times out a hanging identity read, aborts and ignores its late response', async () => {
  vi.useFakeTimers()
  let resolve!: (value: unknown) => void
  const fetchMock = vi.fn().mockImplementationOnce(() => new Promise(done => { resolve = done }))
  vi.stubGlobal('fetch', fetchMock)
  const { result } = renderHook(() => useCallbackCompensationIdentity())
  await act(async () => { await vi.advanceTimersByTimeAsync(3000) })
  expect(result.current).toBeNull()
  expect(fetchMock.mock.calls[0][1].signal.aborted).toBe(true)
  await act(async () => resolve(auth('late')))
  expect(result.current).toBeNull()
})

it('aborts identity reads when the view unmounts', () => {
  const fetchMock = vi.fn().mockReturnValue(new Promise(() => {}))
  vi.stubGlobal('fetch', fetchMock)
  const view = renderHook(() => useCallbackCompensationIdentity())
  view.unmount()
  expect(fetchMock.mock.calls[0][1].signal.aborted).toBe(true)
})
