import { act, cleanup, renderHook } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { stringify } from 'yaml'
import { fetchJson } from '@avernet/clawweb-shared/web/api/client'
import { useEditorStore } from '../store'
import { sessionFor, publish, updateVersion } from '../packVersionSession'
import { usePackCanvasSession } from '../usePackCanvasSession'

const mocks = vi.hoisted(() => ({ read: vi.fn() }))
vi.mock('../../api/workflowDocument', async importOriginal => ({ ...(await importOriginal<typeof import('../../api/workflowDocument')>()), readWorkflowDocument: mocks.read }))
vi.mock('@avernet/clawweb-shared/web/api/client', () => ({ fetchJson: vi.fn() }))
const A = 'a'.repeat(40), B = 'b'.repeat(40), destination = 'flows/moved.yaml'
const token = 'a'.repeat(64), nextToken = 'b'.repeat(64)
const row = (spec: { id: string }, draftToken = token, packId = 'pack') => ({ workflowId: spec.id, packId, specJson: JSON.stringify(spec), draftToken })
let sequence = 0
const blobText = (blob: Blob) => new Promise<string>(resolve => { const r = new FileReader(); r.onload = () => resolve(String(r.result)); r.readAsText(blob) })
function setup() {
  const id = `move-canvas-${++sequence}`, spec = { id, title: 'Initial', version: '1', nodes: [] }
  useEditorStore.getState().reset({ force: true }); useEditorStore.getState().loadSpec(spec); useEditorStore.setState({ _databasePackId: 'pack' })
  const path = `workflows/${id}.yaml`, baseline = btoa(`# Keep exact bytes and comments\n${stringify(spec)}`)
  const files = [{ path: 'workflow.pack.yaml', contentBase64: btoa(stringify({ id: 'pack', workflows: [id] })), mode: '100644' as const },
    { path, contentBase64: baseline, mode: '100644' as const }, { path: 'scripts/test.sh', contentBase64: btoa('old'), mode: '100644' as const }]
  const snapshot = { workflowId: id, packId: 'pack', baseRef: `origin/wf/${id}`, baseCommit: A, draftHead: A, canWrite: true, versionFiles: files }
  const session = sessionFor(id)
  publish(session, { ...session.state, version: 'draft', versions: { ...session.state.versions,
    draft: { snapshot, files: structuredClone(files), loading: false, error: null, blocked: false, notice: null } } })
  mocks.read.mockResolvedValue(structuredClone(spec))
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => url.endsWith('/unified-save') && init?.method === 'GET'
    ? new Response(JSON.stringify({ protocol: 'workflow-unified-save-v1' })) : new Response(JSON.stringify(await fetchJson(url, init)))))
  vi.mocked(fetchJson).mockImplementation(async (url, init) => {
    if (url.endsWith('/editor-draft') && init?.method === 'GET') return row(await mocks.read())
    const input = JSON.parse(await blobText(init!.body as Blob))
    if (url.endsWith('/editor-draft')) { expect(input.expectedDraftToken).toBe(token); mocks.read.mockResolvedValue(structuredClone(input.spec)); return row(input.spec, nextToken) }
    if (url === '/api/auto-heal/save') { mocks.read.mockResolvedValue(structuredClone(input.spec)); return { ok: true, saved: true, workflowId: id, packId: 'pack' } }
    return { branch: `wf/${id}`, commit: B, packId: 'pack', version: 1, requestId: input.requestId }
  })
  return { id, spec, path, baseline, session, hook: renderHook(() => usePackCanvasSession(id, spec)) }
}
function move(ctx: ReturnType<typeof setup>, content = ctx.baseline, keepOld = false, packId = 'pack') {
  const state = ctx.session.state.versions.draft, old = state.files.find(file => file.path === ctx.path)!
  updateVersion(ctx.session, 'draft', { files: [...state.files.filter(file => keepOld || file.path !== ctx.path).map(file =>
    file.path === 'workflow.pack.yaml' ? { ...file, contentBase64: btoa(stringify({ id: packId, workflows: [{ id: ctx.id, file: destination }] })) } : file),
  { ...old, path: destination, mode: '100755', contentBase64: content }] })
}
afterEach(() => { cleanup(); vi.resetAllMocks(); vi.restoreAllMocks(); vi.unstubAllGlobals() })

