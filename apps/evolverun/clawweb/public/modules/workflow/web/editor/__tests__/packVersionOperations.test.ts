import { afterEach, expect, it, vi } from 'vitest'
import { fetchJson } from '@avernet/clawweb-shared/web/api/client'
import { sessionFor, updateVersion, type SaveResult } from '../packVersionSession'
import { savePackVersion, retryPackVersionSave, reconcilePackVersionSave, PACK_SAVE_TIMEOUT_MS } from '../packVersionSave'
import { deployPackCandidate, reconcilePackDeploy, PACK_DEPLOY_TIMEOUT_MS } from '../packDeploy'

vi.mock('@avernet/clawweb-shared/web/api/client', () => ({ fetchJson: vi.fn() }))
afterEach(() => { vi.useRealTimers(); vi.resetAllMocks() })
const A = 'a'.repeat(40), B = 'b'.repeat(40), C = 'c'.repeat(40)
let seq = 0
function setup() {
  const id = `operations-${++seq}`, session = sessionFor(id)
  const files = [{ path: 'script', contentBase64: btoa('original'), mode: '100755' as const },
    { path: 'workflow.pack.yaml', contentBase64: btoa(JSON.stringify({ id: 'pack', workflows: [id] })), mode: '100644' as const },
    { path: `workflows/${id}.yaml`, contentBase64: btoa(JSON.stringify({ id, title: 'Workflow', nodes: [] })), mode: '100644' as const }]
  const candidate = [{ ...files[0], contentBase64: btoa('edited') }, ...files.slice(1)]
  updateVersion(session, 'current', { snapshot: { workflowId: id, packId: 'pack', canWrite: true,
    baseRef: `refs/tags/deploy/${id}/#1`, baseCommit: A, draftHead: null, versionFiles: files }, files: candidate })
  return { id, session, candidate }
}
function result(id: string, requestId: string, commit = B): SaveResult { return { branch: `wf/${id}`, commit, requestId, version: 2, packId: 'pack' } }
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>(r => { resolve = r }); return { promise, resolve } }
async function readBlob(blob: Blob): Promise<string> { return new Promise(resolve => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result)); reader.readAsText(blob) }) }

it('GET only observes an unknown save; POST reconcile verifies the completed identity and restores candidate', async () => {
  const { id, session, candidate } = setup(), onSaved = vi.fn()
  vi.mocked(fetchJson).mockRejectedValueOnce({ status: 503, body: '{"code":"SAVE_OUTCOME_UNKNOWN"}' })
  await savePackVersion(session, id, 'current', candidate, onSaved)
  const attempt = session.state.reconciliation!.attempt
  vi.mocked(fetchJson).mockResolvedValueOnce({ requestId: attempt.requestId, kind: 'save', state: 'pending' })
  await reconcilePackVersionSave(session, id)
  expect(fetchJson).toHaveBeenLastCalledWith(`/api/workflows/${id}/pack-operations/${attempt.requestId}`, expect.objectContaining({ signal: expect.any(AbortSignal) }))
  expect(vi.mocked(fetchJson).mock.calls[1][1]).not.toHaveProperty('method')
  expect(session.state.reconciliation).not.toBeNull(); expect(onSaved).not.toHaveBeenCalled()
  vi.mocked(fetchJson).mockResolvedValueOnce({ requestId: attempt.requestId, kind: 'save', state: 'completed', result: result(id, attempt.requestId) })
  await reconcilePackVersionSave(session, id, true)
  expect(fetchJson).toHaveBeenLastCalledWith(`/api/workflows/${id}/pack-operations/${attempt.requestId}/reconcile`, expect.objectContaining({ method: 'POST', headers: { 'X-Pack-Version-CSRF': '1' } }))
  expect(session.state.reconciliation).toBeNull()
  expect(session.state.versions.draft.files).toEqual(candidate)
  expect(session.state.versions.draft.snapshot!.baseCommit).toBe(B)
  expect(onSaved).toHaveBeenCalledOnce()
})

