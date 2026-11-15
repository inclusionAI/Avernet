import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, renderHook } from '@testing-library/react'
import type { PropsWithChildren } from 'react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { readAutoHealUnifiedBaseline, confirmAutoHealUnifiedReview } from '../autoHealUnifiedBaseline'
import { useAutoHealSave, useAutoHealDeploy, bindAutoHealDeployToSave } from '../autoHealWrites'
import { beginEditorWorkflowWrite, editorWorkflowWriteState, finishEditorWorkflowWrite } from '../../editor/workflowWriteGuard'

let id = '', sequence = 0
const oid = 'a'.repeat(40), token = 'b'.repeat(64), nextToken = 'c'.repeat(64), hash = 'd'.repeat(64)
const requestId = 'request_12345'
const mock = vi.fn<typeof fetch>()
let client: QueryClient
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } })
function wrapper({ children }: PropsWithChildren) { return <QueryClientProvider client={client}>{children}</QueryClientProvider> }
function setupReads() {
  mock.mockImplementation(async url => {
    if (url === '/api/auto-heal/capabilities') return json({ autoHealUnifiedSave: 'v1' })
    if (String(url).endsWith('/editor-draft')) return json({ workflowId: id, packId: 'pack', specJson: JSON.stringify({ id, nodes: [] }), draftToken: token })
    if (String(url).endsWith('/pack-version?version=draft')) return json({ workflowId: id, packId: 'pack', baseRef: `origin/wf/${id}`, baseCommit: oid, draftHead: oid, versionFiles: [] })
    throw new Error('unexpected request')
  })
}
const spec = () => ({ id, title: 'test', nodes: [] })
const receipt = () => ({ requestId, outcome: 'confirmed', reconciliationRequired: false, evidence: { db: 'confirmed', git: 'confirmed', inputHash: hash, commit: oid, draftToken: nextToken } })
const bodyText = (body: Blob) => new Promise<string>(resolve => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result)); reader.readAsText(body) })
beforeEach(() => { id = `unified-${++sequence}`; sessionStorage.clear(); client = new QueryClient({ defaultOptions: { mutations: { retry: false } } }); mock.mockReset(); setupReads(); vi.stubGlobal('fetch', mock) })
afterEach(() => { cleanup(); client.clear(); vi.unstubAllGlobals() })

it('negotiates v1, reads real baselines and sends the exact unified envelope', async () => {
  const baseline = await readAutoHealUnifiedBaseline(id)
  confirmAutoHealUnifiedReview(baseline, spec())
  mock.mockImplementation(async url => url === '/api/auto-heal/unified-save' ? json(receipt()) : Promise.reject(new Error('unexpected')))
  const { result } = renderHook(useAutoHealSave, { wrapper })
  let value: any
  await act(async () => { value = await result.current.mutateAsync({ workflowId: id, spec: spec(), requestId, unifiedBaseline: baseline }) })
  expect(value).toMatchObject({ saved: true, workflowId: id, packId: 'pack', unified: true, commit: oid, draftToken: nextToken })
  expect(() => bindAutoHealDeployToSave({ workflowId: id, packId: 'pack' }, value)).toThrow('与已保存快照或审阅基线不匹配')
  const [, init] = mock.mock.calls.at(-1)!
  expect(JSON.parse(await bodyText(init!.body as Blob))).toEqual({ requestId, workflowId: id, packId: 'pack', sourceVersion: 'draft', sourceCommit: oid, expectedSavedHead: oid, expectedDraftToken: token, specJson: JSON.stringify(spec()) })
  expect(editorWorkflowWriteState(id).phase).toBe('clear')
})

it('rejects unavailable capability before sending a unified or legacy write', async () => {
  mock.mockResolvedValueOnce(json({ code: 'NOT_FOUND' }, 404))
  await expect(readAutoHealUnifiedBaseline(id)).rejects.toThrow('无法确认宿主保存能力')
  expect(mock).toHaveBeenCalledTimes(1)
})

it('does not accept fabricated or stale read evidence', async () => {
  const baseline = await readAutoHealUnifiedBaseline(id)
  confirmAutoHealUnifiedReview(baseline, spec())
  const { result } = renderHook(useAutoHealSave, { wrapper })
  const count = mock.mock.calls.length
  await act(async () => { await expect(result.current.mutateAsync({ workflowId: id, spec: spec(), requestId, unifiedBaseline: { ...baseline } })).rejects.toThrow('可信') })
  const owner = beginEditorWorkflowWrite(id); finishEditorWorkflowWrite(id, owner)
  await act(async () => { await expect(result.current.mutateAsync({ workflowId: id, spec: spec(), requestId, unifiedBaseline: baseline })).rejects.toThrow('可信') })
  expect(mock).toHaveBeenCalledTimes(count)
})

