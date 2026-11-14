import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { gunzipSync } from 'node:zlib'
import { workflowAiApi } from '../workflow-ai'

vi.mock('@avernet/clawweb-shared/web/hooks/useClientUser', () => ({ getClientUser: () => ({ userId: 'test-user', nickName: '测试用户' }) }))
const http = vi.fn()
const signal = new AbortController().signal
const input = { requestId: 'b1d4c678-2233-4455-8899-aabbccddeeff', mode: 'modify' as const, prompt: '  原始需求\n', currentYaml: 'id: flow/name\n' }
beforeEach(() => { http.mockReset(); http.mockImplementation(async () => new Response(JSON.stringify({ status: 'pending', draftId: 'draft/id' }))); vi.stubGlobal('fetch', http) })
afterEach(() => vi.unstubAllGlobals())

describe('dedicated workflow AI draft wire', () => {
  it('keeps the unchanged ai-drafts wire and preserves raw input, CSRF auth and signals', async () => {
    await workflowAiApi.capabilities('flow/name', signal)
    await workflowAiApi.generate('flow/name', input, signal)
    await workflowAiApi.poll('flow/name', 'draft/id', signal)
    const cancelSignal = new AbortController().signal
    await workflowAiApi.cancel('flow/name', 'draft/id', cancelSignal)
    expect(http.mock.calls.map(([url]) => url)).toEqual([
      '/api/workflows/flow%2Fname/ai-drafts/capabilities', '/api/workflows/flow%2Fname/ai-drafts',
      '/api/workflows/flow%2Fname/ai-drafts/draft%2Fid', '/api/workflows/flow%2Fname/ai-drafts/draft%2Fid/cancel',
    ])
    const body = JSON.parse(http.mock.calls[1][1].body)
    expect(body).toEqual(input)
    expect(Object.keys(body)).toEqual(['requestId', 'mode', 'prompt', 'currentYaml'])
    expect(http.mock.calls[1][1].method).toBe('POST')
    expect(http.mock.calls[3][1].method).toBe('POST')
    http.mock.calls.forEach(([, init], index) => {
      expect(init.signal).toBe(index === 3 ? cancelSignal : signal)
      expect(init.headers['X-Pack-Version-CSRF']).toBe(index === 1 || index === 3 ? '1' : undefined)
      expect(init.headers['X-User-Id']).toBe('test-user')
      expect(init.headers['X-User-Name']).toBe(encodeURIComponent('测试用户'))
      expect(init.cache).toBe('no-store')
    })
  })

  it('retains shared gzip for full source snapshots', async () => {
    const large = { ...input, currentYaml: `id: flow\nprompt: ${'汉'.repeat(20_000)}` }
    await workflowAiApi.generate('flow', large, signal)
    const init = http.mock.calls[0][1]
    expect(init.headers['Content-Encoding']).toBe('gzip')
    expect(init.headers['X-Pack-Version-CSRF']).toBe('1')
    expect(JSON.parse(gunzipSync(init.body).toString('utf8'))).toEqual(large)
  })

  it('retains server normalization validation and propagates HTTP failures', async () => {
    const spec = { id: 'flow', title: 'Flow', version: '1', nodes: [] }
    await workflowAiApi.validate(spec, signal)
    expect(http.mock.calls[0][0]).toBe('/api/workflows/validate')
    expect(http.mock.calls[0][1].headers['X-Pack-Version-CSRF']).toBeUndefined()
    expect(JSON.parse(http.mock.calls[0][1].body)).toEqual({ spec })
    http.mockResolvedValueOnce(new Response('service unavailable', { status: 503 }))
    await expect(workflowAiApi.capabilities('flow', signal)).rejects.toThrow('service unavailable')
  })
})
