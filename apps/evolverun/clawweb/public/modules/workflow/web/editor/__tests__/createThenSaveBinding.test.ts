import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { stringify } from 'yaml'
import { api, fetchJson } from '@avernet/clawweb-shared/web/api/client'
import { createWorkflowPack } from '../newWorkflowSave'
import { saveWorkflowCanvasDocument } from '../workflowDocumentSave'
import { useEditorStore } from '../store'
import { sessionFor } from '../packVersionSession'

vi.mock('@avernet/clawweb-shared/web/api/client', () => ({ fetchJson: vi.fn(), api: { workflows: { get: vi.fn() } } }))
const commit = 'c'.repeat(40), token = 'a'.repeat(64), next = 'b'.repeat(64)
let sequence = 0
let stored: { id: string; title: string; version: string; nodes: never[] }, packId: string | null, gate: Promise<void> | undefined
let readStarted: () => void, began: Promise<void>
const text = (body: Blob) => new Promise<string>(resolve => { const r = new FileReader(); r.onload = () => resolve(String(r.result)); r.readAsText(body) })
const state = () => useEditorStore.getState()
beforeEach(() => {
  state().reset({ force: true }); gate = undefined
  began = new Promise(resolve => { readStarted = resolve })
  vi.mocked(api.workflows.get).mockImplementation(async () => structuredClone(stored) as never)
  vi.mocked(fetchJson).mockImplementation(async (url, init) => {
    if (url === '/api/auto-heal/save') {
      const body = JSON.parse(await text(init!.body as Blob)); stored = body.spec; packId = body.packId
      return { ok: true, saved: true, workflowId: body.workflowId, packId, branch: `wf/${body.workflowId}`, commit } as never
    }
    if (url.includes('/pack-version?')) return { workflowId: stored.id, packId: stored.id, baseRef: `origin/wf/${stored.id}`, baseCommit: commit, draftHead: commit,
      versionFiles: [{ path: 'workflow.pack.yaml', contentBase64: btoa(stringify({ id: stored.id, workflows: [stored.id] })), mode: '100644' },
        { path: `workflows/${stored.id}.yaml`, contentBase64: btoa(stringify(stored)), mode: '100644' }] } as never
    if (url.endsWith('/editor-draft') && init?.method === 'GET') {
      readStarted(); if (gate) await gate
      return { workflowId: stored.id, packId, specJson: JSON.stringify(stored), draftToken: token } as never
    }
    throw new Error(`unexpected ${url}`)
  })
  vi.stubGlobal('fetch', vi.fn(async (_url: string, init: RequestInit) => {
    const body = JSON.parse(await text(init.body as Blob))
    expect(body.expectedDraftToken).toBe(token); expect(body).not.toHaveProperty('packId')
    stored = body.spec
    return new Response(JSON.stringify({ workflowId: stored.id, packId, specJson: JSON.stringify(stored), draftToken: next }))
  }))
})
afterEach(() => { vi.resetAllMocks(); vi.unstubAllGlobals(); state().reset({ force: true }) })
function draft() {
  const id = `created-binding-${++sequence}`
  state().createNew(id, 'Original')
  return { id, sessionId: state().sessionId, request: structuredClone(state().spec!) }
}
it('creates, captures same-row identity, preserves in-flight edits and saves them using CAS', async () => {
  const { id, sessionId, request } = draft(); let release!: () => void
  gate = new Promise(resolve => { release = resolve })
  const creating = createWorkflowPack(request); await began
  state().updateSpecField('title', 'Edited during creation')
  release(); const created = await creating; gate = undefined
  expect(state().completeSave(created.requestedSpec, created.requestedSpec, sessionId, created.databaseSpec)).toBe(true)
  expect(state()._databasePackId).toBe(id); expect(state()._databaseSpec?.title).toBe('Original')
  expect(state().spec?.title).toBe('Edited during creation'); expect(state().isDirty).toBe(true)
  expect(await saveWorkflowCanvasDocument(id)).toEqual({ dbSaved: true, commit: null })
  expect(stored.title).toBe('Edited during creation'); expect(state().isDirty).toBe(false)
  expect(vi.mocked(fetchJson).mock.calls.filter(([url]) => url === '/api/auto-heal/save')).toHaveLength(1)
})
it.each([null, 'other-pack'])('keeps the create acknowledgement unconfirmed when raw DB pack is %s', async mismatch => {
  const { id, request } = draft(); let release!: () => void
  gate = new Promise(resolve => { release = resolve })
  const creating = createWorkflowPack(request); await began
  packId = mismatch; release()
  await expect(creating).rejects.toThrow('数据库 Pack 身份与创建确认不一致')
  expect(sessionFor(id).state.creation?.phase).toBe('saved'); expect(state()._savedSpec).toBeNull()
  expect(state()._databasePackId).toBeUndefined(); expect(state().isDirty).toBe(true)
})
it('refuses to bind untrusted or already-bound identity in completeSave', async () => {
  const { id, sessionId, request } = draft(), created = await createWorkflowPack(request)
  expect(state().completeSave(request, request, sessionId, structuredClone(created.databaseSpec))).toBe(false)
  expect(state()._databasePackId).toBeUndefined()
  expect(state().completeSave(request, request, sessionId, created.databaseSpec)).toBe(true)
  expect(state()._databasePackId).toBe(id)
  expect(state().completeSave(request, request, sessionId, created.databaseSpec)).toBe(false)
  expect(state()._databasePackId).toBe(id)
})
