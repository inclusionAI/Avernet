import { afterEach, expect, it, vi } from 'vitest'
import { fetchJson } from '@avernet/clawweb-shared/web/api/client'
import { classifySaveFailure, observeReconciliationHead, sessionFor, updateVersion, validateSnapshot, type Snapshot } from '../packVersionSession'
import { currentVersionSaveConflict, savePackVersion } from '../packVersionSave'

vi.mock('@avernet/clawweb-shared/web/api/client', () => ({ fetchJson: vi.fn() }))
afterEach(() => vi.resetAllMocks())
const A = 'a'.repeat(40), B = 'b'.repeat(40), C = 'c'.repeat(40)
let sequence = 0
const makeSnapshot = (id: string, baseRef: string, draftHead: string | null = B): Snapshot => ({
  workflowId: id, packId: 'pack', baseRef, baseCommit: B, draftHead, canWrite: true,
  versionFiles: [
    { path: 'workflow.pack.yaml', mode: '100644', contentBase64: btoa(`id: pack\nworkflows: [${id}]\n`) },
    { path: `workflows/${id}.yaml`, mode: '100644', contentBase64: btoa(`id: ${id}\nversion: '1'\ntitle: Test\nnodes: []\n`) },
    { path: 'scripts/run', mode: '100755', contentBase64: btoa('before') },
    { path: 'assets/raw', mode: '100644', contentBase64: 'AP8C' },
  ],
})
it('reads wf as draft and an exact workflow deployment tag as current', () => {
  expect(validateSnapshot(makeSnapshot('flow', 'origin/wf/flow'), 'flow', 'draft').baseRef).toBe('origin/wf/flow')
  expect(validateSnapshot(makeSnapshot('flow', 'refs/tags/deploy/flow/#7'), 'flow', 'current').baseRef).toBe('refs/tags/deploy/flow/#7')
})
it.each(['origin/wf/flow', 'origin/saved/flow', 'refs/pack-version/active', 'refs/tags/deploy/other/#7', 'refs/tags/deploy/flow/#0', 'refs/tags/deploy/flow/#01', 'refs/tags/deploy/flow/#7\n'])('rejects non-production or malformed current ref %s', baseRef => {
  expect(() => validateSnapshot(makeSnapshot('flow', baseRef), 'flow', 'current')).toThrow()
})
it.each([null, A])('rejects saved as a draft source regardless of target head %s', draftHead => {
  const value = makeSnapshot('flow', 'origin/saved/flow', draftHead)
  expect(() => validateSnapshot(value, 'flow', 'draft')).toThrow()
})
it('still requires a normal wf draft to attest its actual tip', () => {
  expect(() => validateSnapshot(makeSnapshot('flow', 'origin/wf/flow', null), 'flow', 'draft')).toThrow()
  expect(() => validateSnapshot(makeSnapshot('flow', 'origin/wf/flow', A), 'flow', 'draft')).toThrow()
  expect(() => validateSnapshot(makeSnapshot('flow', 'origin/saved/other'), 'flow', 'draft')).toThrow()
})
it('keeps the existing different-wf-draft overwrite protection for a current-based save', () => {
  expect(currentVersionSaveConflict('current', makeSnapshot('flow', 'refs/tags/deploy/flow/#7', A))).toContain('不能用当前版覆盖')
})
it.each(['draft', 'current'] as const)('accepts a wf save receipt without modifying active tag bytes (%s)', async version => {
  const id = `wf-contract-${++sequence}`, session = sessionFor(id)
  const active = { ...makeSnapshot(id, `refs/tags/deploy/${id}/#7`, null), baseCommit: A }
  const draft = makeSnapshot(id, `origin/wf/${id}`)
  updateVersion(session, 'current', { snapshot: active, files: structuredClone(active.versionFiles) })
  if (version === 'draft') updateVersion(session, 'draft', { snapshot: draft, files: structuredClone(draft.versionFiles) })
  const base = version === 'draft' ? draft : active
  const candidate = base.versionFiles.map(f => f.path === 'scripts/run' ? { ...f, contentBase64: btoa('after') } : f)
  updateVersion(session, version, { files: candidate })
  vi.mocked(fetchJson).mockImplementation(async (_url, init) => {
    const body = await new Promise<string>(resolve => { const r = new FileReader(); r.onload = () => resolve(String(r.result)); r.readAsText(init!.body as Blob) })
    const input = JSON.parse(body)
    expect(input).toMatchObject({ sourceVersion: version, sourceCommit: base.baseCommit, expectedSavedHead: base.draftHead })
    return { branch: `wf/${id}`, commit: C, requestId: input.requestId, packId: 'pack', version: 2 }
  })
  expect(await savePackVersion(session, id, version)).toMatchObject({ branch: `wf/${id}`, commit: C })
  expect(session.state.versions.draft.snapshot).toMatchObject({ baseRef: `origin/wf/${id}`, baseCommit: C, versionFiles: candidate })
  expect(session.state.versions.current.snapshot).toMatchObject({ baseRef: active.baseRef, baseCommit: A, versionFiles: active.versionFiles })
  expect(fetchJson).toHaveBeenCalledTimes(1)
})
it.each([null, A])('refuses an obsolete saved-source response after an uncertain save (%s)', async draftHead => {
  const id = `wf-observe-${++sequence}`, session = sessionFor(id), base = makeSnapshot(id, `origin/wf/${id}`)
  updateVersion(session, 'draft', { snapshot: base, files: base.versionFiles.map(f => f.path === 'scripts/run' ? { ...f, contentBase64: btoa('local') } : f) })
  vi.mocked(fetchJson).mockRejectedValueOnce(new Error('lost'))
  await savePackVersion(session, id, 'draft')
  const pending = session.state.reconciliation!
  vi.mocked(fetchJson).mockResolvedValueOnce(makeSnapshot(id, `origin/saved/${id}`, draftHead))
  await observeReconciliationHead(session, id)
  expect(session.state.reconciliation?.observation).toBeNull()
  expect(session.state.reconciliation?.checkError).toBe(true)
  expect(session.state.reconciliation?.attempt).toBe(pending.attempt)
  expect(session.state.versions.draft.files).toEqual(pending.attempt.files)
})
it('recognizes only a wf target in an unknown save receipt', () => {
  const failure = (branch: string) => classifySaveFailure({ status: 503, body: JSON.stringify({ code: 'SAVE_OUTCOME_UNKNOWN', branch, commit: C }) }, 'flow')
  expect(failure('wf/flow').identity).toEqual({ branch: 'wf/flow', commit: C })
  expect(failure('saved/flow').identity).toBeNull()
})
