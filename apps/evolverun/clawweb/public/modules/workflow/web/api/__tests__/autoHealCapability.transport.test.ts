import { afterEach, describe, expect, it, vi } from 'vitest'
import { AutoHealScopedCapabilityError, readAutoHealUnifiedCapability } from '../autoHealUnifiedBaseline'

const capabilityUrl = '/api/auto-heal/capabilities'
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), {
  status, headers: { 'Content-Type': 'application/json' },
})

afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers() })

describe('AutoHeal capability transport evidence', () => {
  it.each([
    ['v1', true],
    [false, false],
    [null, false],
  ])('accepts the explicit %s capability declaration', async (version, expected) => {
    const fetchMock = vi.fn().mockResolvedValue(json({ autoHealUnifiedSave: version }))
    vi.stubGlobal('fetch', fetchMock)
    await expect(readAutoHealUnifiedCapability()).resolves.toBe(expected)
    expect(fetchMock).toHaveBeenCalledOnce()
    expect(fetchMock.mock.calls[0][0]).toBe(capabilityUrl)
  })

  it.each([
    ['generic route 404', () => json({ error: 'Not Found' }, 404)],
    ['business 404', () => json({ code: 'NOT_FOUND' }, 404)],
    ['authentication failure', () => json({ code: 'AUTHENTICATION_REQUIRED' }, 401)],
    ['server failure', () => json({ code: 'CAPABILITIES_UNAVAILABLE' }, 500)],
    ['invalid JSON', () => new Response('{', { status: 200 })],
  ])('does not infer disabled capability from %s', async (_label, response) => {
    const fetchMock = vi.fn().mockResolvedValue(response())
    vi.stubGlobal('fetch', fetchMock)
    await expect(readAutoHealUnifiedCapability()).rejects.toThrow('无法确认宿主保存能力')
    expect(fetchMock).toHaveBeenCalledOnce()
    expect(fetchMock.mock.calls[0][0]).toBe(capabilityUrl)
  })

  it('does not treat a network exception bearing a 404 status as a response', async () => {
    const fetchMock = vi.fn().mockRejectedValue(Object.assign(new Error('network failure'), {
      status: 404, body: JSON.stringify({ error: 'Not Found' }),
    }))
    vi.stubGlobal('fetch', fetchMock)
    await expect(readAutoHealUnifiedCapability()).rejects.toThrow('无法确认宿主保存能力')
    expect(fetchMock).toHaveBeenCalledOnce()
    expect(fetchMock.mock.calls[0][0]).toBe(capabilityUrl)
  })
})

describe('workflow-scoped host capability', () => {
  it.each([['v1', true], [false, false]])('returns %s only after a scoped read', async (mode, expected) => {
    const fetchMock = vi.fn().mockResolvedValueOnce(json({ autoHealUnifiedSave: 'v1-workflow' })).mockResolvedValueOnce(json({ autoHealUnifiedSave: mode }))
    vi.stubGlobal('fetch', fetchMock)
    await expect(readAutoHealUnifiedCapability('flow/name')).resolves.toBe(expected)
    expect(fetchMock.mock.calls[1][0]).toBe('/api/auto-heal/capabilities?workflowId=flow%2Fname')
  })
  it('never infers legacy on scoped read failure or invalid response', async () => {
    for (const reply of [() => json({}, 200), () => json({ code: 'CAPABILITIES_UNAVAILABLE' }, 503), () => { throw new Error('offline') }]) {
      const fetchMock = vi.fn().mockResolvedValueOnce(json({ autoHealUnifiedSave: 'v1-workflow' })).mockImplementationOnce(reply)
      vi.stubGlobal('fetch', fetchMock)
      await expect(readAutoHealUnifiedCapability('flow')).rejects.toBeInstanceOf(AutoHealScopedCapabilityError)
    }
  })
})

it('exposes an explicit missing-baseline state rather than selecting legacy', async () => {
  const fetchMock = vi.fn().mockResolvedValueOnce(json({ autoHealUnifiedSave: 'v1-workflow' })).mockResolvedValueOnce(json({ autoHealUnifiedSave: 'baseline-unavailable' }))
  vi.stubGlobal('fetch', fetchMock)
  await expect(readAutoHealUnifiedCapability('flow')).rejects.toThrow('缺少数据库草稿或草稿(wf)或生效 tag 基线')
})

it('treats a scoped capability timeout as a non-downgradable failure', async () => {
  vi.useFakeTimers()
  vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(json({ autoHealUnifiedSave: 'v1-workflow' })).mockImplementationOnce(() => new Promise(() => {})))
  const result = readAutoHealUnifiedCapability('flow')
  const assertion = expect(result).rejects.toBeInstanceOf(AutoHealScopedCapabilityError)
  await vi.advanceTimersByTimeAsync(10_001)
  await assertion
})
