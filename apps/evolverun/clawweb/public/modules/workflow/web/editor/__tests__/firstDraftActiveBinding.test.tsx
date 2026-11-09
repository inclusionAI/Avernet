import { act, cleanup, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { stringify } from 'yaml'
import { webcrypto } from 'node:crypto'
import { editorActiveDeploymentToken } from '../../../server/services/editor-active-deployment-token'
import { api, fetchJson } from '@avernet/clawweb-shared/web/api/client'
import { readEditorWorkflowSource } from '../../api/editorWorkflowSource'
import { useEditorStore } from '../store'
import { beginWorkflowTransition, commitWorkflowTransition, prepareWorkflowTransition } from '../workflowTransition'
import { saveWorkflowCanvasDocument } from '../workflowDocumentSave'
import { usePackCanvasSession } from '../usePackCanvasSession'
import { editorWorkflowWriteState } from '../workflowWriteGuard'
import { publish, sessionFor, updateVersion } from '../packVersionSession'

vi.mock('@avernet/clawweb-shared/web/hooks/useClientUser', async importOriginal => ({
  ...(await importOriginal<typeof import('@avernet/clawweb-shared/web/hooks/useClientUser')>()),
  getClientUser: () => ({ userId: 'actor', nickName: '测试用户' }),
}))
vi.mock('@avernet/clawweb-shared/web/api/client', () => ({ api: { workflows: { get: vi.fn(), getHistory: vi.fn(), getDeploySnapshot: vi.fn() } }, fetchJson: vi.fn() }))
const id = 'first-active-flow', packId = 'active-pack', draftToken = 'b'.repeat(64), oid = 'c'.repeat(40)
const spec = { id, title: 'Original', version: '1', nodes: [] }
const activeToken = editorActiveDeploymentToken(id, { pack_id: packId, deploy_number: 9, version: 3, tag_name: 'v3', action: 'deploy', spec_json: JSON.stringify(spec) })
let active: Record<string, unknown>, snapshot: Record<string, unknown>, stored: typeof spec | null, dbPack: string, beforePost: (() => void) | undefined
const text = (body: Blob) => new Promise<string>(resolve => { const r = new FileReader(); r.onload = () => resolve(String(r.result)); r.readAsText(body) })
const state = () => useEditorStore.getState()
beforeEach(() => {
  // Each test represents a fresh browser tab; no production reset is exposed.
  const guard = (globalThis as typeof globalThis & { [key: symbol]: { entries: Map<string, unknown> } | undefined })[Symbol.for('avernet.editor.workflow-write-guard.v1')]
  guard?.entries.delete(id); sessionStorage.clear()
  state().reset({ force: true }); stored = null; dbPack = packId; beforePost = undefined
  vi.spyOn(window, 'confirm').mockReturnValue(true)
  vi.stubGlobal('crypto', webcrypto)
  // The first-draft writer must see real Response objects, not fetchJson errors.
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => new Response(JSON.stringify(await fetchJson(url, init)))))
  active = { deployNumber: 9, version: 3, tagName: 'v3', action: 'deploy', isActive: true, packId, activeToken }
  snapshot = { workflowId: id, deployNumber: 9, version: 3, tagName: 'v3', action: 'deploy', specJson: JSON.stringify(spec) }
  vi.mocked(api.workflows.get).mockImplementation(async () => {
    if (!stored) throw { status: 404, body: JSON.stringify({ code: 'WORKFLOW_NOT_FOUND', workflowId: id }) }
    return structuredClone(stored) as never
  })
  vi.mocked(api.workflows.getHistory).mockImplementation(async () => ({ workflowId: id, history: [], active: structuredClone(active) }) as never)
  vi.mocked(api.workflows.getDeploySnapshot).mockImplementation(async () => structuredClone(snapshot) as never)
  vi.mocked(fetchJson).mockImplementation(async (url, init) => {
    if (url.endsWith('/editor-draft')) return { workflowId: id, packId: dbPack, specJson: JSON.stringify(stored), draftToken } as never
    if (url === '/api/auto-heal/editor-save-first-draft') {
      const body = JSON.parse(await text(init!.body as Blob)); beforePost?.()
      if (stored || body.packId !== active.packId || body.expectedActiveDeployNumber !== active.deployNumber || body.expectedActiveToken !== active.activeToken) throw new Error('conflict')
      stored = body.spec; dbPack = body.packId
      return { ok: true, saved: true, workflowId: id, packId: dbPack } as never
    }
    if (url.endsWith('/pack-version')) {
      const body = JSON.parse(await text(init!.body as Blob))
      return { branch: `wf/${id}`, commit: oid, packId, version: 1, requestId: body.requestId } as never
    }
    throw new Error(`unexpected ${url}`)
  })
})
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.resetAllMocks(); vi.unstubAllGlobals() })
async function open() {
  const source = await readEditorWorkflowSource(id)
  state().loadSpec(source.spec, source.databaseSpec, source.activeDeployNumber)
  return source
}
function setupPack() {
  const files = [
    { path: 'workflow.pack.yaml', contentBase64: btoa(stringify({ id: packId, workflows: [id] })), mode: '100644' as const },
    { path: `workflows/${id}.yaml`, contentBase64: btoa(stringify(spec)), mode: '100644' as const },
    { path: 'scripts/a.sh', contentBase64: btoa('old'), mode: '100644' as const },
  ]
  const base = { workflowId: id, packId, baseRef: `origin/wf/${id}`, baseCommit: oid, draftHead: oid, canWrite: true, versionFiles: files }
  const session = sessionFor(id)
  publish(session, { ...session.state, version: 'draft', versions: { ...session.state.versions, draft: { snapshot: base, files: structuredClone(files), loading: false, error: null, blocked: false, notice: null } } })
  updateVersion(session, 'draft', { files: files.map(f => f.path === 'scripts/a.sh' ? { ...f, contentBase64: btoa('new') } : f) })
  return { session, hook: renderHook(() => usePackCanvasSession(id)) }
}

