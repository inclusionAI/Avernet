import { act, cleanup, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { stringify } from 'yaml'
import { api, fetchJson } from '@avernet/clawweb-shared/web/api/client'
import { readEditorWorkflowSource } from '../../api/editorWorkflowSource'
import { useEditorStore } from '../store'
import { usePackCanvasSession } from '../usePackCanvasSession'
import { UNIFIED_SAVE_TIMEOUT_MS } from '../../api/workflowUnifiedSave'
import { editorWorkflowWriteState } from '../workflowWriteGuard'
import { hasPackVersionRisk, load, publish, releaseCleanSession, sessionFor, updateVersion } from '../packVersionSession'

vi.mock('@avernet/clawweb-shared/web/api/client', () => ({ api: { workflows: { get: vi.fn() } }, fetchJson: vi.fn() }))
const token = 'a'.repeat(64), oid = 'c'.repeat(40), savedOid = 'd'.repeat(40), secondOid = 'f'.repeat(40)
const text = (body: Blob) => new Promise<string>(resolve => { const r = new FileReader(); r.onload = () => resolve(String(r.result)); r.readAsText(body) })
let snapshot: { workflowId: string; packId: string; baseRef: string; baseCommit: string; draftHead: string; canWrite: boolean; versionFiles: { path: string; contentBase64: string; mode: '100644' | '100755' }[] }
let sequence = 0
let row: { workflowId: string; packId: string | null; specJson: string; draftToken: string }, failRead: boolean, hangRead: boolean
let postBegan: () => void, postStarted: Promise<void>, postCount: number
let release!: () => void, began!: () => void, pending: Promise<void>, started: Promise<void>
beforeEach(() => {
  useEditorStore.getState().reset({ force: true }); failRead = false; hangRead = false; postCount = 0
  postStarted = new Promise(r => { postBegan = r })
  pending = new Promise(r => { release = r }); started = new Promise(r => { began = r })
  vi.mocked(api.workflows.get).mockImplementation(async () => JSON.parse(row.specJson) as never)
  vi.spyOn(window, 'confirm').mockReturnValue(true)
  vi.mocked(fetchJson).mockImplementation(async url => {
    if (url.endsWith('/editor-draft')) { if (failRead) throw new Error('offline'); if (hangRead) { postBegan(); await new Promise(() => {}) } return structuredClone(row) as never }
    if (url.endsWith('/pack-version?version=draft')) return structuredClone(snapshot) as never
    throw new Error(`unexpected ${url}`)
  })
  vi.stubGlobal('fetch', vi.fn(async (_url: string, init: RequestInit) => {
    if (init.method === 'GET') return new Response(JSON.stringify({ protocol: 'workflow-unified-save-v1' }))
    const input = JSON.parse(await text(init.body as Blob))
    postCount++
    const commit = postCount === 1 ? savedOid : secondOid
    row = { ...row, specJson: input.specJson, draftToken: (postCount === 1 ? 'b' : 'c').repeat(64) }
    const files = new Map(snapshot.versionFiles.map(file => [file.path, { ...file }]))
    for (const change of input.changes) {
      if (change.operation === 'delete') files.delete(change.path)
      else files.set(change.path, { path: change.path, mode: change.mode, contentBase64: change.contentBase64 })
    }
    const workflow = [...files.values()].find(file => file.path.startsWith('workflows/'))!
    files.set(workflow.path, { ...workflow, contentBase64: btoa(stringify(JSON.parse(input.specJson), { sortMapEntries: true, lineWidth: 0 })) })
    snapshot = { ...snapshot, baseCommit: commit, draftHead: commit, versionFiles: [...files.values()] }
    if (postCount === 1) { began(); await pending }
    return new Response(JSON.stringify({ requestId: input.requestId, outcome: 'confirmed', reconciliationRequired: false,
      evidence: { db: 'confirmed', git: 'confirmed', inputHash: 'e'.repeat(64), commit, draftToken: row.draftToken } }))
  }))
})
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks(); vi.resetAllMocks() })
async function setup() {
  const id = `postflight-${++sequence}`, spec = { id, title: 'Original', version: '1', nodes: [] }
  row = { workflowId: id, packId: null, specJson: JSON.stringify(spec), draftToken: token }
  const source = await readEditorWorkflowSource(id)
  useEditorStore.getState().loadSpec(source.spec, source.databaseSpec, source.activeDeployNumber)
  const files = [{ path: 'workflow.pack.yaml', contentBase64: btoa(stringify({ id, workflows: [id] })), mode: '100644' as const },
    { path: `workflows/${id}.yaml`, contentBase64: btoa(stringify(spec)), mode: '100644' as const },
    { path: 'scripts/a.sh', contentBase64: btoa('old'), mode: '100644' as const }]
  snapshot = { workflowId: id, packId: id, baseRef: `origin/wf/${id}`, baseCommit: oid, draftHead: oid, canWrite: true, versionFiles: files }
  const session = sessionFor(id)
  publish(session, { ...session.state, version: 'draft', versions: { ...session.state.versions, draft: { snapshot, files: structuredClone(files), loading: false, error: null, blocked: false, notice: null } } })
  updateVersion(session, 'draft', { files: files.map(f => f.path === 'scripts/a.sh' ? { ...f, contentBase64: btoa('submitted') } : f) })
  return { id, session, hook: renderHook(() => usePackCanvasSession(id)) }
}
it.each(['null-to-workflow-id', 'document-change', 'read-failure'] as const)('locks the unverified snapshot and retains later edits on postflight %s', async kind => {
  const ctx = await setup()
  let saving!: Promise<unknown>
  act(() => { saving = ctx.hook.result.current.save() })
  await started
  if (kind === 'null-to-workflow-id') row.packId = ctx.id
  if (kind === 'document-change') row.specJson = JSON.stringify({ id: ctx.id, title: 'Remote', version: '1', nodes: [] })
  if (kind === 'read-failure') failRead = true
  act(() => { const v = ctx.session.state.versions.draft; updateVersion(ctx.session, 'draft', { files: v.files.map(f => f.path === 'scripts/a.sh' ? { ...f, contentBase64: btoa('later edit') } : f) }) })
  await act(async () => { release(); await expect(saving).rejects.toMatchObject({ name: 'PackCanvasPartialSaveError' }) })
  expect(ctx.session.state.postSaveVerificationError).toContain('统一保存未能完整确认')
  expect(ctx.session.state.versions.draft.snapshot?.baseCommit).toBe(oid)
  expect(ctx.session.state.versions.draft.files.find(f => f.path === 'scripts/a.sh')?.contentBase64).toBe(btoa('later edit'))
  expect(hasPackVersionRisk(ctx.id)).toBe(true)
  const calls = vi.mocked(fetchJson).mock.calls.length
  load(ctx.session, ctx.id, 'draft'); expect(fetchJson).toHaveBeenCalledTimes(calls)
  expect(ctx.session.state.postSaveVerificationError).toBeTruthy()
  await expect(ctx.hook.result.current.save()).rejects.toThrow('人工核对')
  ctx.hook.unmount(); releaseCleanSession(ctx.id, ctx.session)
  expect(sessionFor(ctx.id)).toBe(ctx.session)
})
it('reports success only after the DB postflight matches', async () => {
  const ctx = await setup(); let saving!: Promise<unknown>
  act(() => { saving = ctx.hook.result.current.save() }); await started
  await act(async () => { release(); await expect(saving).resolves.toEqual({ mode: 'unified', dbSaved: true, gitSaved: true, commit: savedOid }) })
  expect(ctx.session.state.postSaveVerificationError).toBeFalsy()
})


