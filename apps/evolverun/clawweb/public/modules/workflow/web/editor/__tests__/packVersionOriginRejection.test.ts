import { afterEach, expect, it, vi } from 'vitest'
import {
  dirty, hasPackVersionEditRisk, observeReconciliationHead, publish, sessionFor, updateVersion,
  type SaveInput, type Snapshot, type Version,
} from '../packVersionSession'
import { PACK_SAVE_TIMEOUT_MS, reconcilePackVersionSave, retryPackVersionSave, savePackVersion } from '../packVersionSave'

const A = 'a'.repeat(40), B = 'b'.repeat(40), C = 'c'.repeat(40)
const originBody = JSON.stringify({ code: 'ORIGIN_NOT_ALLOWED', error: 'untrusted upstream details' })
let sequence = 0

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks() })

function setup(version: Version = 'current', draftHead: string | null = null) {
  const id = `origin-rejection-${++sequence}`, session = sessionFor(id)
  const files = [
    { path: 'scripts/run.sh', contentBase64: btoa('original'), mode: '100644' as const },
    { path: 'scripts/mode-only.sh', contentBase64: btoa('unchanged'), mode: '100644' as const },
    { path: 'remove.txt', contentBase64: btoa('remove'), mode: '100644' as const },
    { path: 'workflow.pack.yaml', contentBase64: btoa(JSON.stringify({ id: 'pack', workflows: [id] })), mode: '100644' as const },
    { path: `workflows/${id}.yaml`, contentBase64: btoa(JSON.stringify({ id, title: 'Workflow', nodes: [] })), mode: '100644' as const },
  ]
  const candidate = [
    { ...files[0], contentBase64: btoa('edited'), mode: '100755' as const },
    { ...files[1], mode: '100755' as const },
    ...files.slice(3),
    { path: 'new.bin', contentBase64: 'AP8=', mode: '100644' as const },
  ]
  const snapshot: Snapshot = { workflowId: id, packId: 'pack', canWrite: true,
    baseRef: (version === 'current' ? `refs/tags/deploy/${id}/#1` : `origin/wf/${id}`),
    baseCommit: version === 'current' ? A : B, draftHead: version === 'current' ? draftHead : B, versionFiles: files }
  publish(session, { ...session.state, version })
  updateVersion(session, version, { snapshot, files: candidate })
  return { id, session, snapshot, candidate }
}

async function readInput(init: RequestInit): Promise<SaveInput> {
  const text = await new Promise<string>((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result))
    reader.onerror = reject
    reader.readAsText(init.body as Blob)
  })
  return JSON.parse(text)
}

// These tests must fail if the exact pre-write denial enters unknown-outcome
// handling, loses candidate bytes/modes/CAS, or resends without a new user save.
it.each([
  { version: 'current' as const, draftHead: null },
  { version: 'current' as const, draftHead: A },
  { version: 'draft' as const, draftHead: B },
])('keeps $version candidates editable after an origin denial (草稿(wf) head $draftHead)', async ({ version, draftHead }) => {
  const { id, session, snapshot, candidate } = setup(version, draftHead)
  vi.spyOn(window, 'confirm').mockReturnValue(true)
  const onSaved = vi.fn()
  const fetchMock = vi.fn().mockResolvedValueOnce(new Response(originBody, { status: 403 }))
  vi.stubGlobal('fetch', fetchMock)

  expect(await savePackVersion(session, id, version, candidate, onSaved)).toBeNull()
  expect(session.state.reconciliation).toBeNull()
  expect(session.state.saving).toBe(false)
  expect(session.saveRequest).toBeUndefined()
  expect(session.state.version).toBe(version)
  expect(session.state.versions[version]).toMatchObject({ files: candidate, blocked: false, notice: null })
  expect(session.state.versions[version].snapshot).toBe(snapshot)
  expect(dirty(session.state.versions[version])).toBe(true)
  expect(hasPackVersionEditRisk(id)).toBe(true)
  expect(session.state.versions[version].error).toMatch(/版本文件保存请求被来源校验拒绝.*本地修改保留/)
  expect(session.state.versions[version].error).not.toMatch(/untrusted|保存未开始|未写入.*(?:DB|数据库)/)
  expect(onSaved).not.toHaveBeenCalled()

  vi.useFakeTimers()
  await vi.advanceTimersByTimeAsync(PACK_SAVE_TIMEOUT_MS * 2)
  vi.useRealTimers()
  await reconcilePackVersionSave(session, id)
  await reconcilePackVersionSave(session, id, true)
  await observeReconciliationHead(session, id)
  await expect(retryPackVersionSave(session, id)).rejects.toThrow('无法安全自动重试')
  expect(fetchMock).toHaveBeenCalledTimes(1)
  const [url, firstInit] = fetchMock.mock.calls[0] as [string, RequestInit]
  expect(url).toBe(`/api/workflows/${id}/pack-version`)
  expect(firstInit.method).toBe('PUT')
  expect(firstInit.headers).toMatchObject({ 'X-Pack-Version-CSRF': '1' })
  const first = await readInput(firstInit)
  expect(first).toMatchObject({ sourceVersion: version, sourceCommit: version === 'current' ? A : B, expectedSavedHead: draftHead,
    changes: [
      { path: 'remove.txt', operation: 'delete' },
      { path: 'scripts/run.sh', operation: 'replace', contentBase64: btoa('edited'), mode: '100755' },
      { path: 'scripts/mode-only.sh', operation: 'replace', contentBase64: btoa('unchanged'), mode: '100755' },
      { path: 'new.bin', operation: 'create', contentBase64: 'AP8=', mode: '100644' },
    ] })
  expect(first.requestId).toEqual(expect.any(String))

  let second: SaveInput | undefined
  fetchMock.mockImplementationOnce(async (_url: string, init: RequestInit) => {
    second = await readInput(init)
    return new Response(JSON.stringify({ branch: `wf/${id}`, commit: C, requestId: second.requestId, version: 2, packId: 'pack' }))
  })
  const saved = await savePackVersion(session, id, version, undefined, onSaved)
  expect(saved).toMatchObject({ branch: `wf/${id}`, commit: C })
  expect(fetchMock).toHaveBeenCalledTimes(2)
  expect(second?.requestId).toEqual(expect.any(String))
  expect(second?.requestId).not.toBe(first.requestId)
  expect(second).toEqual({ ...first, requestId: second?.requestId })
  expect(session.state.versions.draft.files).toEqual(candidate)
  expect(session.state.versions.draft.snapshot?.baseCommit).toBe(C)
  expect(onSaved).toHaveBeenCalledOnce()
})