it('creates the first draft with the captured pack and token, then binds the verified CAS row', async () => {
  await open(); expect(state()._databasePackId).toBe(packId)
  state().updateSpecField('title', 'Local')
  expect(await saveWorkflowCanvasDocument(id)).toEqual({ dbSaved: true, commit: null })
  const calls = vi.mocked(fetchJson).mock.calls
  const body = JSON.parse(await text(calls.find(([url]) => url === '/api/auto-heal/editor-save-first-draft')![1]!.body as Blob))
  expect(body).toMatchObject({ workflowId: id, packId, expectedDraftAbsent: true, expectedActiveDeployNumber: 9, expectedActiveToken: activeToken })
  expect(vi.mocked(fetch).mock.calls[0][0]).toBe('/api/auto-heal/editor-save-first-draft')
  expect(vi.mocked(fetch).mock.calls[0][1]).toMatchObject({ method: 'POST', cache: 'no-store', headers: {
    'Content-Type': 'application/json', 'X-Pack-Version-CSRF': '1', 'X-User-Id': 'actor', 'X-User-Name': encodeURIComponent('测试用户'),
  } })
  expect(state()._databasePackId).toBe(packId); expect(state()._databaseSpec?.title).toBe('Local'); expect(state()._activeBaseline).toBeUndefined()
})
it.each(['missing pack', 'missing active token'] as const)('allows old history to be read but not saved without evidence: %s', async kind => {
  if (kind === 'missing pack') delete active.packId
  if (kind === 'missing active token') delete active.activeToken
  await open(); state().updateSpecField('title', 'Local')
  await expect(saveWorkflowCanvasDocument(id)).rejects.toThrow('缺少可信')
  expect(fetchJson).not.toHaveBeenCalled()
})
it.each(['content', 'token', 'tag', 'action', 'missing tag', 'missing action'] as const)('rejects mismatched snapshot %s before replacing the editor', async kind => {
  state().loadSpec(spec)
  state().updateSpecField('title', 'Local unsaved')
  const before = state()
  if (kind === 'content') snapshot.specJson = JSON.stringify({ ...spec, title: 'Other' })
  if (kind === 'token') active.activeToken = 'e'.repeat(64)
  if (kind === 'tag') snapshot.tagName = 'other-tag'
  if (kind === 'action') snapshot.action = 'rollback'
  if (kind === 'missing tag') delete snapshot.tagName
  if (kind === 'missing action') delete snapshot.action
  await expect(open()).rejects.toThrow('校验不匹配')
  expect(state()).toBe(before)
  expect(fetchJson).not.toHaveBeenCalled()
})
it('rejects a same-deployment spec/token change since opening', async () => {
  await open(); state().updateSpecField('title', 'Local')
  snapshot.specJson = JSON.stringify({ ...spec, title: 'Remote' }); active.activeToken = editorActiveDeploymentToken(id, { pack_id: packId, deploy_number: 9, version: 3, tag_name: 'v3', action: 'deploy', spec_json: snapshot.specJson as string })
  await expect(saveWorkflowCanvasDocument(id)).rejects.toThrow('生效版本')
  expect(fetchJson).not.toHaveBeenCalled(); expect(state().isDirty).toBe(true)
})
it('leaves the draft dirty when the server detects a change after preflight', async () => {
  await open(); state().updateSpecField('title', 'Local')
  beforePost = () => { active.activeToken = 'd'.repeat(64) }
  await expect(saveWorkflowCanvasDocument(id)).rejects.toThrow('结果未知')
  expect(stored).toBeNull(); expect(state()._databaseSpec).toBeNull(); expect(state().isDirty).toBe(true)
})
it('does not fall back to the legacy AutoHeal save when the dedicated endpoint is unavailable', async () => {
  await open(); state().updateSpecField('title', 'Local')
  const original = vi.mocked(fetchJson).getMockImplementation()!
  vi.mocked(fetchJson).mockImplementation(async (url, init) => {
    if (url === '/api/auto-heal/editor-save-first-draft') throw new Error('not found')
    return original(url, init)
  })
  await expect(saveWorkflowCanvasDocument(id)).rejects.toThrow('结果未知')
  expect(vi.mocked(fetchJson).mock.calls.some(([url]) => url === '/api/auto-heal/save')).toBe(false)
  expect(stored).toBeNull(); expect(state().isDirty).toBe(true)
})
it('preserves the active evidence across a prepared workflow transition', async () => {
  const source = await readEditorWorkflowSource(id), approval = beginWorkflowTransition()!
  const prepared = prepareWorkflowTransition(approval, source.spec, id)!
  expect(commitWorkflowTransition(approval, prepared, id, false, null, 9)).toBe(true)
  expect(state()._databasePackId).toBe(packId); expect(state()._activeBaseline?.activeToken).toBe(activeToken)
})
it('reports a partial first DB draft when unified save is unavailable and preserves file drafts', async () => {
  await open(); const ctx = setupPack()
  const original = vi.mocked(fetch).getMockImplementation()!
  vi.mocked(fetch).mockImplementation((url, init) => String(url).endsWith('/unified-save')
    ? Promise.resolve(new Response('{}', { status: 404 })) : original(url, init))
  const candidate = structuredClone(ctx.session.state.versions.draft.files)
  await act(async () => { await expect(ctx.hook.result.current.save()).rejects.toMatchObject({
    name: 'PackCanvasPartialSaveError', stages: { db: 'confirmed', git: 'not-started' },
  }) })
  expect(window.confirm).toHaveBeenCalledWith(expect.stringContaining('Git 未写入'))
  expect(state()._databasePackId).toBe(packId); expect(state()._databaseSpec).toEqual(spec)
  expect(ctx.session.state.versions.draft.files).toEqual(candidate)
  expect(vi.mocked(fetch).mock.calls.map(([url]) => url)).toEqual(['/api/auto-heal/editor-save-first-draft', `/api/workflows/${id}/unified-save`])
  // Reset only this fixture's sticky partial state before the next case.
  publish(ctx.session, { ...ctx.session.state, postSaveVerificationError: null })
})
it('does not adopt or write Git when first draft readback has another binding', async () => {
  await open(); const ctx = setupPack(); const original = vi.mocked(fetchJson).getMockImplementation()!
  vi.mocked(fetchJson).mockImplementation(async (url, init) => {
    if (url.endsWith('/editor-draft')) return { workflowId: id, packId: 'wrong', specJson: JSON.stringify(stored), draftToken } as never
    return original(url, init)
  })
  await act(async () => { await expect(ctx.hook.result.current.save()).rejects.toMatchObject({ name: 'WorkflowDocumentAcknowledgedError' }) })
  expect(vi.mocked(fetchJson).mock.calls.every(([url]) => !url.endsWith('/pack-version'))).toBe(true)
  expect(state()._databaseSpec).toBeNull()
  expect(ctx.session.state.versions.draft.files.find(file => file.path === 'scripts/a.sh')?.contentBase64).toBe(btoa('new'))
})
it('preserves edits made after the first-draft POST starts', async () => {
  await open(); state().updateSpecField('title', 'Submitted')
  beforePost = () => state().updateSpecField('title', 'Later edit')
  expect(await saveWorkflowCanvasDocument(id)).toEqual({ dbSaved: true, commit: null })
  expect(state()._databaseSpec?.title).toBe('Submitted')
  expect(state().spec?.title).toBe('Later edit'); expect(state().isDirty).toBe(true)
})
it('does not adopt a first-draft receipt into an altered same-session active baseline', async () => {
  await open(); state().updateSpecField('title', 'Submitted')
  beforePost = () => useEditorStore.setState({ _activeBaseline: { ...state()._activeBaseline!, activeToken: 'd'.repeat(64) } })
  await expect(saveWorkflowCanvasDocument(id)).rejects.toThrow('无法确认保存基线')
  expect(stored?.title).toBe('Submitted'); expect(state()._databaseSpec).toBeNull(); expect(state().isDirty).toBe(true)
})
it.each(['missing token and tag', 'invalid token and tag', 'missing token and action', 'invalid token and action'] as const)(
  'rejects observable mismatch with %s before editing', async scenario => {
    state().loadSpec(spec); state().updateSpecField('title', 'Local unsaved')
    const before = state()
    if (scenario.startsWith('missing')) delete active.activeToken
    else active.activeToken = 'invalid'
    if (scenario.endsWith('tag')) snapshot.tagName = 'other-tag'
    else snapshot.action = 'rollback'
    await expect(open()).rejects.toThrow('校验不匹配')
    expect(state()).toBe(before)
    expect(fetchJson).not.toHaveBeenCalled()
  },
)
it('rejects observable tag disagreement even if Web Crypto is unavailable', async () => {
  vi.stubGlobal('crypto', { randomUUID: () => 'unused' })
  snapshot.tagName = 'different-tag'
  await expect(open()).rejects.toThrow('校验不匹配')
  expect(fetchJson).not.toHaveBeenCalled()
})
it('does not guess a content mismatch when Web Crypto is unavailable, and cannot save', async () => {
  vi.stubGlobal('crypto', { randomUUID: () => 'unused' })
  snapshot.specJson = JSON.stringify({ ...spec, title: 'Other' })
  await open(); expect(state().spec?.title).toBe('Other')
  state().updateSpecField('title', 'Local')
  await expect(saveWorkflowCanvasDocument(id)).rejects.toThrow('缺少可信')
  expect(fetchJson).not.toHaveBeenCalled()
})
it('keeps a snapshot readable but unsavable if Web Crypto digest fails', async () => {
  vi.stubGlobal('crypto', { subtle: { digest: vi.fn().mockRejectedValue(new Error('unavailable')) } })
  await open(); expect(state().spec).toEqual(spec); state().updateSpecField('title', 'Local')
  await expect(saveWorkflowCanvasDocument(id)).rejects.toThrow('缺少可信')
  expect(fetchJson).not.toHaveBeenCalled()
})
it('keeps legacy active content readable but refuses to save if Web Crypto is unavailable', async () => {
  vi.stubGlobal('crypto', { randomUUID: () => 'unused' })
  await open(); expect(state().spec).toEqual(spec); state().updateSpecField('title', 'Local')
  await expect(saveWorkflowCanvasDocument(id)).rejects.toThrow('缺少可信')
  expect(fetchJson).not.toHaveBeenCalled()
})