it.each([
  { kind: 'deploy', state: 'completed' }, { kind: 'save', state: 'completed', requestId: 'other' },
  { kind: 'save', state: 'completed', result: { branch: 'wf/wrong', commit: B } },
])('does not clear a frozen attempt on unverified operation response: %j', async override => {
  const { id, session } = setup()
  vi.mocked(fetchJson).mockRejectedValueOnce(new TypeError('lost'))
  await savePackVersion(session, id, 'current')
  const pending = session.state.reconciliation!
  vi.mocked(fetchJson).mockResolvedValueOnce({ requestId: pending.attempt.requestId, ...override })
  await reconcilePackVersionSave(session, id)
  expect(session.state.reconciliation!.attempt).toBe(pending.attempt)
  expect(session.state.reconciliation!.checkError).toBe(true)
})

it('bounds a hung save, forbids retry, and ignores the original late completion', async () => {
  const { id, session } = setup(), old = deferred<SaveResult>(), onSaved = vi.fn()
  vi.useFakeTimers()
  vi.mocked(fetchJson).mockImplementationOnce(() => old.promise)
  const saving = savePackVersion(session, id, 'current', undefined, onSaved)
  await vi.advanceTimersByTimeAsync(PACK_SAVE_TIMEOUT_MS)
  await saving
  const attempt = session.state.reconciliation!.attempt
  await expect(retryPackVersionSave(session, id)).rejects.toThrow('无法安全自动重试')
  old.resolve(result(id, attempt.requestId, B)); await old.promise
  expect(session.state.reconciliation!.attempt).toBe(attempt)
  expect(session.state.versions.current.blocked).toBe(true)
  expect(session.state.versions.draft.snapshot).toBeNull()
  expect(onSaved).not.toHaveBeenCalled()
  expect(fetchJson).toHaveBeenCalledTimes(1)
})

it('retains conflict edits and never retries against a newly fetched head', async () => {
  const { id, session, candidate } = setup()
  vi.mocked(fetchJson).mockRejectedValueOnce({ status: 409, body: '{"code":"VERSION_CONFLICT"}' })
  await savePackVersion(session, id, 'current')
  expect(session.state.versions.current.files).toEqual(candidate)
  expect(session.state.versions.current.blocked).toBe(true)
  expect(session.state.versions.current.snapshot!.baseCommit).toBe(A)
  await savePackVersion(session, id, 'current')
  expect(fetchJson).toHaveBeenCalledTimes(1)
})

it.each([
  { code: 'WORKFLOW_BUSY', body: '{"code":"WORKFLOW_BUSY"}' },
  { code: 'REQUEST_CONFLICT', body: '{"code":"REQUEST_CONFLICT","requestId":"other-operation"}' },
])('keeps a pre-operation %s rejection editable without reconciliation', async ({ code, body }) => {
  const { id, session, candidate } = setup()
  vi.mocked(fetchJson).mockRejectedValueOnce({ status: 409, body })
  await savePackVersion(session, id, 'current')
  expect(vi.mocked(fetchJson).mock.calls[0][0]).toBe(`/api/workflows/${id}/pack-version`)
  expect(session.state.reconciliation).toBeNull()
  expect(session.state.versions.current.blocked).toBe(false)
  expect(session.state.versions.current.files).toEqual(candidate)
  expect(session.state.versions.current.error).toContain('并发请求')

  vi.mocked(fetchJson).mockImplementationOnce(async (_url, init) => {
    const input = JSON.parse(await readBlob(init!.body as Blob))
    expect(input.requestId).not.toBe('other-operation')
    return result(id, input.requestId)
  })
  await savePackVersion(session, id, 'current')
  expect(vi.mocked(fetchJson).mock.calls).toHaveLength(2)
  expect(vi.mocked(fetchJson).mock.calls[1][0]).toBe(`/api/workflows/${id}/pack-version`)
  expect(session.state.versions.draft.snapshot!.baseCommit).toBe(B)
})

it.each([undefined, false, 'true', 1])('does not write unless the snapshot explicitly grants canWrite: %j', async canWrite => {
  const { id, session } = setup()
  updateVersion(session, 'current', { snapshot: { ...session.state.versions.current.snapshot!, canWrite: canWrite as boolean } })
  await savePackVersion(session, id, 'current')
  expect(fetchJson).not.toHaveBeenCalled()
})

