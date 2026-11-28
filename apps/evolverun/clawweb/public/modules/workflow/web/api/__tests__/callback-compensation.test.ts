import { gunzipSync } from 'node:zlib'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { callbackCompensationService as service } from '../callback-compensation'

// Keep fetchJson real: these are display headers, never proof of administrator access.
vi.mock('@avernet/clawweb-shared/web/hooks/useClientUser', () => ({
  getClientUser: () => ({ userId: 'display-only-user', nickName: '显示用户', isAdmin: false }),
}))

const scope = { workflowId: 'wf/a ?#中文', environment: 'PRE' }
const base = `/api/workflows/${encodeURIComponent(scope.workflowId)}/callback-compensations`
const receiver = { configId: 'cfg', name: '通知接收者', receiverId: 'receiver', targetOrigin: 'https://receiver.example' }
const capabilities = {
  ...scope, canPreview: true, executionEnabled: true, blockedReasons: [], configs: [receiver],
}
const preview = {
  ...scope, previewId: 'preview', receiver, expiresAt: '2099-11-28T10:00:00Z',
  items: [{ itemId: 'eligible', flowId: 'run-1', status: 'eligible', reasons: [], payload: { redacted: true } }],
  summary: { eligible: 1, delivered: 0, review: 0, ineligible: 0 },
}
const batch = {
  ...scope, batchId: 'batch/a ?#中文', status: 'running',
  items: [{ itemId: 'eligible', flowId: 'run-1', state: 'queued', auditPending: false }], counts: { queued: 1 },
}
const previewInput = { configId: 'cfg', flowIds: ['run-1'] }
const createInput = {
  previewId: 'preview', itemIds: ['eligible'], reason: '人工核实后只补发成功回调通知', requestKey: 'same-request-key',
}
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), {
  status, headers: { 'Content-Type': 'application/json' },
})
const http = vi.fn<typeof fetch>()
const calls = {
  capabilities: (signal?: AbortSignal) => service.capabilities(scope.workflowId, signal),
  preview: (signal?: AbortSignal) => service.preview(scope, previewInput, signal),
  create: (signal?: AbortSignal) => service.create(scope, createInput, signal),
  batch: (signal?: AbortSignal) => service.batch(scope, batch.batchId, signal),
  stop: (signal?: AbortSignal) => service.stop(scope, batch.batchId, signal),
}

async function rejection(promise: Promise<unknown>) {
  try {
    await promise
  } catch (error) {
    expect(error).toBeInstanceOf(Error)
    return error as Error & { status?: number; data?: unknown }
  }
  throw new Error('Expected callback compensation service to reject')
}
function expectRedacted(error: Error, sensitive: string) {
  const retained = Object.fromEntries(Object.getOwnPropertyNames(error).map((key) => [key, Reflect.get(error, key)]))
  expect(error.message.trim()).not.toBe('')
  expect(JSON.stringify(retained)).not.toContain(sensitive)
  expect(error).not.toHaveProperty('body')
  expect(error).not.toHaveProperty('response')
}

beforeEach(() => { http.mockReset(); vi.stubGlobal('fetch', http) })
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers() })