it('does not adopt a first-draft completion without same-row identity evidence', async () => {
  await open(); state().updateSpecField('title', 'Local')
  const request = structuredClone(state().spec!)
  expect(state().completeSave(request, request, state().sessionId)).toBe(false)
  expect(state()._databaseSpec).toBeNull(); expect(state()._databasePackId).toBe(packId); expect(state().isDirty).toBe(true)
})


const marker = () => `avernet.editor.pending-workflow-write.v1:${encodeURIComponent(id)}`
async function guardStateAfterReload() {
  const symbol = Symbol.for('avernet.editor.workflow-write-guard.v1')
  const root = globalThis as typeof globalThis & { [key: symbol]: unknown }
  const previous = root[symbol]
  delete root[symbol]
  try {
    vi.resetModules()
    return (await import('../workflowWriteGuard')).editorWorkflowWriteState(id).phase
  } finally { root[symbol] = previous }
}

it.each(['DRAFT_ALREADY_EXISTS', 'BASELINE_STALE'] as const)('clears its owner lock for real pre-write 409 %s and can read after reload', async code => {
  await open(); state().updateSpecField('title', 'Local')
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ code }), { status: 409 })))
  await expect(saveWorkflowCanvasDocument(id)).rejects.toThrow('首次数据库草稿保存冲突')
  expect(fetch).toHaveBeenCalledTimes(1)
  expect(vi.mocked(fetch).mock.calls[0][0]).toBe('/api/auto-heal/editor-save-first-draft')
  expect(editorWorkflowWriteState(id).phase).toBe('clear')
  expect(sessionStorage.getItem(marker())).toBeNull()
  expect(state().spec?.title).toBe('Local'); expect(state().isDirty).toBe(true)
  expect((await readEditorWorkflowSource(id)).spec).toEqual(spec)
  expect(await guardStateAfterReload()).toBe('clear')
})

