import { act, cleanup, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { stringify } from 'yaml'
import { api, fetchJson } from '@avernet/clawweb-shared/web/api/client'
import { readEditorWorkflowSource } from '../editorWorkflowSource'
import { useEditorStore } from '../../editor/store'
import { sessionFor, publish, updateVersion } from '../../editor/packVersionSession'
import { usePackCanvasSession } from '../../editor/usePackCanvasSession'
import { editorWorkflowWriteState } from '../../editor/workflowWriteGuard'

vi.mock('@avernet/clawweb-shared/web/api/client', () => ({ api: { workflows: { get: vi.fn() } }, fetchJson: vi.fn() }))
const old = 'a'.repeat(40), commit = 'b'.repeat(40), token = 'c'.repeat(64), newToken = 'd'.repeat(64)
const blobText = (body: Blob) => new Promise<string>(resolve => { const r = new FileReader(); r.onload = () => resolve(String(r.result)); r.readAsText(body) })
let counter = 0
let row: { workflowId: string; packId: null; specJson: string; draftToken: string }
let submitted: any, snapshot: any, post: ReturnType<typeof vi.fn>
let responseCommit: string
let scenario: 'ok' | 'partial' | 'stale-commit' | 'stale-token' | 'stale-yaml' | 'missing-file' | 'denied' | 'rejected' | 'offline' | 'concurrent' | 'default-host' | 'cap-invalid' | 'cap-down'
const encode = (s: string) => btoa(unescape(encodeURIComponent(s)))
beforeEach(() => {
  scenario = 'ok'; submitted = undefined; responseCommit = commit
  vi.spyOn(window, 'confirm').mockReturnValue(true)
  useEditorStore.getState().reset({ force: true })
  vi.mocked(api.workflows.get).mockImplementation(async () => JSON.parse(row.specJson) as never)
  vi.mocked(fetchJson).mockImplementation(async url => {
    if (url.endsWith('/editor-draft')) return structuredClone(row) as never
    if (url.endsWith('/pack-version?version=draft')) return structuredClone(snapshot) as never
    throw new Error(`Unexpected GET ${url}`)
  })
  post = vi.fn(async (_url: string, init: RequestInit) => {
    if (init.method === 'GET') {
      if (scenario === 'default-host') return new Response('{}', { status: 401 })
      if (scenario === 'cap-down') return new Response('{}', { status: 503 })
      if (scenario === 'cap-invalid') return new Response(JSON.stringify({ protocol: 'workflow-unified-save-v0' }))
      return { status: 200, json: async () => ({ protocol: 'workflow-unified-save-v1' }) }
    }
    if (init.method === 'PUT') {
      const input = JSON.parse(await blobText(init.body as Blob))
      row = { ...row, specJson: JSON.stringify(input.spec), draftToken: newToken }
      return new Response(JSON.stringify(row))
    }
    submitted = JSON.parse(await blobText(init.body as Blob))
    if (scenario === 'offline') throw new Error('connection lost')
    if (scenario === 'rejected') return { status: 409, json: async () => ({ requestId: submitted.requestId, outcome: 'rejected', evidence: { db: 'not_started', git: 'not_started' }, reconciliationRequired: false }) }
    if (scenario === 'denied') return { status: 401, json: async () => ({ code: 'AUTHENTICATION_REQUIRED' }) }
    if (scenario === 'partial') return { status: 409, json: async () => ({ requestId: submitted.requestId, outcome: 'partial', evidence: { db: 'confirmed', git: 'not_started' }, reconciliationRequired: true }) }
    if (scenario === 'concurrent') useEditorStore.getState().updateSpecField('title', 'Later edit')
    row = { ...row, specJson: submitted.specJson, draftToken: scenario === 'stale-token' ? token : newToken }
    const yaml = stringify(JSON.parse(submitted.specJson), { sortMapEntries: true, lineWidth: 0 })
    snapshot = { ...snapshot, baseCommit: scenario === 'stale-commit' ? old : responseCommit, draftHead: scenario === 'stale-commit' ? old : responseCommit,
      versionFiles: snapshot.versionFiles.map((f: any) => f.path.endsWith('.yaml') && f.path !== 'workflow.pack.yaml'
        ? { ...f, contentBase64: encode(scenario === 'stale-yaml' ? 'id: wrong\n' : yaml) }
        : f.path === 'scripts/a.sh' && submitted.changes.length ? { ...f, contentBase64: encode('new') } : f).filter((f: any) => scenario !== 'missing-file' || f.path !== 'scripts/a.sh') }
    return { status: 200, json: async () => ({ requestId: submitted.requestId, outcome: 'confirmed', reconciliationRequired: false,
      evidence: { db: 'confirmed', git: 'confirmed', inputHash: 'e'.repeat(64), commit: responseCommit, draftToken: newToken } }) }
  })
  vi.stubGlobal('fetch', post)
})
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); vi.resetAllMocks() })
async function setup(script: boolean) {
  const id = `unified-hook-${++counter}`, spec = { id, title: 'Original', version: '1', nodes: [] }
  row = { workflowId: id, packId: null, specJson: JSON.stringify(spec), draftToken: token }
  const source = await readEditorWorkflowSource(id)
  useEditorStore.getState().loadSpec(source.spec, source.databaseSpec, source.activeDeployNumber)
  const files = [{ path: 'workflow.pack.yaml', contentBase64: encode(stringify({ id, workflows: [id] })), mode: '100644' as const },
    { path: `workflows/${id}.yaml`, contentBase64: encode(stringify(spec)), mode: '100644' as const },
    { path: 'scripts/a.sh', contentBase64: encode('old'), mode: '100644' as const }]
  snapshot = { workflowId: id, packId: id, baseRef: `origin/wf/${id}`, baseCommit: old, draftHead: old, canWrite: false, versionFiles: files }
  const session = sessionFor(id)
  publish(session, { ...session.state, version: 'draft', versions: { ...session.state.versions, draft: { snapshot, files: structuredClone(files), loading: false, error: null, blocked: false, notice: null } } })
  if (script) updateVersion(session, 'draft', { files: files.map(f => f.path === 'scripts/a.sh' ? { ...f, contentBase64: encode('new') } : f) })
  useEditorStore.getState().updateSpecField('title', 'Edited')
  return { id, session, hook: renderHook(() => usePackCanvasSession(id)) }
}
it.each([false, true])('submits one candidate and verifies the complete saved snapshot (script=%s)', async script => {
  const ctx = await setup(script)
  await act(async () => { await expect(ctx.hook.result.current.save()).resolves.toEqual({ mode: 'unified', dbSaved: true, gitSaved: true, commit }) })
  expect(post.mock.calls.filter(([, init]) => init.method === 'POST')).toHaveLength(1)
  expect(submitted.expectedDraftToken).toBe(token)
  expect(submitted.sourceCommit).toBe(old)
  expect(JSON.parse(submitted.specJson).title).toBe('Edited')
  expect(submitted.changes.map((c: any) => c.path)).toEqual(script ? ['scripts/a.sh'] : [])
  expect(ctx.session.state.versions.draft.snapshot?.baseCommit).toBe(commit)
  expect(useEditorStore.getState().isDirty).toBe(false)
  expect(editorWorkflowWriteState(ctx.id).phase).toBe('clear')
})
it.each(['partial', 'stale-commit', 'stale-token', 'stale-yaml', 'missing-file', 'denied', 'offline'] as const)('retains and locks drafts after %s', async kind => {
  scenario = kind
  const ctx = await setup(true)
  await act(async () => { await expect(ctx.hook.result.current.save()).rejects.toThrow('统一保存') })
  expect(ctx.session.state.versions.draft.snapshot?.baseCommit).toBe(old)
  expect(ctx.session.state.versions.draft.files.find(f => f.path === 'scripts/a.sh')?.contentBase64).toBe(encode('new'))
  expect(useEditorStore.getState().isDirty).toBe(true)
  expect(editorWorkflowWriteState(ctx.id).phase).toBe('unknown')
  await expect(ctx.hook.result.current.save()).rejects.toThrow()
  expect(post.mock.calls.filter(([, init]) => init.method === 'POST')).toHaveLength(1)
})

