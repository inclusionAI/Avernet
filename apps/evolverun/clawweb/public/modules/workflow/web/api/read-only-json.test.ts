import { afterEach, describe, expect, it, vi } from 'vitest'
import { repairBatches } from './repair-batches'
import { gunzipSync } from 'node:zlib'

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals() })

describe('bounded repair reads', () => {
  it('sends a 50-issue preview scope in the body without an oversized request URL', async () => {
    const previewSignatures = Array.from({ length: 50 }, (_, i) => `${i}:${'节点'.repeat(250)}`)
    let requestUrl = '', requestInit: RequestInit | undefined
    vi.stubGlobal('fetch', vi.fn(async (url: string, init: RequestInit) => {
      requestUrl = url; requestInit = init
      return { ok: true, json: async () => ({ issuePreviews: [] }) }
    }))
    await repairBatches.candidates('wf-1', { pageSize: 1, previewSignatures })
    expect(new TextEncoder().encode(requestUrl).length).toBeLessThan(1024)
    expect(requestInit?.method).toBe('POST')
    const body = new Headers(requestInit?.headers).get('Content-Encoding') === 'gzip'
      ? gunzipSync(requestInit?.body as Uint8Array).toString('utf8') : requestInit?.body as string
    expect(JSON.parse(body)).toEqual({ workflowId: 'wf-1', pageSize: 1, previewSignatures })
  })

  it('also aborts a stalled body-bearing preview read', async () => {
    vi.useFakeTimers()
    vi.stubGlobal('fetch', vi.fn((_url: string, init: RequestInit) => new Promise((_resolve, reject) => {
      init.signal?.addEventListener('abort', () => reject(init.signal?.reason))
    })))
    const failed = expect(repairBatches.candidates('wf-1', { previewSignatures: ['issue-a'] })).rejects.toThrow(/读取超时/)
    await vi.advanceTimersByTimeAsync(30_000)
    await failed
    expect(vi.getTimerCount()).toBe(0)
  })

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