it.each(['forged exception', 'thrown Response', 'network', 'fake response', 'forged prototype', 'wrong code', 'wrong status', 'malformed body', 'missing code', 'oversized body', 'invalid success', 'unreadable body'] as const)(
  'keeps the lock and refuses future reads after %s', async kind => {
    await open(); state().updateSpecField('title', 'Local')
    const fetchMock = vi.fn(async () => {
      if (kind === 'forged exception') throw { status: 409, code: 'DRAFT_ALREADY_EXISTS', body: '{"code":"DRAFT_ALREADY_EXISTS"}' }
      if (kind === 'thrown Response') throw new Response('{"code":"BASELINE_STALE"}', { status: 409 })
      if (kind === 'network') throw new TypeError('connection lost')
      if (kind === 'fake response') return { ok: false, status: 409, text: async () => '{"code":"BASELINE_STALE"}' }
      if (kind === 'forged prototype') return Object.defineProperties(Object.create(Response.prototype), { status: { value: 409 }, text: { value: async () => '{"code":"BASELINE_STALE"}' } })
      if (kind === 'wrong code') return new Response('{"code":"DRAFT_CONFLICT"}', { status: 409 })
      if (kind === 'wrong status') return new Response('{"code":"BASELINE_STALE"}', { status: 500 })
      if (kind === 'malformed body') return new Response('{', { status: 409 })
      if (kind === 'missing code') return new Response('{}', { status: 409 })
      if (kind === 'oversized body') return new Response(JSON.stringify({ code: 'BASELINE_STALE', detail: 'x'.repeat(9000) }), { status: 409 })
      if (kind === 'invalid success') return new Response('{', { status: 200 })
      return new Response(new ReadableStream({ start(controller) { controller.error(new Error('body lost')) } }), { status: 409 })
    })
    vi.stubGlobal('fetch', fetchMock)
    await expect(saveWorkflowCanvasDocument(id)).rejects.toThrow('结果未知')
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(editorWorkflowWriteState(id).phase).toBe('unknown')
    expect(sessionStorage.getItem(marker())).not.toBeNull()
    await expect(readEditorWorkflowSource(id)).rejects.toThrow('人工核对')
    expect(await guardStateAfterReload()).toBe('unknown')
    expect(state().spec?.title).toBe('Local'); expect(state().isDirty).toBe(true)
  },
)