it('refuses missing authorization without a legacy fallback', async () => {
  const baseline = await readAutoHealUnifiedBaseline(id)
  confirmAutoHealUnifiedReview(baseline, spec())
  mock.mockImplementation(async () => json({ code: 'UNIFIED_SAVE_UNAVAILABLE' }, 503))
  const { result } = renderHook(useAutoHealSave, { wrapper })
  await act(async () => { await expect(result.current.mutateAsync({ workflowId: id, spec: spec(), requestId, unifiedBaseline: baseline })).rejects.toThrow('未自动降级') })
  expect(mock.mock.calls.filter(([url]) => url === '/api/auto-heal/save')).toHaveLength(0)
  expect(editorWorkflowWriteState(id).phase).toBe('clear')
})

it.each([
  { requestId, outcome: 'partial', reconciliationRequired: true, evidence: { db: 'confirmed', git: 'not_started', inputHash: hash, commit: oid, draftToken: nextToken } },
  { ...receipt(), requestId: 'other_request' },
  { ...receipt(), evidence: { db: 'confirmed', git: 'confirmed' } },
])('locks after a partial or uncorrelated response', async response => {
  const baseline = await readAutoHealUnifiedBaseline(id)
  confirmAutoHealUnifiedReview(baseline, spec())
  mock.mockImplementation(async () => json(response, response.outcome === 'partial' ? 409 : 200))
  const { result } = renderHook(useAutoHealSave, { wrapper })
  await act(async () => { await expect(result.current.mutateAsync({ workflowId: id, spec: spec(), requestId, unifiedBaseline: baseline })).rejects.toThrow('人工核对') })
  expect(editorWorkflowWriteState(id).phase).toBe('unknown')
})

it('rejects inconsistent Pack identity and a missing saved-ref baseline', async () => {
  mock.mockImplementation(async url => {
    if (url === '/api/auto-heal/capabilities') return json({ autoHealUnifiedSave: 'v1' })
    if (String(url).endsWith('/editor-draft')) return json({ workflowId: id, packId: 'different-pack', specJson: JSON.stringify({ id, nodes: [] }), draftToken: token })
    return json({ workflowId: id, packId: 'pack', baseRef: `origin/wf/${id}`, baseCommit: oid, draftHead: oid, versionFiles: [] })
  })
  await expect(readAutoHealUnifiedBaseline(id)).rejects.toThrow('基线不一致')
  expect(mock.mock.calls.every(([, init]) => !init?.method || init.method === 'GET')).toBe(true)
})

it('does not route existing createOnly through unified save', async () => {
  mock.mockImplementation(async () => json({ ok: true, saved: true, workflowId: id, packId: 'pack', branch: `wf/${id}`, commit: oid }))
  const { result } = renderHook(useAutoHealSave, { wrapper })
  await act(async () => { await result.current.mutateAsync({ workflowId: id, packId: 'pack', spec: spec(), createOnly: true, requestId, expectedSavedHead: null }) })
  expect(mock).toHaveBeenCalledTimes(1)
  expect(mock.mock.calls[0][0]).toBe('/api/auto-heal/save')
})

it('locks on a lost unified response and never retries', async () => {
  const baseline = await readAutoHealUnifiedBaseline(id)
  confirmAutoHealUnifiedReview(baseline, spec())
  mock.mockImplementation(async () => { throw new Error('network disconnected') })
  const { result } = renderHook(useAutoHealSave, { wrapper })
  const count = mock.mock.calls.length
  await act(async () => { await expect(result.current.mutateAsync({ workflowId: id, spec: spec(), requestId, unifiedBaseline: baseline })).rejects.toThrow('network disconnected') })
  expect(mock).toHaveBeenCalledTimes(count + 1)
  expect(editorWorkflowWriteState(id).phase).toBe('unknown')
})

