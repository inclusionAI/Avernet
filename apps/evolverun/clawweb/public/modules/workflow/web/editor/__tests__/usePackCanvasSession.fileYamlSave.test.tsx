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

function editYaml(ctx: Awaited<ReturnType<typeof setup>>, yaml: string) {
  act(() => updateVersion(ctx.session, 'draft', { files: ctx.session.state.versions.draft.files.map(f =>
    f.path === `workflows/${ctx.id}.yaml` ? { ...f, contentBase64: btoa(yaml) } : f) }))
}
const node = { id: 'step', title: 'Step', phase: 'work', executor: { type: 'action' } }

it('saves the file YAML as the DB and Git document, preserves script edits and updates the canvas', async () => {
  const ctx = await setup(), changed = { id: ctx.id, title: 'File change', version: '1', nodes: [node] }
  editYaml(ctx, stringify(changed))
  let saving!: Promise<unknown>
  act(() => { saving = ctx.hook.result.current.save() }); await started
  await act(async () => { release(); await expect(saving).resolves.toEqual({ mode: 'unified', dbSaved: true, gitSaved: true, commit: savedOid }) })
  const post = vi.mocked(fetch).mock.calls.find(([, init]) => init?.method === 'POST')!
  const input = JSON.parse(await text(post[1]!.body as Blob))
  expect(JSON.parse(input.specJson)).toEqual(changed)
  expect(input.changes).toEqual([{ operation: 'replace', path: 'scripts/a.sh', mode: '100644', contentBase64: btoa('submitted') }])
  expect(JSON.parse(row.specJson)).toEqual(changed)
  expect(snapshot.versionFiles.find(f => f.path === `workflows/${ctx.id}.yaml`)?.contentBase64).toBe(btoa(stringify(changed, { sortMapEntries: true, lineWidth: 0 })))
  expect(snapshot.versionFiles.find(f => f.path === 'scripts/a.sh')?.contentBase64).toBe(btoa('submitted'))
  expect(useEditorStore.getState().spec).toEqual(changed)
  expect(useEditorStore.getState()._savedSpec).toEqual(changed)
  expect(useEditorStore.getState().isDirty).toBe(false)
  expect(ctx.session.state.versions.draft.files).toEqual(ctx.session.state.versions.draft.snapshot?.versionFiles)
})

