import { act, cleanup, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { stringify } from 'yaml'
import { createElement } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useEditorWorkflowDocument } from '../../api/useEditorWorkflowDocument'
import { api, fetchJson } from '@avernet/clawweb-shared/web/api/client'
import { readEditorWorkflowSource } from '../../api/editorWorkflowSource'
import { useEditorStore } from '../store'
import { beginWorkflowTransition, commitWorkflowTransition } from '../workflowTransition'
import { saveWorkflowCanvasDocument } from '../workflowDocumentSave'
import { usePackCanvasSession } from '../usePackCanvasSession'
import { publish, sessionFor, updateVersion } from '../packVersionSession'

vi.mock('@avernet/clawweb-shared/web/api/client', () => ({ api: { workflows: { get: vi.fn() } }, fetchJson: vi.fn() }))
const token = 'a'.repeat(64), next = 'b'.repeat(64), oid = 'c'.repeat(40), savedOid = 'd'.repeat(40)
let counter = 0, id: string
let spec: { id: string; title: string; version: string; nodes: never[] }
let savedSnapshot: ReturnType<typeof snapshotFor> | undefined
let row: { workflowId: string; packId: string | null; specJson: string; draftToken: string }
let beforePut: (() => void) | undefined
const bodyText = (body: Blob) => new Promise<string>(resolve => { const r = new FileReader(); r.onload = () => resolve(String(r.result)); r.readAsText(body) })
const store = () => useEditorStore.getState()
beforeEach(() => {
  id = `binding-flow-${++counter}`
  spec = { id, title: 'Original', version: '1', nodes: [] }; savedSnapshot = undefined
  vi.spyOn(window, 'confirm').mockReturnValue(true)
  row = { workflowId: id, packId: 'pack-a', specJson: JSON.stringify(spec), draftToken: token }
  beforePut = undefined
  store().reset({ force: true })
  vi.mocked(api.workflows.get).mockResolvedValue(spec as never)
  vi.mocked(fetchJson).mockImplementation(async (url, init) => {
    if (url.endsWith('/editor-draft')) return structuredClone(row) as never
    if (url.endsWith('/pack-version?version=draft') && savedSnapshot) return structuredClone(savedSnapshot) as never
    const body = JSON.parse(await bodyText(init!.body as Blob))
    return { branch: `wf/${id}`, commit: oid, packId: 'pack-a', requestId: body.requestId, version: 1 } as never
  })
  vi.stubGlobal('fetch', vi.fn(async (url: string, init: RequestInit) => {
    if (url.endsWith('/unified-save') && init.method === 'GET') return new Response(JSON.stringify({ protocol: 'workflow-unified-save-v1' }))
    const body = JSON.parse(await bodyText(init.body as Blob))
    beforePut?.()
    if (url.endsWith('/unified-save')) {
      if (body.expectedDraftToken !== row.draftToken) return new Response('{}', { status: 409 })
      row = { ...row, specJson: body.specJson, draftToken: next }
      savedSnapshot = snapshotFor(body.specJson)
      return new Response(JSON.stringify({ requestId: body.requestId, outcome: 'confirmed', reconciliationRequired: false, evidence: { db: 'confirmed', git: 'confirmed', commit: savedOid, draftToken: next, inputHash: 'e'.repeat(64) } }))
    }
    if (body.expectedDraftToken !== row.draftToken) return new Response(JSON.stringify({ code: 'DRAFT_CONFLICT' }), { status: 409 })
    row = { ...row, specJson: JSON.stringify(body.spec), draftToken: next }
    return new Response(JSON.stringify(row))
  }))
})
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.resetAllMocks(); vi.unstubAllGlobals() })
async function open() {
  const source = await readEditorWorkflowSource(id)
  store().loadSpec(source.spec, source.databaseSpec, source.activeDeployNumber)
  return source
}
function snapshotFor(specJson: string) {
  const source = sessionFor(id).state.versions.draft
  return { ...source.snapshot!, baseCommit: savedOid, draftHead: savedOid,
    versionFiles: source.files.map(f => f.path === `workflows/${id}.yaml`
      ? { ...f, contentBase64: btoa(stringify(JSON.parse(specJson), { sortMapEntries: true, lineWidth: 0 })) } : { ...f }) }
}
function pack(packId = 'pack-a') {
  const files = [
    { path: 'workflow.pack.yaml', contentBase64: btoa(stringify({ id: packId, workflows: [id] })), mode: '100644' as const },
    { path: `workflows/${id}.yaml`, contentBase64: btoa(stringify(spec)), mode: '100644' as const },
    { path: 'scripts/a.sh', contentBase64: btoa('old'), mode: '100644' as const },
  ]
  const snapshot = { workflowId: id, packId, baseRef: `origin/wf/${id}`, baseCommit: oid, draftHead: oid, canWrite: true, versionFiles: files }
  const session = sessionFor(id)
  publish(session, { ...session.state, version: 'draft', versions: { ...session.state.versions, draft: { snapshot, files: structuredClone(files), loading: false, error: null, blocked: false, notice: null } } })
  updateVersion(session, 'draft', { files: files.map(f => f.path === 'scripts/a.sh' ? { ...f, contentBase64: btoa('new') } : f) })
  return { session, hook: renderHook(() => usePackCanvasSession(id)) }
}
function drift(packId: string | null) { row = { ...row, packId, draftToken: next } }