it('requires confirmation of the exact candidate; altered or unreviewed content is never sent', async () => {
  const baseline = await readAutoHealUnifiedBaseline(id)
  const { result } = renderHook(useAutoHealSave, { wrapper })
  const count = mock.mock.calls.length
  await act(async () => { await expect(result.current.mutateAsync({ workflowId: id, spec: spec(), requestId, unifiedBaseline: baseline })).rejects.toThrow('尚未确认') })
  confirmAutoHealUnifiedReview(baseline, spec())
  await act(async () => { await expect(result.current.mutateAsync({ workflowId: id, spec: { ...spec(), title: 'changed' }, requestId, unifiedBaseline: baseline })).rejects.toThrow('已变化') })
  expect(mock).toHaveBeenCalledTimes(count)
})

it('does not send a reviewed baseline after another tab-local write', async () => {
  const baseline = await readAutoHealUnifiedBaseline(id)
  confirmAutoHealUnifiedReview(baseline, spec())
  const owner = beginEditorWorkflowWrite(id); finishEditorWorkflowWrite(id, owner)
  const count = mock.mock.calls.length
  const { result } = renderHook(useAutoHealSave, { wrapper })
  await act(async () => { await expect(result.current.mutateAsync({ workflowId: id, spec: spec(), requestId, unifiedBaseline: baseline })).rejects.toThrow('可信') })
  expect(mock).toHaveBeenCalledTimes(count)
})

it('does not fall back when the server rejects the reviewed baseline as stale', async () => {
  const baseline = await readAutoHealUnifiedBaseline(id)
  confirmAutoHealUnifiedReview(baseline, spec())
  mock.mockImplementation(async () => json({ code: 'BASELINE_STALE' }, 409))
  const { result } = renderHook(useAutoHealSave, { wrapper })
  await act(async () => { await expect(result.current.mutateAsync({ workflowId: id, spec: spec(), requestId, unifiedBaseline: baseline })).rejects.toThrow('基线已变化') })
  expect(editorWorkflowWriteState(id).phase).toBe('clear')
  expect(mock.mock.calls.filter(([url]) => url === '/api/auto-heal/save')).toHaveLength(0)
})

it('treats a response-body timeout after POST as unknown and never retries or falls back', async () => {
  const baseline = await readAutoHealUnifiedBaseline(id)
  confirmAutoHealUnifiedReview(baseline, spec())
  const stream = new ReadableStream({ start() { /* keep the response body open */ } })
  mock.mockImplementation(async () => new Response(stream, { status: 503 }))
  const { result } = renderHook(useAutoHealSave, { wrapper })
  vi.useFakeTimers()
  try {
    let attempt!: Promise<unknown>
    await act(async () => { attempt = result.current.mutateAsync({ workflowId: id, spec: spec(), requestId, unifiedBaseline: baseline }); void attempt.catch(() => {}); await Promise.resolve(); await Promise.resolve() })
    await act(async () => { await vi.advanceTimersByTimeAsync(30_001) })
    await expect(attempt).rejects.toThrow('超时，结果未知')
    expect(editorWorkflowWriteState(id).phase).toBe('unknown')
    expect(mock.mock.calls.filter(([url]) => url === '/api/auto-heal/unified-save')).toHaveLength(1)
    expect(mock.mock.calls.filter(([url]) => url === '/api/auto-heal/save')).toHaveLength(0)
  } finally { vi.useRealTimers() }
})

it('bounds a capability read that never responds without sending a write', async () => {
  mock.mockImplementation(async () => new Promise<Response>(() => {}))
  vi.useFakeTimers()
  try {
    const attempt = readAutoHealUnifiedBaseline(id)
    void attempt.catch(() => {})
    await vi.advanceTimersByTimeAsync(10_001)
    await expect(attempt).rejects.toThrow('无法确认宿主保存能力')
    expect(mock).toHaveBeenCalledTimes(1)
    expect(editorWorkflowWriteState(id).phase).toBe('clear')
  } finally { vi.useRealTimers() }
})


it.each([
  [503, { code: 'UNIFIED_SAVE_UNAVAILABLE' }],
  [409, { code: 'BASELINE_STALE' }],
  [400, { code: 'INVALID_REQUEST' }],
  [409, { requestId, outcome: 'rejected', reconciliationRequired: false, evidence: { db: 'not_started', git: 'not_started' } }],
])('releases the write guard for a protocol pre-write rejection on HTTP %s', async (status, response) => {
  const baseline = await readAutoHealUnifiedBaseline(id)
  confirmAutoHealUnifiedReview(baseline, spec())
  mock.mockImplementation(async () => json(response, status))
  const { result } = renderHook(useAutoHealSave, { wrapper })
  await act(async () => { await expect(result.current.mutateAsync({ workflowId: id, spec: spec(), requestId, unifiedBaseline: baseline })).rejects.toThrow('未写入') })
  expect(editorWorkflowWriteState(id).phase).toBe('clear')
  expect(mock.mock.calls.filter(([url]) => url === '/api/auto-heal/save')).toHaveLength(0)
})