it('accepts an origin denial correlated to this PUT without thawing the other version', async () => {
  const { id, session, candidate } = setup()
  updateVersion(session, 'draft', { blocked: true, error: 'Earlier conflict' })
  const otherVersion = session.state.versions.draft
  vi.stubGlobal('fetch', vi.fn(async (_url: string, init: RequestInit) => {
    const input = await readInput(init)
    return new Response(JSON.stringify({ code: 'ORIGIN_NOT_ALLOWED', requestId: input.requestId }), { status: 403 })
  }))
  await savePackVersion(session, id, 'current')
  expect(session.state.reconciliation).toBeNull()
  expect(session.state.versions.current).toMatchObject({ blocked: false, files: candidate })
  expect(session.state.versions.draft).toBe(otherVersion)
})

// Broadening the exception to another status/code, accepting malformed bodies,
// or ignoring a mismatched response requestId must leave these cases failing.
it.each([
  { name: 'generic 403', status: 403, body: '{}' },
  { name: 'permission denial', status: 403, body: '{"code":"FORBIDDEN"}' },
  { name: 'wrong code case', status: 403, body: '{"code":"origin_not_allowed"}' },
  { name: 'code only in message', status: 403, body: '{"error":"ORIGIN_NOT_ALLOWED"}' },
  { name: 'invalid JSON', status: 403, body: '{"code":"ORIGIN_NOT_ALLOWED"' },
  { name: 'HTML error', status: 403, body: '<h1>ORIGIN_NOT_ALLOWED</h1>' },
  { name: 'array JSON', status: 403, body: '[{"code":"ORIGIN_NOT_ALLOWED"}]' },
  { name: 'null JSON', status: 403, body: 'null' },
  { name: 'string JSON', status: 403, body: '"ORIGIN_NOT_ALLOWED"' },
  { name: 'oversized JSON', status: 403, body: JSON.stringify({ code: 'ORIGIN_NOT_ALLOWED', error: 'x'.repeat(8192) }) },
  { name: 'different requestId', status: 403, body: '{"code":"ORIGIN_NOT_ALLOWED","requestId":"another-request"}' },
  { name: 'null requestId', status: 403, body: '{"code":"ORIGIN_NOT_ALLOWED","requestId":null}' },
  { name: 'empty requestId', status: 403, body: '{"code":"ORIGIN_NOT_ALLOWED","requestId":""}' },
  { name: 'numeric requestId', status: 403, body: '{"code":"ORIGIN_NOT_ALLOWED","requestId":42}' },
  ...[400, 401, 404, 409, 422, 429, 500, 503].map(status => ({ name: `origin code with HTTP ${status}`, status, body: originBody })),
  { name: 'generic 500', status: 500, body: '{}' },
  { name: 'generic 503', status: 503, body: '{"code":"SERVICE_UNAVAILABLE"}' },
])('keeps $name unverified and frozen', async ({ status, body }) => {
  const { id, session, snapshot, candidate } = setup()
  const fetchMock = vi.fn().mockResolvedValueOnce(new Response(body, { status }))
  vi.stubGlobal('fetch', fetchMock)
  await savePackVersion(session, id, 'current')
  expect(session.state.reconciliation).toMatchObject({ reason: 'unverified', identity: null, attemptedFiles: candidate,
    sourceCommit: A, expectedSavedHead: null })
  expect(session.state.versions.current).toMatchObject({ blocked: true, files: candidate })
  expect(session.state.versions.current.snapshot).toBe(snapshot)
  expect(session.state.saving).toBe(false)
  await savePackVersion(session, id, 'current')
  expect(fetchMock).toHaveBeenCalledTimes(1)
})