it.each([['pack-a', 'pack-b'], [null, 'workflow-id'], ['workflow-id', null]] as const)('rejects raw binding change %s to %s even with identical spec', async (original, changed) => {
  row.packId = original === 'workflow-id' ? id : original
  await open(); expect(store()._databasePackId).toBe(row.packId)
  store().updateSpecField('title', 'Local'); drift(changed === 'workflow-id' ? id : changed)
  await expect(saveWorkflowCanvasDocument(id)).rejects.toThrow('Pack 身份')
  expect(fetch).not.toHaveBeenCalled(); expect(store().isDirty).toBe(true)
})
it('uses the original row identity after a workflow transition', async () => {
  const source = await readEditorWorkflowSource(id)
  const approval = beginWorkflowTransition()!
  expect(commitWorkflowTransition(approval, source.spec, id, false, source.databaseSpec, null)).toBe(true)
  expect(store()._databasePackId).toBe('pack-a')
  store().updateSpecField('title', 'Local'); drift('pack-b')
  await expect(saveWorkflowCanvasDocument(id)).rejects.toThrow('Pack 身份')
  expect(fetch).not.toHaveBeenCalled()
})
it('saves with a matching binding and token without changing raw pack_id', async () => {
  await open(); store().updateSpecField('title', 'Local')
  expect(await saveWorkflowCanvasDocument(id)).toEqual({ dbSaved: true, commit: null })
  expect(row.packId).toBe('pack-a'); expect(store()._databasePackId).toBe('pack-a')
})
it('relies on CAS to reject a binding change after the preflight GET', async () => {
  await open(); store().updateSpecField('title', 'Local'); beforePut = () => drift('pack-b')
  await expect(saveWorkflowCanvasDocument(id)).rejects.toThrow('冲突')
  expect(fetch).toHaveBeenCalledTimes(1); expect(JSON.parse(row.specJson)).toEqual(spec); expect(store().isDirty).toBe(true)
})
it('fails closed when a session was loaded without same-row identity evidence', async () => {
  store().loadSpec(spec); store().updateSpecField('title', 'Local')
  await expect(saveWorkflowCanvasDocument(id)).rejects.toThrow('缺少可信')
  expect(fetchJson).not.toHaveBeenCalled(); expect(fetch).not.toHaveBeenCalled()
})
it('refuses a Git-only file save after database binding drift', async () => {
  await open(); const ctx = pack(); drift('pack-b')
  await act(async () => { await expect(ctx.hook.result.current.save()).rejects.toThrow('Pack 身份') })
  expect(vi.mocked(fetchJson).mock.calls.every(([url]) => url.endsWith('/editor-draft'))).toBe(true)
  expect(ctx.session.state.versions.draft.files.find(f => f.path === 'scripts/a.sh')?.contentBase64).toBe(btoa('new'))
})
it('refuses Git snapshot B even if DB subsequently changed from original A to B', async () => {
  await open(); drift('pack-b'); const ctx = pack('pack-b')
  await act(async () => { await expect(ctx.hook.result.current.save()).rejects.toThrow('数据库 Pack 身份不一致') })
  expect(vi.mocked(fetchJson).mock.calls.filter(([url]) => !url.endsWith('/editor-draft'))).toHaveLength(0)
})
it('rejects a binding change observed after the unified save before adopting the snapshot', async () => {
  await open(); store().updateSpecField('title', 'Local'); const ctx = pack()
  let gets = 0
  vi.mocked(fetchJson).mockImplementation(async (url) => {
    if (url.endsWith('/pack-version?version=draft')) return structuredClone(savedSnapshot) as never
    if (!url.endsWith('/editor-draft')) throw new Error('unexpected read')
    if (++gets === 2) drift('pack-b')
    return structuredClone(row) as never
  })
  await act(async () => { await expect(ctx.hook.result.current.save()).rejects.toMatchObject({ name: 'PackCanvasPartialSaveError' }) })
  expect(gets).toBe(2); expect(row.packId).toBe('pack-b')
  expect(ctx.session.state.versions.draft.files.find(f => f.path === 'scripts/a.sh')?.contentBase64).toBe(btoa('new'))
})
it('saves a Git-only change after verifying the original binding', async () => {
  await open(); const ctx = pack()
  await act(async () => { expect(await ctx.hook.result.current.save()).toEqual({ mode: 'unified', dbSaved: true, gitSaved: true, commit: savedOid }) })
  expect(vi.mocked(fetchJson).mock.calls.filter(([url]) => !url.endsWith('/editor-draft'))).toHaveLength(1)
})
it('saves DB and Git when both preflights match the original binding', async () => {
  await open(); store().updateSpecField('title', 'Local'); const ctx = pack()
  await act(async () => { expect(await ctx.hook.result.current.save()).toEqual({ mode: 'unified', dbSaved: true, gitSaved: true, commit: savedOid }) })
  expect(vi.mocked(fetchJson).mock.calls.filter(([url]) => !url.endsWith('/editor-draft'))).toHaveLength(1)
})
it('rejects an identity change in session while the DB preflight is pending', async () => {
  await open(); store().updateSpecField('title', 'Local')
  let resolve!: (value: unknown) => void
  vi.mocked(fetchJson).mockReturnValueOnce(new Promise(r => { resolve = r }) as never)
  const saving = saveWorkflowCanvasDocument(id)
  useEditorStore.setState({ _databasePackId: 'pack-b' })
  resolve(structuredClone(row))
  await expect(saving).rejects.toThrow('会话已变化')
  expect(fetch).not.toHaveBeenCalled()
})
it('does not associate a changed document with the old CAS identity', async () => {
  const source = await readEditorWorkflowSource(id)
  source.databaseSpec!.title = 'Mutated'
  store().loadSpec(source.spec, source.databaseSpec, source.activeDeployNumber)
  expect(store()._databasePackId).toBeUndefined()
  store().updateSpecField('title', 'Local')
  await expect(saveWorkflowCanvasDocument(id)).rejects.toThrow('缺少可信')
})
it('fails closed on an active-deployment first draft with no row identity', async () => {
  store().loadSpec(spec, null, 3); store().updateSpecField('title', 'Local')
  await expect(saveWorkflowCanvasDocument(id)).rejects.toThrow('缺少可信')
  expect(fetchJson).not.toHaveBeenCalled(); expect(fetch).not.toHaveBeenCalled()
})

it('does not let query structural sharing mix unchanged content with a new row binding', async () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const wrapper = ({ children }: { children: React.ReactNode }) => createElement(QueryClientProvider, { client }, children)
  const hook = renderHook(() => useEditorWorkflowDocument(id), { wrapper })
  await waitFor(() => expect(hook.result.current.databaseSpec).toBeDefined())
  const previous = hook.result.current.databaseSpec
  drift('pack-b')
  await act(async () => { await client.invalidateQueries({ queryKey: ['editor-workflow-document', id] }) })
  await waitFor(() => expect(hook.result.current.databaseSpec).not.toBe(previous))
  store().loadSpec(hook.result.current.data!, hook.result.current.databaseSpec, hook.result.current.activeDeployNumber)
  expect(store()._databasePackId).toBe('pack-b')
  client.clear()
})