it('preserves a later canvas edit after verifying the submitted snapshot', async () => {
  scenario = 'concurrent'
  const ctx = await setup(true)
  await act(async () => { await expect(ctx.hook.result.current.save()).resolves.toEqual({ mode: 'unified', dbSaved: true, gitSaved: true, commit }) })
  expect(JSON.parse(submitted.specJson).title).toBe('Edited')
  expect(JSON.parse(row.specJson).title).toBe('Edited')
  expect(ctx.session.state.versions.draft.snapshot?.baseCommit).toBe(commit)
  expect(ctx.session.state.versions.draft.snapshot?.draftHead).toBe(commit)
  expect(ctx.session.state.versions.draft.files.find(f => f.path === 'scripts/a.sh')?.contentBase64).toBe(encode('new'))
  expect(useEditorStore.getState().spec?.title).toBe('Later edit')
  expect(useEditorStore.getState()._savedSpec?.title).toBe('Edited')
  expect(useEditorStore.getState().isDirty).toBe(true)
  expect(editorWorkflowWriteState(ctx.id).phase).toBe('clear')
  expect(post.mock.calls.filter(([, init]) => init.method === 'POST')).toHaveLength(1)
  scenario = 'ok'
  const secondCommit = 'c'.repeat(40)
  responseCommit = secondCommit
  await act(async () => { await expect(ctx.hook.result.current.save()).resolves.toMatchObject({ commit: secondCommit }) })
  expect(submitted.sourceCommit).toBe(commit)
  expect(submitted.expectedSavedHead).toBe(commit)
  expect(ctx.session.state.versions.draft.snapshot?.baseCommit).toBe(secondCommit)
  expect(ctx.session.state.versions.draft.snapshot?.draftHead).toBe(secondCommit)
  expect(JSON.parse(submitted.specJson).title).toBe('Later edit')
  expect(JSON.parse(row.specJson).title).toBe('Later edit')
  expect(useEditorStore.getState().isDirty).toBe(false)
  expect(post.mock.calls.filter(([, init]) => init.method === 'POST')).toHaveLength(2)
})