it.each(['draft', 'current'] as const)('preserves file edits made during a confirmed %s save against the new baseline', async selected => {
  const ctx = await setup()
  const removed = { path: 'scripts/removed.sh', contentBase64: btoa('original'), mode: '100644' as const }
  snapshot = { ...snapshot, versionFiles: [...snapshot.versionFiles, removed] }
  const draft = ctx.session.state.versions.draft
  const draftFiles = [...draft.files, removed]
  updateVersion(ctx.session, 'draft', { snapshot, files: selected === 'draft' ? draftFiles : structuredClone(snapshot.versionFiles) })
  if (selected === 'current') {
    const current = { ...snapshot, baseRef: `refs/tags/deploy/${ctx.id}/#1` }
    updateVersion(ctx.session, 'current', { snapshot: current, files: draftFiles })
    publish(ctx.session, { ...ctx.session.state, version: 'current' })
  }
  let saving!: Promise<unknown>
  act(() => { saving = ctx.hook.result.current.save() }); await started
  act(() => {
    const files = ctx.session.state.versions[selected].files
      .filter(file => file.path !== removed.path)
      .map(file => file.path === 'scripts/a.sh' ? { ...file, contentBase64: btoa('later edit') } : file)
    files.push({ path: 'scripts/new.sh', contentBase64: btoa('new'), mode: '100755' })
    updateVersion(ctx.session, selected, { files })
  })
  await act(async () => { release(); await expect(saving).resolves.toEqual({ mode: 'unified', dbSaved: true, gitSaved: true, commit: savedOid }) })
  const state = ctx.session.state
  expect(state.postSaveVerificationError).toBeFalsy()
  expect(editorWorkflowWriteState(ctx.id).phase).toBe('clear')
  expect(state.versions.draft.snapshot?.baseCommit).toBe(savedOid)
  expect(state.versions.draft.snapshot?.versionFiles.find(f => f.path === 'scripts/a.sh')?.contentBase64).toBe(btoa('submitted'))
  const files = state.versions.draft.files
  expect(files.find(f => f.path === 'scripts/a.sh')?.contentBase64).toBe(btoa('later edit'))
  expect(files.find(f => f.path === removed.path)).toBeUndefined()
  expect(files.find(f => f.path === 'scripts/new.sh')).toEqual({ path: 'scripts/new.sh', contentBase64: btoa('new'), mode: '100755' })
  if (selected === 'current') {
    expect(state.version).toBe('draft')
    expect(state.versions.draft.notice).toContain('后续文件修改已迁移到草稿版')
    expect(state.versions.current.files).toEqual(state.versions.current.snapshot?.versionFiles)
    expect(state.versions.current.snapshot?.baseCommit).toBe(oid)
    expect(state.versions.current.snapshot?.draftHead).toBe(savedOid)
  }
  expect(hasPackVersionRisk(ctx.id)).toBe(true) // Later edits remain dirty, not quarantined.
  await act(async () => { await expect(ctx.hook.result.current.save()).resolves.toEqual({ mode: 'unified', dbSaved: true, gitSaved: true, commit: secondOid }) })
  const posts = vi.mocked(fetch).mock.calls.filter(([, init]) => init?.method === 'POST')
  expect(posts).toHaveLength(2)
  const second = JSON.parse(await text(posts[1][1]!.body as Blob))
  expect(second).toMatchObject({ sourceVersion: 'draft', sourceCommit: savedOid, expectedSavedHead: savedOid, expectedDraftToken: 'b'.repeat(64) })
  expect(second.changes).toEqual(expect.arrayContaining([
    { operation: 'replace', path: 'scripts/a.sh', contentBase64: btoa('later edit'), mode: '100644' },
    { operation: 'delete', path: removed.path },
    { operation: 'create', path: 'scripts/new.sh', contentBase64: btoa('new'), mode: '100755' },
  ]))
  expect(second.changes).toHaveLength(3)
  expect(ctx.session.state.versions.draft.files).toEqual(ctx.session.state.versions.draft.snapshot?.versionFiles)
  expect(ctx.session.state.versions.draft.snapshot?.baseCommit).toBe(secondOid)
})