describe('native callback compensation transport through shared fetchJson', () => {
  it('uses encoded native routes, authenticated cookies, safe display headers and JSON CSRF for every mutation', async () => {
    http.mockResolvedValueOnce(json(capabilities)).mockResolvedValueOnce(json(preview))
      .mockResolvedValueOnce(json(batch, 202)).mockResolvedValueOnce(json(batch))
      .mockResolvedValueOnce(json({ ...batch, status: 'stopped' }))
    const signal = new AbortController().signal
    await expect(calls.capabilities(signal)).resolves.toEqual(capabilities)
    await expect(calls.preview(signal)).resolves.toEqual(preview)
    await expect(calls.create(signal)).resolves.toEqual(batch)
    await expect(calls.batch(signal)).resolves.toEqual(batch)
    await expect(calls.stop(signal)).resolves.toEqual({ ...batch, status: 'stopped' })
    expect(http.mock.calls.map(([url]) => url)).toEqual([
      `${base}/capabilities`, `${base}/preview`, base,
      `${base}/${encodeURIComponent(batch.batchId)}`, `${base}/${encodeURIComponent(batch.batchId)}/stop`,
    ])
    expect(http).toHaveBeenCalledTimes(5)
    http.mock.calls.forEach(([url, init], index) => {
      expect(url).not.toContain('user_id')
      expect(init).toEqual(expect.objectContaining({ credentials: 'include', cache: 'no-store' }))
      expect(init?.signal).toBeDefined()
      expect(init?.signal?.aborted).toBe(false)
      const headers = new Headers(init?.headers)
      expect(headers.get('X-User-Id')).toBe('display-only-user')
      expect(headers.get('X-User-Name')).toBe(encodeURIComponent('显示用户'))
      expect(headers.has('Authorization')).toBe(false)
      expect(headers.has('X-Admin')).toBe(false)
      expect(headers.has('X-Is-Admin')).toBe(false)
      if ([1, 2, 4].includes(index)) {
        expect(init?.method).toBe('POST')
        expect(headers.get('Content-Type')).toBe('application/json')
        expect(headers.get('X-Callback-Compensation-CSRF')).toBe('1')
      } else {
        expect(init?.method ?? 'GET').toBe('GET')
        expect(init?.body).toBeUndefined()
        expect(headers.has('X-Callback-Compensation-CSRF')).toBe(false)
      }
    })
    expect(JSON.parse(http.mock.calls[1][1]?.body as string)).toEqual(previewInput)
    expect(JSON.parse(http.mock.calls[2][1]?.body as string)).toEqual(createInput)
    expect(JSON.parse(http.mock.calls[4][1]?.body as string)).toEqual({})
  })

  it('preserves the explicit absolute time range without adding actor or execution overrides', async () => {
    http.mockResolvedValue(json(preview))
    const input = { configId: 'cfg', from: '2026-11-27T00:00:00+08:00', to: '2026-11-28T00:00:00+08:00' }
    await service.preview(scope, input)
    expect(JSON.parse(http.mock.calls[0][1]?.body as string)).toEqual(input)
    expect(http).toHaveBeenCalledOnce()
  })

  it.each(Object.keys(calls) as Array<keyof typeof calls>)('propagates cancellation to fetch for %s (including wrapped timeout signals)', async (method) => {
    http.mockImplementation((_url, init) => new Promise<Response>((_resolve, reject) => {
      const abort = () => reject(new DOMException('Aborted', 'AbortError'))
      if (init?.signal?.aborted) abort()
      else init?.signal?.addEventListener('abort', abort, { once: true })
    }))
    const controller = new AbortController()
    const pending = rejection(calls[method](controller.signal))
    await vi.waitFor(() => expect(http).toHaveBeenCalledOnce())
    const forwarded = http.mock.calls[0][1]?.signal
    expect(forwarded).toBeDefined()
    expect(forwarded?.aborted).toBe(false)
    controller.abort()
    expect(forwarded?.aborted).toBe(true)
    await pending
    expect(http).toHaveBeenCalledOnce()
  })

  it.each(Object.keys(calls) as Array<keyof typeof calls>)('retains only safe status and code on %s HTTP failure', async (method) => {
    const sensitive = 'test-token'
    http.mockResolvedValue(json({ code: 'EXECUTION_DISABLED', message: sensitive, body: sensitive, config: { secret: sensitive } }, 409))
    const error = await rejection(calls[method]())
    expect(error.status).toBe(409)
    expect(error.data).toEqual({ code: 'EXECUTION_DISABLED' })
    expectRedacted(error, sensitive)
    expect(http).toHaveBeenCalledOnce()
  })

  it.each([403, 503])('redacts an unstructured %s response rather than retaining the shared ApiError body', async (status) => {
    const sensitive = 'private-upstream-html-and-credentials'
    http.mockResolvedValue(new Response(`<html>${sensitive}</html>`, { status }))
    const error = await rejection(calls.capabilities())
    expect(error.status).toBe(status)
    expectRedacted(error, sensitive)
    if (error.data !== undefined) expect(Object.keys(error.data as object).every((key) => key === 'code')).toBe(true)
    expect(http).toHaveBeenCalledOnce()
  })

  it.each(['preview', 'create', 'stop'] as const)('never retries %s after an uncertain network failure', async (method) => {
    const sensitive = 'receiver-url-with-secret-in-network-error'
    http.mockRejectedValue(new Error(sensitive))
    expectRedacted(await rejection(calls[method]()), sensitive)
    expect(http).toHaveBeenCalledOnce()
  })

  it('preserves JSON and CSRF when native fetchJson compresses a large legitimate run-ID request', async () => {
    const input = { configId: 'cfg', flowIds: Array.from({ length: 100 }, (_, index) => `run-${index}-${'a'.repeat(160)}`) }
    http.mockResolvedValue(json(preview))
    await service.preview(scope, input)
    const init = http.mock.calls[0][1]!
    const headers = new Headers(init.headers)
    expect(headers.get('Content-Type')).toBe('application/json')
    expect(headers.get('Content-Encoding')).toBe('gzip')
    expect(headers.get('X-Callback-Compensation-CSRF')).toBe('1')
    expect(init.credentials).toBe('include')
    expect(JSON.parse(gunzipSync(init.body as Uint8Array).toString('utf8'))).toEqual(input)
    expect(http).toHaveBeenCalledOnce()
  })

  it('bounds a hanging create without retrying or leaking timeout details', async () => {
    vi.useFakeTimers()
    http.mockImplementation((_url, init) => new Promise<Response>((_resolve, reject) => {
      init?.signal?.addEventListener('abort', () => reject(new Error('private-timeout-detail')), { once: true })
    }))
    const pending = rejection(calls.create())
    expect(http).toHaveBeenCalledOnce()
    await vi.advanceTimersByTimeAsync(30_000)
    expect(http.mock.calls[0][1]?.signal?.aborted).toBe(true)
    expectRedacted(await pending, 'private-timeout-detail')
    await vi.advanceTimersByTimeAsync(60_000)
    expect(http).toHaveBeenCalledOnce()
  })

  it('loads safe receiver choices only from capabilities and drops extra secret config fields', async () => {
    const sensitive = 'test-token'
    http.mockResolvedValue(json({ ...capabilities, configs: [{ ...receiver, secret: sensitive, url: 'https://receiver.example/path?token=test-token' }] }))
    const result = await calls.capabilities()
    expect(result.configs).toEqual([receiver])
    expect(result.configs[0]).not.toHaveProperty('secret')
    expect(result.configs[0]).not.toHaveProperty('url')
    expect(JSON.stringify(result)).not.toContain(sensitive)
    expect(http.mock.calls.map(([url]) => url)).toEqual([`${base}/capabilities`])
  })
})