it.each([
  [500, { code: 'BASELINE_STALE' }],
  [409, { code: 'BASELINE_STALE', outcome: 'partial' }],
  [409, { requestId, outcome: 'rejected', evidence: { db: 'not_started', git: 'not_started' } }],
  [409, { requestId: 'another-request', outcome: 'rejected', reconciliationRequired: false, evidence: { db: 'not_started', git: 'not_started' } }],
  [409, { requestId, outcome: 'rejected', reconciliationRequired: true, evidence: { db: 'not_started', git: 'not_started' } }],
  [409, { requestId, outcome: 'rejected', reconciliationRequired: false, evidence: { db: 'unknown', git: 'not_started' } }],
  [409, { requestId, outcome: 'partial', reconciliationRequired: true, evidence: { db: 'confirmed', git: 'not_started' } }],
])('retains the unknown-write guard for an unproven HTTP %s result', async (status, response) => {
  const baseline = await readAutoHealUnifiedBaseline(id)
  confirmAutoHealUnifiedReview(baseline, spec())
  mock.mockImplementation(async () => json(response, status))
  const { result } = renderHook(useAutoHealSave, { wrapper })
  await act(async () => { await expect(result.current.mutateAsync({ workflowId: id, spec: spec(), requestId, unifiedBaseline: baseline })).rejects.toThrow('人工核对') })
  expect(editorWorkflowWriteState(id).phase).toBe('unknown')
  expect(mock.mock.calls.filter(([url]) => url === '/api/auto-heal/save')).toHaveLength(0)
})

it('binds snapshot deployment to the exact saved receipt and forbids mutation or downgrade', async () => {
  const baseline = await readAutoHealUnifiedBaseline(id)
  confirmAutoHealUnifiedReview(baseline, spec())
  mock.mockImplementation(async url => url === '/api/auto-heal/unified-save' ? json(receipt()) : json({ ok: true }))
  const { result } = renderHook(() => ({ save: useAutoHealSave(), deploy: useAutoHealDeploy() }), { wrapper })
  let saved: any
  await act(async () => { saved = await result.current.save.mutateAsync({ workflowId: id, spec: spec(), requestId, unifiedBaseline: baseline }) })
  const input = { workflowId: id, packId: 'pack', candidateCommit: oid, expectedDraftToken: nextToken,
    expectedActiveCommit: '', snapshotProtocol: 'saved-tree-v2' as const, confirmedFileDiff: true as const, requestId }
  expect(() => bindAutoHealDeployToSave({ ...input, expectedDraftToken: token }, saved)).toThrow('不匹配')
  expect(() => bindAutoHealDeployToSave({ ...input }, { ...saved })).toThrow('状态已变化')
  bindAutoHealDeployToSave(input, saved)
  const altered = input as typeof input & { snapshotProtocol?: string }
  delete (altered as { snapshotProtocol?: string }).snapshotProtocol
  const count = mock.mock.calls.length
  await act(async () => { await expect(result.current.deploy.mutateAsync(input)).rejects.toThrow('确认已变化') })
  expect(mock).toHaveBeenCalledTimes(count)
})

it('uses current and a null 草稿(wf) head only after an exact missing-snapshot response, then sends those CAS values', async () => {
  const current = 'f'.repeat(40)
  mock.mockImplementation(async url => {
    if (url === '/api/auto-heal/capabilities') return json({ autoHealUnifiedSave: 'v1' })
    if (String(url).endsWith('/editor-draft')) return json({ workflowId: id, packId: 'pack', specJson: JSON.stringify({ id, nodes: [] }), draftToken: token })
    if (String(url).endsWith('/pack-version?version=draft')) return json({ error: 'Snapshot not found', code: 'SNAPSHOT_NOT_FOUND' }, 404)
    if (String(url).endsWith('/pack-version?version=current')) return json({ workflowId: id, packId: 'pack', baseRef: `refs/tags/deploy/${id}/#1`, baseCommit: current, draftHead: null, versionFiles: [] })
    if (url === '/api/auto-heal/unified-save') return json(receipt())
    throw new Error('unexpected request')
  })
  const baseline = await readAutoHealUnifiedBaseline(id)
  expect(baseline).toMatchObject({ sourceVersion: 'current', sourceCommit: current, expectedSavedHead: null, expectedDraftToken: token })
  confirmAutoHealUnifiedReview(baseline, spec())
  const { result } = renderHook(useAutoHealSave, { wrapper })
  await act(async () => { await result.current.mutateAsync({ workflowId: id, spec: spec(), requestId, unifiedBaseline: baseline }) })
  expect(JSON.parse(await bodyText(mock.mock.calls.at(-1)![1]!.body as Blob))).toMatchObject({ sourceVersion: 'current', sourceCommit: current, expectedSavedHead: null, expectedDraftToken: token })
})