it('refuses a path move and preserves exact baseline bytes and mode', async () => {
  const ctx = setup(); act(() => move(ctx))
  await expectRenameRefused(ctx)
  expect(ctx.session.state.versions.draft.files.find(f => f.path === destination)).toMatchObject({ contentBase64: ctx.baseline, mode: '100755' })
})
it('refuses an edited YAML move without losing the simultaneous script edit', async () => {
  const ctx = setup(), edited = { ...ctx.spec, title: 'Edited YAML' }
  act(() => { move(ctx, btoa(stringify(edited))); const v = ctx.session.state.versions.draft
    updateVersion(ctx.session, 'draft', { files: v.files.map(f => f.path === 'scripts/test.sh' ? { ...f, contentBase64: btoa('new') } : f) }) })
  await expectRenameRefused(ctx)
  expect(ctx.session.state.versions.draft.files.find(f => f.path === destination)?.contentBase64).toBe(btoa(stringify(edited)))
  expect(ctx.session.state.versions.draft.files.find(f => f.path === 'scripts/test.sh')?.contentBase64).toBe(btoa('new'))
})
it('preserves a canvas edit when the workflow file is moved', async () => {
  const ctx = setup(); act(() => { move(ctx); useEditorStore.getState().updateSpecField('title', 'Canvas edit') })
  await expectRenameRefused(ctx)
  expect(useEditorStore.getState().spec?.title).toBe('Canvas edit'); expect(useEditorStore.getState().isDirty).toBe(true)
})
it('retains both drafts when canvas and moved YAML disagree', async () => {
  const ctx = setup(); act(() => { move(ctx, btoa(stringify({ ...ctx.spec, title: 'File' }))); useEditorStore.getState().updateSpecField('title', 'Canvas') })
  await expectRenameRefused(ctx)
  expect(useEditorStore.getState().spec?.title).toBe('Canvas')
  expect(ctx.session.state.versions.draft.files.find(f => f.path === destination)?.contentBase64).toBe(btoa(stringify({ ...ctx.spec, title: 'File' })))
})
it('retains moved YAML without attempting a failing database read', async () => {
  const ctx = setup(), edited = btoa(stringify({ ...ctx.spec, title: 'File' }))
  act(() => move(ctx, edited)); vi.mocked(fetchJson).mockRejectedValueOnce(new Error('offline'))
  await expectRenameRefused(ctx)
  expect(ctx.session.state.versions.draft.files.find(f => f.path === destination)?.contentBase64).toBe(edited)
})
it('does not attempt a Git write for a structural candidate even if Git would fail', async () => {
  const ctx = setup(), edited = { ...ctx.spec, title: 'File' }
  act(() => move(ctx, btoa(stringify(edited))))
  vi.mocked(fetchJson).mockRejectedValue(new Error('lost'))
  await expectRenameRefused(ctx)
  expect(ctx.session.state.versions.draft.files.find(f => f.path === destination)?.contentBase64).toBe(btoa(stringify(edited)))
})
it('retains subsequent edits across repeated rejected structural saves', async () => {
  const ctx = setup(), edited = { ...ctx.spec, title: 'File' }
  act(() => move(ctx, btoa(stringify(edited))))
  await expectRenameRefused(ctx)
  act(() => { const v = ctx.session.state.versions.draft; updateVersion(ctx.session, 'draft', { files: v.files.map(f => f.path === 'scripts/test.sh' ? { ...f, contentBase64: btoa('later') } : f) }) })
  await expectRenameRefused(ctx)
  expect(ctx.session.state.versions.draft.files.find(f => f.path === 'scripts/test.sh')?.contentBase64).toBe(btoa('later'))
})
it('fails closed on an edited retained old YAML path', async () => {
  const ctx = setup(); act(() => { move(ctx, ctx.baseline, true); const v = ctx.session.state.versions.draft
    updateVersion(ctx.session, 'draft', { files: v.files.map(f => f.path === ctx.path ? { ...f, contentBase64: btoa('edited old file') } : f) }) })
  await expectRenameRefused(ctx)
  expect(ctx.session.state.versions.draft.files.find(f => f.path === ctx.path)?.contentBase64).toBe(btoa('edited old file'))
})
it('refuses an existing destination and preserves both workflow files', async () => {
  const ctx = setup(), edited = { ...ctx.spec, title: 'Existing target' }
  act(() => { move(ctx, btoa(stringify(edited)), true); const v = ctx.session.state.versions.draft
    const target = v.files.find(f => f.path === destination)!
    updateVersion(ctx.session, 'draft', { snapshot: { ...v.snapshot!, versionFiles: [...v.snapshot!.versionFiles, target] } }) })
  await expectRenameRefused(ctx)
  expect(ctx.session.state.versions.draft.files.find(f => f.path === ctx.path)?.contentBase64).toBe(ctx.baseline)
  expect(ctx.session.state.versions.draft.files.find(f => f.path === destination)?.contentBase64).toBe(btoa(stringify(edited)))
})
it('rejects an unsupported move with a mismatched destination identity before writes', async () => {
  const ctx = setup(); act(() => move(ctx, btoa(stringify({ ...ctx.spec, id: 'different' }))))
  await act(async () => { await expect(ctx.hook.result.current.save()).rejects.toThrow('Pack manifest') })
  expect(fetchJson).not.toHaveBeenCalled(); expect(vi.mocked(fetch).mock.calls.every(([, init]) => init?.method === 'GET')).toBe(true)
})
it('does not send a moved candidate even if the database transport would be lost', async () => {
  const ctx = setup(); act(() => move(ctx, btoa(stringify({ ...ctx.spec, title: 'File' }))))
  vi.mocked(fetchJson).mockRejectedValueOnce(new Error('connection lost'))
  await expectRenameRefused(ctx)
})
it('does not read or overwrite a changed DB Pack identity for an unsupported move', async () => {
  const ctx = setup(); act(() => move(ctx, btoa(stringify({ ...ctx.spec, title: 'File' }))))
  vi.mocked(fetchJson).mockResolvedValueOnce(row(ctx.spec, token, 'other-pack'))
  await expectRenameRefused(ctx)
  expect(ctx.session.state.versions.draft.files.find(f => f.path === destination)?.contentBase64).toBe(btoa(stringify({ ...ctx.spec, title: 'File' })))
})

