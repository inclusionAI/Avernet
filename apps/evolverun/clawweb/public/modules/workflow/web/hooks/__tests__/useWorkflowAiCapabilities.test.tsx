import { act, cleanup, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
const api = vi.hoisted(() => ({ capabilities: vi.fn() }))
vi.mock('../../api/workflow-ai', () => ({ workflowAiApi: api }))
import { useWorkflowAiCapabilities } from '../useWorkflowAiCapabilities'

beforeEach(() => { api.capabilities.mockReset() })
afterEach(() => { cleanup(); vi.useRealTimers() })

describe('AutoHeal/BaaS capability lifecycle', () => {
  it('shows loading, server unavailability state, and explicit retry', async () => {
    api.capabilities.mockResolvedValueOnce({ available: false, reason: 'AUTO_HEAL_BOT_UNAVAILABLE' }).mockResolvedValueOnce({ available: true })
    const { result } = renderHook(() => useWorkflowAiCapabilities(true, 'flow'))
    expect(result.current.loading).toBe(true)
    await waitFor(() => expect(result.current.value).toEqual({ available: false, reason: 'AUTO_HEAL_BOT_UNAVAILABLE' }))
    act(() => result.current.retry())
    await waitFor(() => expect(result.current.value?.available).toBe(true))
    expect(api.capabilities).toHaveBeenCalledTimes(2)
  })

  it('surfaces capability transport failure without proving server config missing', async () => {
    api.capabilities.mockRejectedValueOnce(new Error('API 404'))
    const { result } = renderHook(() => useWorkflowAiCapabilities(true, 'flow'))
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.error).toContain('无法确认')
    expect(result.current.error).toContain('autoHeal.botId')
    expect(result.current.error).toContain('BaaS')
    expect(result.current.error).toContain('不证明')
    expect(result.current.value).toBeUndefined()
  })

  it('ignores a stale non-cooperative capability response after workflow changes', async () => {
    let resolve!: (value: { available: boolean }) => void
    api.capabilities.mockReturnValueOnce(new Promise(done => { resolve = done })).mockResolvedValueOnce({ available: false, reason: 'second workflow' })
    const { result, rerender } = renderHook(({ workflowId }) => useWorkflowAiCapabilities(true, workflowId), { initialProps: { workflowId: 'first' } })
    const signal = api.capabilities.mock.calls[0][1]
    rerender({ workflowId: 'second' })
    await waitFor(() => expect(result.current.value?.reason).toBe('second workflow'))
    expect(signal.aborted).toBe(true)
    await act(async () => resolve({ available: true }))
    expect(result.current.value?.available).toBe(false)
  })

  it('bounds non-cooperative capability reads and allows retry', async () => {
    vi.useFakeTimers()
    api.capabilities.mockReturnValueOnce(new Promise(() => {})).mockResolvedValueOnce({ available: true })
    const { result } = renderHook(() => useWorkflowAiCapabilities(true, 'flow'))
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000) })
    expect(result.current.loading).toBe(false)
    expect(result.current.error).toContain('超时')
    await act(async () => result.current.retry())
    expect(result.current.value?.available).toBe(true)
  })

  it('does not check while closed or without a workflow, and aborts on close', async () => {
    api.capabilities.mockReturnValue(new Promise(() => {}))
    const { rerender } = renderHook(({ open, id }) => useWorkflowAiCapabilities(open, id), { initialProps: { open: false, id: '' } })
    expect(api.capabilities).not.toHaveBeenCalled()
    rerender({ open: true, id: 'flow' })
    const signal = api.capabilities.mock.calls[0][1]
    rerender({ open: false, id: 'flow' })
    expect(signal.aborted).toBe(true)
  })
})
