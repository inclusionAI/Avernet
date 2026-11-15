// @vitest-environment node
import express from 'express'
import { once } from 'node:events'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { WorkflowAiDraftService } from '../../../server/contracts/workflow-ai-draft'
import { createWorkflowAiDraftRouter } from '../../../server/routes/workflow-ai-drafts'
import { fetchJson } from '@avernet/clawweb-shared/web/api/client'
import { workflowAiApi } from '../workflow-ai'

// Keep the actual API, fetchJson and backend router/CSRF guard. Only the model service
// is synthetic; fetch relays real HTTP to a per-test loopback Express listener.
vi.mock('@avernet/clawweb-shared/web/hooks/useClientUser', () => ({ getClientUser: () => ({ userId: 'test-user', nickName: 'Test User' }) }))
const workflowId = 'csrf-wire'
const draftsPath = `/api/workflows/${workflowId}/ai-drafts`
const input = { requestId: 'b1d4c678-2233-4455-8899-aabbccddeeff', mode: 'modify' as const, prompt: '原始需求', currentYaml: 'id: csrf-wire\n' }
const task = { draftId: 'test-draft', workflowId, status: 'running' as const, inputSpecDigest: 'a'.repeat(64) }
const service: WorkflowAiDraftService = {
  capabilities: vi.fn(async () => ({ available: true })),
  create: vi.fn(async () => ({ draftId: task.draftId, status: 'pending' as const })),
  get: vi.fn(async () => task),
  cancel: vi.fn(async () => ({ ...task, status: 'cancelled' as const })),
  reconcile: vi.fn(async () => 0),
}
let server: ReturnType<express.Application['listen']>
let statuses: number[]
let received: { path: string; csrf: string | undefined }[]

beforeEach(async () => {
  vi.clearAllMocks(); statuses = []; received = []
  const app = express()
  app.use(express.json())
  app.use((request, _response, next) => {
    received.push({ path: request.path, csrf: request.get('x-pack-version-csrf') })
    next()
  })
  app.use('/api/workflows', createWorkflowAiDraftRouter({
    service, principal: async request => ({ actorId: request.get('X-User-Id') ?? '' }),
  }))
  app.post('/api/workflows/validate', (request, response) => response.json({ valid: true, issues: [], normalizedSpec: request.body.spec }))
  server = app.listen(0, '127.0.0.1')
  await once(server, 'listening')
  const address = server.address()
  if (!address || typeof address === 'string') throw new Error('Loopback test listener unavailable')
  const nativeFetch = globalThis.fetch
  const base = `http://127.0.0.1:${address.port}`
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    const response = await nativeFetch(`${base}${url}`, init)
    statuses.push(response.status)
    return response
  })
})
afterEach(async () => {
  vi.unstubAllGlobals()
  if (server) await new Promise<void>((resolve, reject) => server.close(error => error ? reject(error) : resolve()))
})

describe('real workflow AI API -> Express CSRF contract', () => {
  it('submits and cancels with the marker required by the unchanged router guard', async () => {
    const signal = new AbortController().signal
    const submitted = await workflowAiApi.generate(workflowId, input, signal)
    expect(submitted).toEqual({ draftId: task.draftId, status: 'pending' })
    const cancelled = await workflowAiApi.cancel(workflowId, submitted.draftId, signal)
    expect(cancelled.status).toBe('cancelled')
    expect(statuses).toEqual([202, 200])
    expect(received).toEqual([
      { path: draftsPath, csrf: '1' }, { path: `${draftsPath}/${task.draftId}/cancel`, csrf: '1' },
    ])
    expect(service.create).toHaveBeenCalledExactlyOnceWith('test-user', workflowId, input)
    expect(service.cancel).toHaveBeenCalledExactlyOnceWith('test-user', workflowId, task.draftId)
  })

  it('still rejects both POSTs without the CSRF marker before calling the service', async () => {
    await expect(fetchJson(draftsPath, { method: 'POST', body: JSON.stringify(input) })).rejects.toThrow('CSRF_REJECTED')
    await expect(fetchJson(`${draftsPath}/${task.draftId}/cancel`, { method: 'POST' })).rejects.toThrow('CSRF_REJECTED')
    expect(statuses).toEqual([403, 403])
    expect(service.create).not.toHaveBeenCalled()
    expect(service.cancel).not.toHaveBeenCalled()
  })

  it('leaves capabilities/poll/validation free of a new CSRF header requirement', async () => {
    const signal = new AbortController().signal
    await expect(workflowAiApi.capabilities(workflowId, signal)).resolves.toEqual({ available: true })
    await expect(workflowAiApi.poll(workflowId, task.draftId, signal)).resolves.toEqual(task)
    const spec = { id: workflowId, title: 'Flow', version: '1', nodes: [] }
    await expect(workflowAiApi.validate(spec, signal)).resolves.toEqual({ valid: true, issues: [], normalizedSpec: spec })
    expect(statuses).toEqual([200, 200, 200])
    expect(received.every(request => request.csrf === undefined)).toBe(true)
  })
})
