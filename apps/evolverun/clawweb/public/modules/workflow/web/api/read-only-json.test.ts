import { afterEach, describe, expect, it, vi } from 'vitest'
import { repairBatches } from './repair-batches'

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals() })

describe('bounded repair reads', () => {
  it('ends a stalled detail read and permits an explicit fresh retry', async () => {
    vi.useFakeTimers()
    let aborted = false
    vi.stubGlobal('fetch', vi.fn().mockImplementationOnce((_url: string, init: RequestInit) => new Promise((_resolve, reject) => {
      init.signal?.addEventListener('abort', () => { aborted = true; reject(init.signal?.reason) })
    })).mockResolvedValueOnce({ ok: true, json: async () => ({ itemId: 'item-1' }) }))
    const failed = expect(repairBatches.item('wf-1', 'item-1')).rejects.toThrow(/读取超时/)
    await vi.advanceTimersByTimeAsync(30_000)
    await failed
    expect(aborted).toBe(true)
    expect(await repairBatches.item('wf-1', 'item-1')).toEqual({ itemId: 'item-1' })
  })

  it('clears its timer after a successful candidate read', async () => {
    vi.useFakeTimers()
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({ items: [] }) }))
    expect(await repairBatches.candidates('wf-1')).toEqual({ items: [] })
    expect(vi.getTimerCount()).toBe(0)
  })
})