it('keeps current selected and clean when no files changed after dispatch', async () => {
  const ctx = await setup()
  const draft = ctx.session.state.versions.draft
  updateVersion(ctx.session, 'current', { snapshot: { ...draft.snapshot!, baseRef: `refs/tags/deploy/${ctx.id}/#1` }, files: structuredClone(draft.files) })
  updateVersion(ctx.session, 'draft', { files: structuredClone(draft.snapshot!.versionFiles) })
  publish(ctx.session, { ...ctx.session.state, version: 'current' })
  let saving!: Promise<unknown>
  act(() => { saving = ctx.hook.result.current.save() }); await started
  await act(async () => { release(); await expect(saving).resolves.toMatchObject({ mode: 'unified', commit: savedOid }) })
  expect(ctx.session.state.version).toBe('current')
  expect(ctx.session.state.versions.current.files).toEqual(ctx.session.state.versions.current.snapshot?.versionFiles)
  expect(ctx.session.state.versions.draft.files).toEqual(ctx.session.state.versions.draft.snapshot?.versionFiles)
})

it('preserves independent draft edits made by a synchronous save completion subscriber', async () => {
  const ctx = await setup()
  const draft = ctx.session.state.versions.draft
  updateVersion(ctx.session, 'current', { snapshot: { ...draft.snapshot!, baseRef: `refs/tags/deploy/${ctx.id}/#1` }, files: structuredClone(draft.files) })
  updateVersion(ctx.session, 'draft', { files: structuredClone(draft.snapshot!.versionFiles) })
  publish(ctx.session, { ...ctx.session.state, version: 'current' })
  act(() => useEditorStore.getState().updateSpecField('title', 'Submitted'))
  const added = { path: 'scripts/other-draft.sh', contentBase64: btoa('independent'), mode: '100644' as const }
  let triggered = false
  const unsubscribe = useEditorStore.subscribe(state => {
    if (!triggered && state._databaseSpec?.title === 'Submitted') {
      triggered = true
      updateVersion(ctx.session, 'draft', { files: [...ctx.session.state.versions.draft.files, added] })
    }
  })
  try {
    let saving!: Promise<unknown>
    act(() => { saving = ctx.hook.result.current.save() }); await started
    await act(async () => { release(); await expect(saving).rejects.toMatchObject({ name: 'PackCanvasPartialSaveError' }) })
    expect(triggered).toBe(true)
    expect(ctx.session.state.versions.draft.files).toContainEqual(added)
    expect(ctx.session.state.versions.current.files.find(f => f.path === 'scripts/a.sh')?.contentBase64).toBe(btoa('submitted'))
    expect(editorWorkflowWriteState(ctx.id).phase).toBe('unknown')
  } finally { unsubscribe() }
})