/** Identity writes are temporarily unavailable. Assert both transport layers,
 * the immutable DB baseline and the complete local candidate remain untouched. */
async function expectRenameRefused(ctx: ReturnType<typeof setup>) {
  const candidate = structuredClone(ctx.session.state.versions.draft.files)
  const snapshot = structuredClone(ctx.session.state.versions.draft.snapshot)
  const canvas = structuredClone(useEditorStore.getState().spec)
  const database = structuredClone(useEditorStore.getState()._databaseSpec)
  await act(async () => { await expect(ctx.hook.result.current.save()).rejects.toThrow('暂不支持') })
  expect(fetchJson).not.toHaveBeenCalled(); expect(vi.mocked(fetch).mock.calls.every(([, init]) => init?.method === 'GET')).toBe(true)
  expect(mocks.read).not.toHaveBeenCalled()
  expect(ctx.session.state.versions.draft.files).toEqual(candidate)
  expect(ctx.session.state.versions.draft.snapshot).toEqual(snapshot)
  expect(useEditorStore.getState().spec).toEqual(canvas)
  expect(useEditorStore.getState()._databaseSpec).toEqual(database)
  expect(ctx.session.state.uncertainRename).toBeNull()
  expect(ctx.session.state.reconciliation).toBeNull()
  expect(ctx.session.state.versions.draft.blocked).toBe(false)
}
it('refuses identity and path changes while retaining the full edited candidate inventory', async () => {
  const ctx = setup()
  const asset = { path: 'assets/image.bin', contentBase64: btoa('\u0000\u00ff'), mode: '100644' as const }
  act(() => { move(ctx, ctx.baseline, false, 'renamed'); const v = ctx.session.state.versions.draft
    updateVersion(ctx.session, 'draft', { files: [...v.files.map(f => f.path === 'scripts/test.sh' ? { ...f, contentBase64: btoa('new') } : f), asset] }) })
  await expectRenameRefused(ctx)
  expect(ctx.session.state.versions.draft.files).toContainEqual(asset)
  expect(ctx.session.state.versions.draft.files.find(f => f.path === destination)).toMatchObject({ contentBase64: ctx.baseline, mode: '100755' })
  expect(ctx.session.state.versions.draft.files.find(f => f.path === 'scripts/test.sh')?.contentBase64).toBe(btoa('new'))
  expect(ctx.session.state.versions.draft.snapshot?.packId).toBe('pack')
})
it('refuses an identity-only change without moving or losing the workflow', async () => {
  const ctx = setup()
  act(() => { const v = ctx.session.state.versions.draft
    updateVersion(ctx.session, 'draft', { files: v.files.map(f => f.path === 'workflow.pack.yaml'
      ? { ...f, contentBase64: btoa(stringify({ id: 'renamed', workflows: [ctx.id] })) } : f) }) })
  await expectRenameRefused(ctx)
  expect(ctx.session.state.versions.draft.files.find(f => f.path === ctx.path)?.contentBase64).toBe(ctx.baseline)
})
it('refuses identity and path changes with an edited YAML and preserves both drafts', async () => {
  const ctx = setup(), edited = { ...ctx.spec, title: 'Edited YAML' }
  act(() => move(ctx, btoa(stringify(edited)), false, 'renamed'))
  await expectRenameRefused(ctx)
  expect(ctx.session.state.versions.draft.files.find(f => f.path === destination)?.contentBase64).toBe(btoa(stringify(edited)))
  expect(useEditorStore.getState().spec).toEqual(ctx.spec)
})
it.each(['lost DB response', 'wrong DB identity', 'lost Git response', 'unreadable DB response'] as const)(
  'never reaches transport or creates an uncertainty lock even if %s would occur', async kind => {
    const ctx = setup(); act(() => move(ctx, ctx.baseline, false, 'renamed'))
    vi.mocked(fetchJson).mockImplementation(async () => {
      if (kind === 'wrong DB identity') return { ok: true, saved: true, workflowId: ctx.id, packId: 'wrong' }
      throw new Error(kind)
    })
    await expectRenameRefused(ctx)
    // Repeating this local refusal sends nothing, retains the candidate and is
    // not mistaken for an attempted remote rename that requires reconciliation.
    await expectRenameRefused(ctx)
  },
)
it('refuses an identity change before a DB baseline read even if that read would fail', async () => {
  const ctx = setup(); act(() => move(ctx, ctx.baseline, false, 'renamed'))
  mocks.read.mockRejectedValueOnce(new Error('offline'))
  await expectRenameRefused(ctx)
  await expectRenameRefused(ctx)
})
it('blocks a conflicting rename before either DB or Git is written', async () => {
  const ctx = setup()
  act(() => {
    const draft = ctx.session.state.versions.draft
    publish(ctx.session, { ...ctx.session.state, version: 'current', versions: { ...ctx.session.state.versions,
      current: { ...draft, snapshot: { ...draft.snapshot!, baseRef: `refs/tags/deploy/${ctx.id}/#1`, draftHead: B }, files: structuredClone(draft.files) } } })
    const current = ctx.session.state.versions.current
    updateVersion(ctx.session, 'current', { files: current.files.map(f => f.path === 'workflow.pack.yaml'
      ? { ...f, contentBase64: btoa(stringify({ id: 'renamed', workflows: [ctx.id] })) } : f) })
  })
  const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
  await act(async () => { await expect(ctx.hook.result.current.save()).rejects.toThrow('切换到草稿') })
  expect(confirm).not.toHaveBeenCalled()
  expect(fetchJson).not.toHaveBeenCalled()
  expect(ctx.session.state.uncertainRename).toBeNull()
  expect(ctx.session.state.versions.current.files.find(f => f.path === 'workflow.pack.yaml')?.contentBase64).toBe(btoa(stringify({ id: 'renamed', workflows: [ctx.id] })))
})
it('preserves unrelated workflows and binary resources when a Pack rename is refused', async () => {
  const ctx = setup()
  const other = { path: 'workflows/other.yaml', contentBase64: btoa('id: other\ntitle: Other\n'), mode: '100644' as const }
  const asset = { path: 'assets/keep.dat', contentBase64: btoa('\u0000\u00ff'), mode: '100755' as const }
  act(() => {
    const v = ctx.session.state.versions.draft
    const base = [...v.files.map(f => f.path === 'workflow.pack.yaml' ? { ...f, contentBase64: btoa(stringify({ id: 'pack', workflows: [ctx.id, 'other'] })) } : f), other, asset]
    updateVersion(ctx.session, 'draft', { snapshot: { ...v.snapshot!, versionFiles: base }, files: structuredClone(base) })
    const updated = ctx.session.state.versions.draft
    updateVersion(ctx.session, 'draft', { files: updated.files.map(f => f.path === 'workflow.pack.yaml'
      ? { ...f, contentBase64: btoa(stringify({ id: 'renamed', workflows: [ctx.id, 'other'] })) } : f) })
  })
  await expectRenameRefused(ctx)
  expect(ctx.session.state.versions.draft.files).toContainEqual(other)
  expect(ctx.session.state.versions.draft.files).toContainEqual(asset)
  expect(ctx.session.state.versions.draft.snapshot!.versionFiles).toContainEqual(other)
  expect(ctx.session.state.versions.draft.snapshot!.versionFiles).toContainEqual(asset)
})

it('blocks a different 草稿(wf) head before writing either DB or Git and keeps both local edits', async () => {
  const ctx = setup()
  act(() => {
    const draft = ctx.session.state.versions.draft
    const snapshot = { ...draft.snapshot!, baseRef: `refs/tags/deploy/${ctx.id}/#1`, draftHead: B }
    const files = draft.files.map(f => f.path === 'scripts/test.sh' ? { ...f, contentBase64: btoa('local current') } : f)
    publish(ctx.session, { ...ctx.session.state, version: 'current', versions: { ...ctx.session.state.versions,
      current: { ...draft, snapshot, files } } })
    useEditorStore.getState().updateSpecField('title', 'local canvas')
  })
  await act(async () => { await expect(ctx.hook.result.current.save()).rejects.toThrow('切换到草稿') })
  expect(fetchJson).not.toHaveBeenCalled()
  expect(useEditorStore.getState().spec?.title).toBe('local canvas')
  expect(ctx.session.state.versions.current.files.find(f => f.path === 'scripts/test.sh')?.contentBase64).toBe(btoa('local current'))
})
