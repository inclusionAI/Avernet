import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { useObservedAuthEpoch } from '../useObservedAuthEpoch'

vi.mock('@avernet/clawweb-shared/web/hooks/useClientUser', () => ({ isDevAuthMode: () => false }))
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers() })
const auth = (userId: string) => ({ ok: true, json: async () => ({ userId }) })

describe('observed identity invalidation', () => {
  it('suspends on focus and rejects an earlier late identity response', async () => {
    let resolveOld!: (value: unknown) => void
    const fetchMock = vi.fn().mockImplementationOnce(() => new Promise(resolve => { resolveOld = resolve }))
      .mockResolvedValueOnce(auth('new-user'))
    vi.stubGlobal('fetch', fetchMock)
    const { result } = renderHook(() => useObservedAuthEpoch())
    expect(result.current).toBeNull()
    act(() => window.dispatchEvent(new Event('focus')))
    await waitFor(() => expect(result.current).toBe(1))
    await act(async () => { resolveOld(auth('old-user')); await Promise.resolve() })
    expect(result.current).toBe(1)
    expect(fetchMock.mock.calls[0][1].signal.aborted).toBe(true)
  })

  it('increments the epoch when periodic server display identity changes and fails closed on error', async () => {
    vi.useFakeTimers()
    const fetchMock = vi.fn().mockResolvedValueOnce(auth('user-a')).mockResolvedValueOnce(auth('user-b')).mockRejectedValueOnce(new Error('offline'))
    vi.stubGlobal('fetch', fetchMock)
    const { result } = renderHook(() => useObservedAuthEpoch())
    await act(async () => { await Promise.resolve(); await Promise.resolve() })
    expect(result.current).toBe(1)
    await act(async () => { await vi.advanceTimersByTimeAsync(5000) })
    expect(result.current).toBe(2)
    await act(async () => { await vi.advanceTimersByTimeAsync(5000) })
    expect(result.current).toBeNull()
  })
  it('fails closed when an identity refresh hangs and ignores its late response', async () => {
    vi.useFakeTimers()
    let resolveSlow!: (value: unknown) => void
    const fetchMock = vi.fn().mockResolvedValueOnce(auth('user-a')).mockImplementationOnce(() => new Promise(resolve => { resolveSlow = resolve }))
    vi.stubGlobal('fetch', fetchMock)
    const { result } = renderHook(() => useObservedAuthEpoch())
    await act(async () => { await Promise.resolve(); await Promise.resolve() })
    expect(result.current).toBe(1)
    await act(async () => { await vi.advanceTimersByTimeAsync(8000) })
    expect(result.current).toBeNull()
    expect(fetchMock.mock.calls[1][1].signal.aborted).toBe(true)
    await act(async () => { resolveSlow(auth('user-a')); await Promise.resolve() })
    expect(result.current).toBeNull()
  })

})