it('does not trust even a correlated pre-write rejection without origin-bound proof', async () => {
  scenario = 'rejected'
  const ctx = await setup(true)
  await act(async () => { await expect(ctx.hook.result.current.save()).rejects.toThrow('统一保存') })
  expect(ctx.session.state.postSaveVerificationError).toBeTruthy()
  await expect(ctx.hook.result.current.save()).rejects.toThrow()
  expect(post.mock.calls.filter(([, init]) => init.method === 'POST')).toHaveLength(1)
})

it('does not advertise unified capability based on legacy pack-version canWrite', async () => {
  const ctx = await setup(false)
  act(() => {
    const state = ctx.session.state.versions.draft
    updateVersion(ctx.session, 'draft', { snapshot: { ...state.snapshot!, canWrite: true } })
  })
  expect(ctx.hook.result.current.canWrite).toBe(false)
  await act(async () => { await expect(ctx.hook.result.current.save()).resolves.toMatchObject({ commit }) })
})

it('does not prompt, dispatch or lock when an existing draft lacks unified capability, and can retry when restored', async () => {
  scenario = 'default-host'
  const ctx = await setup(true)
  vi.mocked(window.confirm).mockReturnValueOnce(false)
  await act(async () => { await expect(ctx.hook.result.current.save()).rejects.toThrow('数据库和 Git 均未写入') })
  expect(window.confirm).not.toHaveBeenCalled()
  expect(post.mock.calls.map(([, init]) => init.method)).toEqual(['GET'])
  expect(useEditorStore.getState().isDirty).toBe(true)
  expect(ctx.session.state.postSaveVerificationError).toBeFalsy()
  expect(editorWorkflowWriteState(ctx.id).phase).toBe('clear')
  scenario = 'ok'
  await act(async () => { await expect(ctx.hook.result.current.save()).resolves.toMatchObject({ commit }) })
})

it('default deny-only host refuses DB-only CAS and preserves the database, YAML and file drafts', async () => {
  scenario = 'default-host'
  const ctx = await setup(true)
  const before = structuredClone(ctx.session.state.versions.draft)
  const beforeRow = structuredClone(row)
  await act(async () => { await expect(ctx.hook.result.current.save()).rejects.toThrow('数据库和 Git 均未写入') })
  expect(window.confirm).not.toHaveBeenCalled()
  expect(post.mock.calls.map(([url, init]) => [url, init.method])).toEqual([
    [`/api/workflows/${ctx.id}/unified-save`, 'GET'],
  ])
  expect(row).toEqual(beforeRow)
  expect(useEditorStore.getState().spec?.title).toBe('Edited')
  expect(useEditorStore.getState().isDirty).toBe(true)
  expect(ctx.session.state.versions.draft).toEqual(before)
  expect(editorWorkflowWriteState(ctx.id).phase).toBe('clear')
})
it.each(['cap-invalid', 'cap-down'] as const)('does not fall back or lock on indeterminate capability %s', async kind => {
  scenario = kind
  const ctx = await setup(true)
  await act(async () => { await expect(ctx.hook.result.current.save()).rejects.toThrow('能力') })
  expect(post.mock.calls.map(([, init]) => init.method)).toEqual(['GET'])
  expect(editorWorkflowWriteState(ctx.id).phase).toBe('clear')
  expect(useEditorStore.getState().isDirty).toBe(true)
})
it('saves the edited DB candidate over divergent Git YAML', async () => {
  const ctx = await setup(false)
  act(() => {
    const v = ctx.session.state.versions.draft
    const files = v.files.map(f => f.path.startsWith('workflows/') ? { ...f, contentBase64: encode(stringify({ id: ctx.id, title: 'Different Git', version: '1', nodes: [] })) } : f)
    updateVersion(ctx.session, 'draft', { snapshot: { ...v.snapshot!, versionFiles: files }, files: structuredClone(files) })
  })
  expect(ctx.hook.result.current.baselineComparison).toBe('different')
  await act(async () => { await expect(ctx.hook.result.current.save()).resolves.toMatchObject({ mode: 'unified', commit }) })
  expect(post.mock.calls.map(([, init]) => init.method)).toEqual(['GET', 'POST'])
  expect(JSON.parse(submitted.specJson).title).toBe('Edited')
  expect(submitted.expectedDraftToken).toBe(token)
  expect(editorWorkflowWriteState(ctx.id).phase).toBe('clear')
  expect(useEditorStore.getState().isDirty).toBe(false)
})