it('keeps SAVE_OUTCOME_UNKNOWN frozen with its validated Git identity', async () => {
  const { id, session, candidate } = setup()
  vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({ code: 'SAVE_OUTCOME_UNKNOWN',
    branch: `wf/${id}`, commit: C }), { status: 503 })))
  await savePackVersion(session, id, 'current')
  expect(session.state.reconciliation).toMatchObject({ reason: 'server', identity: { branch: `wf/${id}`, commit: C }, attemptedFiles: candidate })
  expect(session.state.versions.current.blocked).toBe(true)
})

it.each(['status', 'recover', 'head'] as const)('does not clear an earlier unknown on an origin-denied %s request', async action => {
  const { id, session, snapshot, candidate } = setup()
  const fetchMock = vi.fn().mockRejectedValueOnce(new TypeError('Network lost'))
    .mockResolvedValueOnce(new Response(originBody, { status: 403 }))
  vi.stubGlobal('fetch', fetchMock)
  await savePackVersion(session, id, 'current')
  const pending = session.state.reconciliation!
  expect(pending.reason).toBe('no-response')
  if (action === 'head') await observeReconciliationHead(session, id)
  else await reconcilePackVersionSave(session, id, action === 'recover')
  expect(session.state.reconciliation).toMatchObject({ token: pending.token, checking: false, checkError: true, reason: 'no-response' })
  expect(session.state.reconciliation?.attempt).toBe(pending.attempt)
  expect(session.state.versions.current).toMatchObject({ blocked: true, files: candidate })
  expect(session.state.versions.current.snapshot).toBe(snapshot)
  const [url, init] = fetchMock.mock.calls[1] as [string, RequestInit]
  expect(url).toBe(action === 'head' ? `/api/workflows/${id}/pack-version?version=draft`
    : `/api/workflows/${id}/pack-operations/${pending.attempt.requestId}${action === 'recover' ? '/reconcile' : ''}`)
  expect(init.method).toBe(action === 'recover' ? 'POST' : undefined)
  await savePackVersion(session, id, 'current')
  expect(fetchMock).toHaveBeenCalledTimes(2)
})

it('does not thaw a timed-out save when its original PUT later receives an origin denial', async () => {
  const { id, session, candidate } = setup()
  let respond!: (response: Response) => void
  const lateResponse = new Promise<Response>(resolve => { respond = resolve })
  const fetchMock = vi.fn().mockReturnValueOnce(lateResponse)
  vi.stubGlobal('fetch', fetchMock)
  vi.useFakeTimers()
  const saving = savePackVersion(session, id, 'current')
  await vi.advanceTimersByTimeAsync(PACK_SAVE_TIMEOUT_MS)
  await saving
  const pending = session.state.reconciliation
  expect(pending).toMatchObject({ reason: 'no-response', attemptedFiles: candidate })
  respond(new Response(originBody, { status: 403 }))
  await vi.advanceTimersByTimeAsync(PACK_SAVE_TIMEOUT_MS)
  expect(session.state.reconciliation).toBe(pending)
  expect(session.state.versions.current.blocked).toBe(true)
  await savePackVersion(session, id, 'current')
  expect(fetchMock).toHaveBeenCalledTimes(1)
})

it('does not clear another workflow unknown when the current PUT is rejected', async () => {
  const earlier = setup(), current = setup()
  vi.stubGlobal('fetch', vi.fn().mockRejectedValueOnce(new TypeError('Network lost'))
    .mockResolvedValueOnce(new Response(originBody, { status: 403 })))
  await savePackVersion(earlier.session, earlier.id, 'current')
  const earlierState = earlier.session.state
  await savePackVersion(current.session, current.id, 'current')
  expect(current.session.state.reconciliation).toBeNull()
  expect(current.session.state.versions.current.blocked).toBe(false)
  expect(earlier.session.state).toBe(earlierState)
  expect(earlier.session.state.reconciliation?.reason).toBe('no-response')
  expect(earlier.session.state.versions.current.blocked).toBe(true)
})

it('does not clear a newer source error when an explicitly retried save completes', async () => {
  const { id, session } = setup()
  let finish!: () => void
  const fetchMock = vi.fn().mockResolvedValueOnce(new Response(originBody, { status: 403 }))
  vi.stubGlobal('fetch', fetchMock)
  await savePackVersion(session, id, 'current')
  fetchMock.mockImplementationOnce(async (_url: string, init: RequestInit) => {
    const input = await readInput(init)
    return new Promise<Response>(resolve => {
      finish = () => resolve(new Response(JSON.stringify({ branch: `wf/${id}`, commit: C,
        requestId: input.requestId, version: 2, packId: 'pack' })))
    })
  })
  const saving = savePackVersion(session, id, 'current')
  await vi.waitFor(() => expect(finish).toBeTypeOf('function'))
  updateVersion(session, 'current', { error: 'A different source observation failed' })
  finish()
  expect(await saving).toMatchObject({ commit: C })
  expect(session.state.versions.current.error).toBe('A different source observation failed')
})
