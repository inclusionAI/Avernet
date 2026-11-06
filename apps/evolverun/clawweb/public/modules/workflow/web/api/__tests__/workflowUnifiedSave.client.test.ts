import { afterEach, expect, it, vi } from 'vitest'
import { readUnifiedSaveCapability, serializeUnifiedSpec, UNIFIED_SAVE_TIMEOUT_MS, workflowUnifiedSave } from '../workflowUnifiedSave'

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals() })
it('times out even if the transport ignores abort and never retries', async () => {
  vi.useFakeTimers()
  const fetch = vi.fn(() => new Promise(() => {}))
  vi.stubGlobal('fetch', fetch)
  const result = workflowUnifiedSave('wf', { requestId: 'request_123', sourceVersion: 'draft', sourceCommit: 'a'.repeat(40),
    expectedSavedHead: 'a'.repeat(40), expectedDraftToken: 'b'.repeat(64), specJson: '{}', changes: [] })
  const failure = expect(result).rejects.toThrow('超时')
  await vi.advanceTimersByTimeAsync(UNIFIED_SAVE_TIMEOUT_MS)
  await failure
  expect(fetch).toHaveBeenCalledTimes(1)
})
it.each([undefined, NaN, Infinity, -0, Number.MAX_SAFE_INTEGER + 1, [,,1], { value: undefined }])('rejects values JSON would discard or change', value => {
  expect(() => serializeUnifiedSpec(value)).toThrow()
})

it.each([401, 404])('treats GET %s as unavailable without issuing a POST', async status => {
  const fetch = vi.fn().mockResolvedValue(new Response('{}', { status }))
  vi.stubGlobal('fetch', fetch)
  await expect(readUnifiedSaveCapability('wf')).resolves.toBe('unavailable')
  expect(fetch).toHaveBeenCalledTimes(1)
  expect(fetch.mock.calls[0][1]).toMatchObject({ method: 'GET', cache: 'no-store' })
})
it.each([{ protocol: 'workflow-unified-save-v0' }, { protocol: 'workflow-unified-save-v1', extra: true }, {}, null])('rejects an invalid capability document %o', async body => {
  const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify(body)))
  vi.stubGlobal('fetch', fetch)
  await expect(readUnifiedSaveCapability('wf')).rejects.toThrow('能力响应无效')
  expect(fetch).toHaveBeenCalledTimes(1)
})
it('accepts only the exact negotiated protocol and still never sends a POST', async () => {
  const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ protocol: 'workflow-unified-save-v1' })))
  vi.stubGlobal('fetch', fetch)
  await expect(readUnifiedSaveCapability('wf')).resolves.toBe('available')
  expect(fetch).toHaveBeenCalledTimes(1)
  expect(fetch.mock.calls[0][1].method).toBe('GET')
})