const deployed = (id: string) => ({ ok: true, deployed: true, workflowId: id, packId: 'pack', deployNumber: 3, version: 4, tagName: 'deploy/3' })
it('keeps deploy commit/requestId after failure and forbids both old and new candidate retries', async () => {
  const { id, session } = setup(), transport = vi.fn().mockRejectedValueOnce(new TypeError('lost')).mockResolvedValueOnce(deployed(id))
  await deployPackCandidate(session, id, 'pack', B, transport)
  const attempt = session.state.deployment!
  expect(attempt.pending).toBe(false)
  expect(() => deployPackCandidate(session, id, 'pack', C, transport)).toThrow('不能自动重试')
  expect(() => deployPackCandidate(session, id, 'pack', B, transport)).toThrow('不能自动重试')
  expect(transport).toHaveBeenCalledTimes(1)
  expect(session.state.deployment).toEqual(attempt)
})
it('queries deploy without side effects and restores only a verified completed result via explicit POST', async () => {
  const { id, session } = setup(), transport = vi.fn().mockRejectedValue(new TypeError('lost'))
  await deployPackCandidate(session, id, 'pack', B, transport)
  const requestId = session.state.deployment!.input.requestId
  vi.mocked(fetchJson).mockResolvedValueOnce({ requestId, kind: 'deploy', state: 'unknown' })
  expect(await reconcilePackDeploy(session)).toBeNull()
  expect(session.state.deployment!.input.requestId).toBe(requestId)
  vi.mocked(fetchJson).mockResolvedValueOnce({ requestId, kind: 'deploy', state: 'completed', result: deployed(id) })
  expect(await reconcilePackDeploy(session, true)).toEqual(deployed(id))
  expect(fetchJson).toHaveBeenLastCalledWith(`/api/workflows/${id}/pack-operations/${requestId}/reconcile`, expect.objectContaining({ method: 'POST', headers: { 'X-Pack-Version-CSRF': '1' } }))
  expect(session.state.deployment).toBeNull()
})
it('times out deployment without assigning a new request ID or accepting a late response', async () => {
  const { id, session } = setup(), old = deferred<unknown>(), transport = vi.fn().mockReturnValueOnce(old.promise)
  vi.useFakeTimers()
  const pending = deployPackCandidate(session, id, 'pack', B, transport)
  const requestId = session.state.deployment!.input.requestId
  await vi.advanceTimersByTimeAsync(PACK_DEPLOY_TIMEOUT_MS); await pending
  expect(session.state.deployment!.input.requestId).toBe(requestId)
  old.resolve(deployed(id)); await old.promise
  expect(session.state.deployment!.input.requestId).toBe(requestId)
})
it('preserves a renamed candidate when a mismatched receipt cannot be verified and retry is forbidden', async () => {
  const { id, session, candidate } = setup()
  const renamed = candidate.map(file => file.path === 'workflow.pack.yaml'
    ? { ...file, contentBase64: btoa(JSON.stringify({ id: 'renamed', workflows: [id] })) } : file)
  vi.mocked(fetchJson).mockRejectedValueOnce(new TypeError('lost'))
  await savePackVersion(session, id, 'current', renamed)
  const pending = session.state.reconciliation!
  expect(pending.attempt.expectedPackId).toBe('renamed')
  // A completed operation receipt with the old identity must not thaw the attempt.
  vi.mocked(fetchJson).mockResolvedValueOnce({ requestId: pending.attempt.requestId, kind: 'save', state: 'completed', result: result(id, pending.attempt.requestId) })
  await reconcilePackVersionSave(session, id)
  expect(session.state.reconciliation!.checkError).toBe(true)
  expect(session.state.versions.draft.snapshot).toBeNull()
  await expect(retryPackVersionSave(session, id)).rejects.toThrow('无法安全自动重试')
  expect(session.state.reconciliation!.attempt).toBe(pending.attempt)
  expect(session.state.versions.current.files).toEqual(renamed)
  expect(session.state.versions.current.blocked).toBe(true)
  expect(fetchJson).toHaveBeenCalledTimes(2)
})
it('accepts a correctly identified renamed candidate from completed operation recovery', async () => {
  const { id, session, candidate } = setup()
  const renamed = candidate.map(file => file.path === 'workflow.pack.yaml'
    ? { ...file, contentBase64: btoa(JSON.stringify({ id: 'renamed', workflows: [id] })) } : file)
  vi.mocked(fetchJson).mockRejectedValueOnce(new TypeError('lost'))
  await savePackVersion(session, id, 'current', renamed)
  const requestId = session.state.reconciliation!.attempt.requestId
  vi.mocked(fetchJson).mockResolvedValueOnce({ requestId, kind: 'save', state: 'completed', result: { ...result(id, requestId), packId: 'renamed' } })
  await reconcilePackVersionSave(session, id, true)
  expect(session.state.reconciliation).toBeNull()
  expect(session.state.versions.draft.snapshot).toMatchObject({ packId: 'renamed', baseCommit: B, workflowId: id })
})
it('unfreezes only a verified pre-push failed receipt, keeping candidate and baseline for a corrected NEW request', async () => {
  const { candidateProjection } = await import('../../../server/services/pack-operation-service')
  const { id, session } = setup()
  const files = session.state.versions.current.files
  const yaml = atob(files.find(file => file.path.endsWith('.yaml') && file.path !== 'workflow.pack.yaml')!.contentBase64)
  expect(() => candidateProjection(id, yaml, atob(files.find(file => file.path === 'workflow.pack.yaml')!.contentBase64))).toThrow()
  vi.mocked(fetchJson).mockRejectedValueOnce({ status: 400, body: '{"code":"INVALID_WORKFLOW"}' })
  await savePackVersion(session, id, 'current')
  const failed = session.state.reconciliation!.attempt
  vi.mocked(fetchJson).mockResolvedValueOnce({ requestId: failed.requestId, kind: 'save', state: 'failed' })
  await reconcilePackVersionSave(session, id)
  expect(session.state.reconciliation).toBeNull()
  expect(session.state.versions.current.blocked).toBe(false)
  expect(session.state.versions.current.snapshot!.baseCommit).toBe(A)
  expect(session.state.versions.current.files).toEqual(failed.files)
  const fixed = { id, title: 'Fixed', version: '1', nodes: [{ id: 'done', title: 'Done', phase: 'execute', executor: { type: 'done' } }] }
  updateVersion(session, 'current', { files: failed.files.map(file => file.path === `workflows/${id}.yaml` ? { ...file, contentBase64: btoa(JSON.stringify(fixed)) } : file) })
  vi.mocked(fetchJson).mockImplementationOnce(async (_url, init) => {
    const input = JSON.parse(await readBlob(init!.body as Blob))
    expect(input.requestId).not.toBe(failed.requestId)
    expect(input).toMatchObject({ sourceCommit: A, expectedSavedHead: null })
    return result(id, input.requestId)
  })
  await savePackVersion(session, id, 'current')
  expect(session.state.versions.draft.snapshot!.baseCommit).toBe(B)
})
it.each(['unknown', 'preparing', 'prepared', 'pushing'])('does not thaw a %s operation receipt', async state => {
  const { id, session } = setup()
  vi.mocked(fetchJson).mockRejectedValueOnce(new TypeError('lost'))
  await savePackVersion(session, id, 'current')
  const pending = session.state.reconciliation!
  vi.mocked(fetchJson).mockResolvedValueOnce({ requestId: pending.attempt.requestId, kind: 'save', state })
  await reconcilePackVersionSave(session, id)
  expect(session.state.reconciliation!.attempt).toBe(pending.attempt)
  expect(session.state.versions.current.blocked).toBe(true)
})
it.each([403, 404])('never treats an HTTP %s lookup failure as proof of no write', async status => {
  const { id, session } = setup()
  vi.mocked(fetchJson).mockRejectedValueOnce(new TypeError('lost'))
  await savePackVersion(session, id, 'current')
  vi.mocked(fetchJson).mockRejectedValueOnce({ status, body: '{"code":"OPERATION_NOT_FOUND"}' })
  await reconcilePackVersionSave(session, id)
  expect(session.state.reconciliation).not.toBeNull(); expect(session.state.versions.current.blocked).toBe(true)
})
it('rejects a failed receipt for a different request without unlocking files', async () => {
  const { id, session } = setup()
  vi.mocked(fetchJson).mockRejectedValueOnce(new TypeError('lost'))
  await savePackVersion(session, id, 'current')
  vi.mocked(fetchJson).mockResolvedValueOnce({ requestId: 'different-operation', kind: 'save', state: 'failed' })
  await reconcilePackVersionSave(session, id)
  expect(session.state.reconciliation).not.toBeNull(); expect(session.state.versions.current.blocked).toBe(true)
})