it('does not overwrite independent draft edits introduced while saving current', async () => {
  const ctx = await setup()
  const draft = ctx.session.state.versions.draft
  updateVersion(ctx.session, 'current', { snapshot: { ...draft.snapshot!, baseRef: `refs/tags/deploy/${ctx.id}/#1` }, files: structuredClone(draft.files) })
  updateVersion(ctx.session, 'draft', { files: structuredClone(draft.snapshot!.versionFiles) })
  publish(ctx.session, { ...ctx.session.state, version: 'current' })
  let saving!: Promise<unknown>
  act(() => { saving = ctx.hook.result.current.save() }); await started
  const draftFiles = [...ctx.session.state.versions.draft.files, { path: 'scripts/independent.sh', contentBase64: btoa('draft'), mode: '100644' as const }]
  const currentFiles = ctx.session.state.versions.current.files.map(f => f.path === 'scripts/a.sh' ? { ...f, contentBase64: btoa('later') } : f)
  act(() => { updateVersion(ctx.session, 'draft', { files: draftFiles }); updateVersion(ctx.session, 'current', { files: currentFiles }) })
  await act(async () => { release(); await expect(saving).rejects.toMatchObject({ name: 'PackCanvasPartialSaveError' }) })
  expect(ctx.session.state.versions.draft.files).toEqual(draftFiles)
  expect(ctx.session.state.versions.current.files).toEqual(currentFiles)
  expect(ctx.session.state.version).toBe('current')
  expect(editorWorkflowWriteState(ctx.id).phase).toBe('unknown')
})

it.each(['draft', 'current'] as const)('retains %s later edits and keeps the write guard locked when the POST result is unknown', async selected => {
  const ctx = await setup()
  if (selected === 'current') {
    const draft = ctx.session.state.versions.draft
    updateVersion(ctx.session, 'current', { snapshot: { ...draft.snapshot!, baseRef: `refs/tags/deploy/${ctx.id}/#1` }, files: structuredClone(draft.files) })
    updateVersion(ctx.session, 'draft', { files: structuredClone(draft.snapshot!.versionFiles) })
    publish(ctx.session, { ...ctx.session.state, version: 'current' })
  }
  const originalDraft = structuredClone(ctx.session.state.versions.draft.files)
  vi.mocked(fetch).mockImplementation(async (_url, init) => {
    if (init?.method === 'GET') return new Response(JSON.stringify({ protocol: 'workflow-unified-save-v1' }))
    began(); await pending
    throw new Error('connection lost after dispatch')
  })
  let saving!: Promise<unknown>
  act(() => { saving = ctx.hook.result.current.save() }); await started
  act(() => {
    useEditorStore.getState().updateSpecField('title', 'Later canvas edit')
    const files = ctx.session.state.versions[selected].files.map(f => f.path === 'scripts/a.sh' ? { ...f, contentBase64: btoa('later edit') } : f)
    updateVersion(ctx.session, selected, { files })
  })
  await act(async () => { release(); await expect(saving).rejects.toMatchObject({ name: 'PackCanvasPartialSaveError' }) })
  expect(editorWorkflowWriteState(ctx.id).phase).toBe('unknown')
  expect(ctx.session.state.postSaveVerificationError).toContain('统一保存未能完整确认')
  expect(useEditorStore.getState().spec?.title).toBe('Later canvas edit')
  expect(useEditorStore.getState()._databaseSpec?.title).toBe('Original')
  expect(ctx.session.state.versions.draft.snapshot?.baseCommit).toBe(oid)
  expect(ctx.session.state.version).toBe(selected)
  expect(ctx.session.state.versions[selected].files.find(f => f.path === 'scripts/a.sh')?.contentBase64).toBe(btoa('later edit'))
  if (selected === 'current') expect(ctx.session.state.versions.draft.files).toEqual(originalDraft)
  await expect(ctx.hook.result.current.save()).rejects.toThrow('人工核对')
})

it('bounds a hung database postflight and preserves the manual-review lock', async () => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
  const ctx = await setup(); let saving!: Promise<unknown>
  act(() => { saving = ctx.hook.result.current.save() }); await started
  hangRead = true
  await act(async () => { release(); await postStarted })
  const rejected = expect(saving).rejects.toMatchObject({ name: 'PackCanvasPartialSaveError' })
  await act(async () => { await vi.advanceTimersByTimeAsync(UNIFIED_SAVE_TIMEOUT_MS); await rejected })
  expect(ctx.session.state.postSaveVerificationError).toContain('统一保存未能完整确认')
  expect(hasPackVersionRisk(ctx.id)).toBe(true)
})