describe('callback compensation response scope and protocol validation', () => {
  it.each([
    ['capabilities workflow', 'capabilities', { ...capabilities, workflowId: 'another-workflow' }],
    ['capabilities preview authority', 'capabilities', { ...capabilities, canPreview: false }],
    ['capabilities config uniqueness', 'capabilities', { ...capabilities, configs: [receiver, receiver] }],
    ['preview workflow', 'preview', { ...preview, workflowId: 'another-workflow' }],
    ['preview environment', 'preview', { ...preview, environment: 'PROD' }],
    ['preview selected config', 'preview', { ...preview, receiver: { ...receiver, configId: 'other-config' } }],
    ['preview expiry', 'preview', { ...preview, expiresAt: 'not-a-date' }],
    ['preview eligibility', 'preview', { ...preview, items: [{ ...preview.items[0], status: 'force-resend' }] }],
    ['create workflow', 'create', { ...batch, workflowId: 'another-workflow' }],
    ['create environment', 'create', { ...batch, environment: 'PROD' }],
    ['batch ID', 'batch', { ...batch, batchId: 'other-batch' }],
    ['batch environment', 'batch', { ...batch, environment: 'PROD' }],
    ['batch state', 'batch', { ...batch, items: [{ ...batch.items[0], state: 'retry' }] }],
    ['batch duplicate items', 'batch', { ...batch, items: [batch.items[0], batch.items[0]] }],
    ['stop batch ID', 'stop', { ...batch, batchId: 'other-batch' }],
    ['stop workflow', 'stop', { ...batch, workflowId: 'another-workflow' }],
    ['stop environment', 'stop', { ...batch, environment: 'PROD' }],
  ] as const)('rejects mismatched %s instead of admitting it into native UI state', async (_label, method, body) => {
    http.mockResolvedValue(json(body))
    await expect(calls[method]()).rejects.toBeInstanceOf(Error)
    expect(http).toHaveBeenCalledOnce()
  })

  it('redacts malformed JSON errors without retaining response text', async () => {
    const sensitive = 'upstream-secret-in-invalid-json'
    http.mockResolvedValue(new Response(`{"token":"${sensitive}`, { headers: { 'Content-Type': 'application/json' } }))
    expectRedacted(await rejection(calls.capabilities()), sensitive)
    expect(http).toHaveBeenCalledOnce()
  })
})
