import { act, cleanup, renderHook } from '@testing-library/react'
import { createHash, webcrypto } from 'node:crypto'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { stringify } from 'yaml'
import type { WorkflowSpec } from '@avernet/clawweb-shared/web/types'
import { useWorkflowAiDraft } from '../useWorkflowAiDraft'

// Exercise the real hook, service, scoped API and fetchJson; only the HTTP boundary is fake.
vi.mock('@avernet/clawweb-shared/web/hooks/useClientUser', () => ({ getClientUser: () => ({ userId: 'test-user', nickName: 'Test' }) }))
const source: WorkflowSpec = { id: 'validation-recovery', title: 'Source', version: '1', nodes: [{ id: 'done', title: 'Done', executor: { type: 'done' } }] }
const candidate: WorkflowSpec = { ...source, title: 'AI draft' }
const draftsPath = `/api/workflows/${source.id}/ai-drafts`
const draftId = 'completed-draft'
const taskPath = `${draftsPath}/${draftId}`
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status })
const http = vi.fn()
let currentYaml: string
let validations: number
let capabilityReads: number

beforeEach(() => {
  http.mockReset(); currentYaml = ''; validations = 0; capabilityReads = 0
  vi.stubGlobal('crypto', webcrypto)
  vi.stubGlobal('fetch', http)
})
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

type TransportFailure =
  | { kind: 'validation'; failure: 'disconnect' | '503' | 'auth' | 'rate-limit' | 'invalid-yaml' | 'validation-rejection' | 'invalid-normalized-spec' | '422'; status?: 401 | 403 | 429 }
  | { kind: 'submission'; failure: 'lost-ack' | 'capability-transport' | 'capability-unavailable' }

/** The hook, service and `workflowAiApi` remain real; only the HTTP boundary is synthetic. */
function configureTransport(value: TransportFailure) {
  http.mockImplementation(async (url: string, init: RequestInit) => {
    if (url === `${draftsPath}/capabilities`) {
      capabilityReads += 1
      if (value.kind === 'submission' && value.failure === 'capability-transport' && capabilityReads === 2) throw new TypeError('capability transport failed')
      if (value.kind === 'submission' && value.failure === 'capability-unavailable' && capabilityReads === 2) return json({ available: false, reason: 'AUTO_HEAL_BOT_UNAVAILABLE' })
      return json({ available: true })
    }
    if (url === draftsPath && init.method === 'POST') {
      currentYaml = JSON.parse(String(init.body)).currentYaml
      if (value.kind === 'submission' && submissions().length === 1) throw new TypeError('lost submission acknowledgment')
      return json({ draftId, status: 'pending' })
    }
    if (url === taskPath) return json({
      draftId, workflowId: source.id, status: 'completed',
      inputSpecDigest: createHash('sha256').update(currentYaml, 'utf8').digest('hex'),
      result: { yaml: value.kind === 'validation' && value.failure === 'invalid-yaml' ? 'null' : stringify(candidate), summary: 'Completed draft' },
    })
    if (url === '/api/workflows/validate' && init.method === 'POST') {
      validations += 1
      const first = validations === 1
      if (value.kind === 'validation') {
        if (first && value.failure === 'disconnect') throw new TypeError('validation disconnected')
        if (first && value.failure === '503') return json({ error: 'temporarily unavailable' }, 503)
        if (first && (value.failure === 'auth' || value.failure === 'rate-limit')) return json({ error: value.failure }, value.status)
        if (value.failure === '422') return json({ error: 'Invalid candidate' }, 422)
        if (value.failure === 'validation-rejection') return json({ valid: false, normalizedSpec: null, issues: [{ path: 'nodes', message: 'Invalid configuration', severity: 'error' }] })
      }
      return json({ valid: true, issues: [], normalizedSpec: value.kind === 'validation' && value.failure === 'invalid-normalized-spec' ? { ...candidate, id: 'other' } : candidate })
    }
    throw new Error(`Unexpected HTTP request: ${init.method ?? 'GET'} ${url}`)
  })
}

async function start() {
  const hook = renderHook(() => useWorkflowAiDraft(true, 'modify', source))
  act(() => hook.result.current.changePrompt('  原始需求\n'))
  await act(async () => { await hook.result.current.generate() })
  return hook
}

const submissions = () => http.mock.calls.filter(([url, init]) => url === draftsPath && init.method === 'POST')