it.each([
  ['authorization denial', () => json({ error: 'Forbidden', code: 'FORBIDDEN' }, 403)],
  ['generic 404', () => json({ error: 'Not Found' }, 404)],
  ['wrong body', () => json({ code: 'SNAPSHOT_NOT_FOUND' }, 404)],
  ['extra fields', () => json({ error: 'Snapshot not found', code: 'SNAPSHOT_NOT_FOUND', extra: true }, 404)],
  ['server error', () => json({ error: 'Snapshot not found', code: 'SNAPSHOT_NOT_FOUND' }, 503)],
  ['network error with forged fields', () => { throw Object.assign(new Error('network'), { status: 404, body: '{"error":"Snapshot not found","code":"SNAPSHOT_NOT_FOUND"}' }) }],
])('does not use current or send a save after %s', async (_name, failed) => {
  setupReads()
  const prior = mock.getMockImplementation()!
  mock.mockImplementation(async (url, init) => String(url).endsWith('/pack-version?version=draft') ? failed() : prior(url, init))
  await expect(readAutoHealUnifiedBaseline(id)).rejects.toThrow()
  expect(mock.mock.calls.some(([url]) => String(url).includes('version=current'))).toBe(false)
  expect(mock.mock.calls.some(([url]) => url === '/api/auto-heal/unified-save' || url === '/api/auto-heal/save')).toBe(false)
})

it.each([
  ['saved ref appeared', { draftHead: oid, packId: 'pack' }],
  ['pack changed', { draftHead: null, packId: 'other' }],
])('rejects current baseline if %s', async (_name, fields) => {
  const prior = mock.getMockImplementation()!
  mock.mockImplementation(async (url, init) => {
    if (String(url).endsWith('/pack-version?version=draft')) return json({ error: 'Snapshot not found', code: 'SNAPSHOT_NOT_FOUND' }, 404)
    if (String(url).endsWith('/pack-version?version=current')) return json({ workflowId: id, packId: fields.packId, baseRef: `refs/tags/deploy/${id}/#1`, baseCommit: oid, draftHead: fields.draftHead, versionFiles: [] })
    return prior(url, init)
  })
  await expect(readAutoHealUnifiedBaseline(id)).rejects.toThrow('基线不一致')
})

it.each([401, 403, 404, 503])('fails closed when current snapshot returns HTTP %s', async status => {
  const prior = mock.getMockImplementation()!
  mock.mockImplementation(async (url, init) => {
    if (String(url).endsWith('/pack-version?version=draft')) return json({ error: 'Snapshot not found', code: 'SNAPSHOT_NOT_FOUND' }, 404)
    if (String(url).endsWith('/pack-version?version=current')) return json({ error: 'unavailable' }, status)
    return prior(url, init)
  })
  await expect(readAutoHealUnifiedBaseline(id)).rejects.toThrow()
  expect(mock.mock.calls.some(([url]) => url === '/api/auto-heal/unified-save' || url === '/api/auto-heal/save')).toBe(false)
})

it.each([null, oid, 'e'.repeat(40)])('rejects a legacy saved source regardless of its wf target head %s', async draftHead => {
  const prior = mock.getMockImplementation()!
  mock.mockImplementation(async (url, init) => String(url).endsWith('/pack-version?version=draft')
    ? json({ workflowId: id, packId: 'pack', baseRef: `origin/saved/${id}`, baseCommit: oid, draftHead, versionFiles: [] }) : prior(url, init))
  await expect(readAutoHealUnifiedBaseline(id)).rejects.toThrow('Invalid response')
  expect(mock.mock.calls.some(([url]) => String(url).includes('version=current'))).toBe(false)
  expect(mock.mock.calls.some(([url]) => url === '/api/auto-heal/unified-save' || url === '/api/auto-heal/save')).toBe(false)
})