it('blocks conflicting canvas and YAML changes without discarding either draft', async () => {
  const ctx = await setup(); editYaml(ctx, stringify({ id: ctx.id, title: 'File change', version: '1', nodes: [] }))
  act(() => useEditorStore.getState().updateSpecField('title', 'Canvas change'))
  const files = structuredClone(ctx.session.state.versions.draft.files)
  await act(async () => { await expect(ctx.hook.result.current.save()).rejects.toThrow('画布和文件同时修改') })
  expect(useEditorStore.getState().spec?.title).toBe('Canvas change')
  expect(ctx.session.state.versions.draft.files).toEqual(files)
  expect(vi.mocked(fetch).mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(0)
})

it.each(['not: [valid', 'id: missing-title\nnodes: []', 'duplicate'])('rejects invalid YAML (%s) before dispatch', async yaml => {
  const ctx = await setup(); editYaml(ctx, yaml === 'duplicate' ? stringify({ id: ctx.id, title: 'Invalid', nodes: [node, node] }) : yaml)
  await act(async () => { await expect(ctx.hook.result.current.save()).rejects.toThrow() })
  expect(vi.mocked(fetch).mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(0)
  expect(useEditorStore.getState().spec?.title).toBe('Original')
})

it('quarantines an unknown result and preserves YAML and canvas drafts', async () => {
  const ctx = await setup(); editYaml(ctx, stringify({ id: ctx.id, title: 'File change', version: '1', nodes: [] }))
  const files = structuredClone(ctx.session.state.versions.draft.files)
  vi.mocked(fetch).mockImplementation(async (_url, init) => {
    if (init?.method === 'GET') return new Response(JSON.stringify({ protocol: 'workflow-unified-save-v1' }))
    throw new Error('connection lost after dispatch')
  })
  await act(async () => { await expect(ctx.hook.result.current.save()).rejects.toMatchObject({ name: 'PackCanvasPartialSaveError' }) })
  expect(editorWorkflowWriteState(ctx.id).phase).toBe('unknown')
  expect(ctx.session.state.versions.draft.files).toEqual(files)
  expect(useEditorStore.getState().spec?.title).toBe('Original')
  expect(useEditorStore.getState()._savedSpec?.title).toBe('Original')
  await expect(ctx.hook.result.current.save()).rejects.toThrow()
})

it('locks while retaining a concurrent canvas edit and newly saved YAML node', async () => {
  const ctx = await setup(); editYaml(ctx, stringify({ id: ctx.id, title: 'File change', version: '1', nodes: [node] }))
  let saving!: Promise<unknown>
  act(() => { saving = ctx.hook.result.current.save() }); await started
  act(() => useEditorStore.getState().updateSpecField('title', 'Later canvas change'))
  await act(async () => { release(); await expect(saving).rejects.toThrow('文件 YAML 已写入，但保存期间画布或布局发生变化') })
  expect(useEditorStore.getState().spec?.title).toBe('Later canvas change')
  expect(useEditorStore.getState()._savedSpec?.title).toBe('Original')
  expect(atob(ctx.session.state.versions.draft.files.find(f => f.path === `workflows/${ctx.id}.yaml`)!.contentBase64)).toContain('id: step')
  expect(editorWorkflowWriteState(ctx.id).phase).toBe('unknown')
  await expect(ctx.hook.result.current.save()).rejects.toThrow('人工核对')
  expect(postCount).toBe(1)
})

it('can save a second YAML-only edit after automatic layout for a new saved node', async () => {
  const ctx = await setup(), first = { id: ctx.id, title: 'First', version: '1', nodes: [node] }
  editYaml(ctx, stringify(first)); let saving!: Promise<unknown>
  act(() => { saving = ctx.hook.result.current.save() }); await started
  await act(async () => { release(); await expect(saving).resolves.toMatchObject({ mode: 'unified', commit: savedOid }) })
  expect(useEditorStore.getState().spec).toEqual(first)
  expect(useEditorStore.getState().isLayoutDirty).toBe(false)
  const positions = structuredClone(useEditorStore.getState().positions)
  const second = { ...first, title: 'Second' }
  editYaml(ctx, stringify(second))
  await act(async () => { await expect(ctx.hook.result.current.save()).resolves.toMatchObject({ mode: 'unified', commit: secondOid }) })
  expect(JSON.parse(row.specJson)).toEqual(second); expect(useEditorStore.getState().spec).toEqual(second)
  expect(useEditorStore.getState().positions).toEqual(positions); expect(useEditorStore.getState().isLayoutDirty).toBe(false)
  expect(snapshot.versionFiles.find(f => f.path === 'scripts/a.sh')?.contentBase64).toBe(btoa('submitted'))
  expect(postCount).toBe(2)
})

it('retains a real drag after YAML adoption and blocks divergent file-only save', async () => {
  const ctx = await setup(), first = { id: ctx.id, title: 'First', version: '1', nodes: [node] }
  editYaml(ctx, stringify(first)); let saving!: Promise<unknown>
  act(() => { saving = ctx.hook.result.current.save() }); await started
  await act(async () => { release(); await saving })
  act(() => useEditorStore.getState().moveNode('step', 701, 303))
  editYaml(ctx, stringify({ ...first, title: 'Second' }))
  await act(async () => { await expect(ctx.hook.result.current.save()).rejects.toThrow('画布和文件同时修改') })
  expect(useEditorStore.getState().positions.step).toEqual({ x: 701, y: 303 })
  expect(useEditorStore.getState().isLayoutDirty).toBe(true); expect(postCount).toBe(1)
})