it('does not clear another owner marker even for a real pre-write conflict', async () => {
  await open(); state().updateSpecField('title', 'Local')
  vi.stubGlobal('fetch', vi.fn(async () => {
    sessionStorage.setItem(marker(), JSON.stringify({ version: 1, owner: 'another-owner' }))
    return new Response('{"code":"DRAFT_ALREADY_EXISTS"}', { status: 409 })
  }))
  await expect(saveWorkflowCanvasDocument(id)).rejects.toThrow('标记无法清除')
  expect(editorWorkflowWriteState(id).phase).toBe('unknown')
  expect(JSON.parse(sessionStorage.getItem(marker())!).owner).toBe('another-owner')
  await expect(readEditorWorkflowSource(id)).rejects.toThrow('人工核对')
})

it('does not trust replay of a previous classified conflict as a new fetch exception', async () => {
  await open(); state().updateSpecField('title', 'Local')
  vi.stubGlobal('fetch', vi.fn(async () => new Response('{"code":"BASELINE_STALE"}', { status: 409 })))
  const previous = await saveWorkflowCanvasDocument(id).catch(error => error)
  expect(previous.message).toContain('保存冲突')
  expect(editorWorkflowWriteState(id).phase).toBe('clear')
  const replay = vi.fn(async () => { throw previous })
  vi.stubGlobal('fetch', replay)
  await expect(saveWorkflowCanvasDocument(id)).rejects.toThrow('结果未知')
  expect(replay).toHaveBeenCalledTimes(1)
  expect(editorWorkflowWriteState(id).phase).toBe('unknown')
  await expect(readEditorWorkflowSource(id)).rejects.toThrow('人工核对')
})