describe('real hook/service duplicate-BaaS recovery', () => {
  it.each(['capability-transport', 'capability-unavailable'] as const)('keeps the same submitted requestId when interrupted recovery fails readiness with %s', async failure => {
    configureTransport({ kind: 'submission', failure })
    const hook = renderHook(() => useWorkflowAiDraft(true, 'modify', source))
    act(() => hook.result.current.changePrompt('  原始需求\n'))

    await act(async () => { await hook.result.current.generate() })
    expect(hook.result.current.recoverable).toBe(true)
    expect(submissions()).toHaveLength(1)
    const first = String(submissions()[0][1].body)
    const firstBody = JSON.parse(first)
    expect(Object.keys(firstBody)).toEqual(['requestId', 'mode', 'prompt', 'currentYaml'])

    await act(async () => { await hook.result.current.generate() })
    expect(hook.result.current.recoverable).toBe(true)
    expect(hook.result.current.error).toContain('AI 草稿')
    expect(submissions()).toHaveLength(1)

    await act(async () => { await hook.result.current.generate() })
    expect(hook.result.current.recoverable).toBe(false)
    expect(hook.result.current.result?.draft.spec).toEqual(candidate)
    expect(submissions()).toHaveLength(2)
    const secondBody = JSON.parse(String(submissions()[1][1].body))
    expect(secondBody).toEqual(firstBody)
    expect(secondBody.requestId).toBe(firstBody.requestId)
    expect(secondBody.prompt).toBe('  原始需求\n')
  })

  it.each([
    { failure: 'auth', status: 401 } as const,
    { failure: 'auth', status: 403 } as const,
    { failure: 'rate-limit', status: 429 } as const,
  ])('keeps a completed draft when validation returns HTTP $status, with exactly one generation POST', async value => {
    configureTransport({ kind: 'validation', failure: value.failure, status: value.status })
    const { result } = await start()
    expect(result.current.recoverable).toBe(true)
    expect(result.current.error).toContain('校验')
    expect(submissions()).toHaveLength(1)
    const originalRequestId = JSON.parse(String(submissions()[0][1].body)).requestId

    await act(async () => { await result.current.generate() })
    expect(result.current.recoverable).toBe(false)
    expect(result.current.error).toBeNull()
    expect(result.current.result?.draft.spec).toEqual(candidate)
    expect(submissions()).toHaveLength(1)
    expect(JSON.parse(String(submissions()[0][1].body)).requestId).toBe(originalRequestId)
    expect(validations).toBe(2)
    expect(http.mock.calls.map(([url]) => url)).toEqual([
      `${draftsPath}/capabilities`, draftsPath, taskPath, '/api/workflows/validate', taskPath, '/api/workflows/validate',
    ])
  })

  it.each(['disconnect', '503'] as const)('preserves a completed draft after %s, then recovers without another generation POST', async failure => {
    configureTransport({ kind: 'validation', failure })
    const { result } = await start()
    expect(result.current.recoverable).toBe(true)
    expect(result.current.busy).toBe(false)
    expect(result.current.result).toBeNull()
    expect(result.current.error).toContain('校验')
    expect(submissions()).toHaveLength(1)
    const originalSubmission = JSON.parse(String(submissions()[0][1].body))
    expect(originalSubmission.prompt).toBe('  原始需求\n')

    await act(async () => { await result.current.generate() })
    expect(result.current.recoverable).toBe(false)
    expect(result.current.error).toBeNull()
    expect(result.current.result?.draft.spec).toEqual(candidate)
    expect(result.current.result?.baseSpec).toEqual(source)
    expect(submissions()).toHaveLength(1)
    expect(JSON.parse(String(submissions()[0][1].body)).requestId).toBe(originalSubmission.requestId)
    expect(http.mock.calls.map(([url]) => url)).toEqual([
      `${draftsPath}/capabilities`, draftsPath, taskPath, '/api/workflows/validate', taskPath, '/api/workflows/validate',
    ])
    expect(validations).toBe(2)
    expect(source.title).toBe('Source')
  })

  it.each(['invalid-yaml', 'validation-rejection', 'invalid-normalized-spec'] as const)('does not offer transport recovery for deterministic %s', async failure => {
    configureTransport({ kind: 'validation', failure })
    const { result } = await start()
    expect(result.current.recoverable).toBe(false)
    expect(result.current.result).toBeNull()
    expect(result.current.error).not.toBeNull()
    expect(result.current.busy).toBe(false)
    expect(submissions()).toHaveLength(1)
    expect(validations).toBe(failure === 'invalid-yaml' ? 0 : 1)
  })
})
