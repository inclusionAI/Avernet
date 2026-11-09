import { act, cleanup, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { stringify } from 'yaml'
import { webcrypto } from 'node:crypto'
import { api, fetchJson } from '@avernet/clawweb-shared/web/api/client'
import { editorActiveDeploymentToken } from '../../../server/services/editor-active-deployment-token'
import { readEditorWorkflowSource } from '../../api/editorWorkflowSource'
import { useEditorStore } from '../store'
import { publish, sessionFor, updateVersion } from '../packVersionSession'
import { usePackCanvasSession } from '../usePackCanvasSession'
import { editorWorkflowWriteState } from '../workflowWriteGuard'

vi.mock('@avernet/clawweb-shared/web/api/client', () => ({ api: { workflows: { get: vi.fn(), getHistory: vi.fn(), getDeploySnapshot: vi.fn() } }, fetchJson: vi.fn() }))
vi.mock('@avernet/clawweb-shared/web/hooks/useClientUser', () => ({ getClientUser: () => ({ userId: 'editor', nickName: 'Editor' }) }))
const originalCommit = 'a'.repeat(40), resultCommit = 'b'.repeat(40), draftToken = 'c'.repeat(64), savedToken = 'd'.repeat(64)
const text = (body: Blob) => new Promise<string>(resolve => { const r = new FileReader(); r.onload = () => resolve(String(r.result)); r.readAsText(body) })
let count = 0
let mode: 'ok' | 'unavailable' | 'cap-error' | 'git-unknown' | 'changed-db' | 'changed-session' | 'first-conflict'
let payloads: Array<{ url: string; body: Record<string, any> }>
let stored: Record<string, any> | null, active: Record<string, any>, deployment: Record<string, any>, saved: Record<string, any>, currentToken: string
let id: string, spec: { id: string; title: string; version: string; nodes: Array<Record<string, unknown>> }
const packId = 'pack'

beforeEach(() => {
  id = `first-unified-${++count}`
  spec = { id, title: 'Original', version: '1', nodes: [
    { id: 'start', title: 'Start', phase: 'execute', type: 'start', config: {}, executor: { type: 'start' }, dependsOn: [] },
    { id: 'end', title: 'End', phase: 'execute', type: 'end', config: {}, executor: { type: 'end' }, dependsOn: ['start'] },
  ] }
  mode = 'ok'; payloads = []; stored = null; currentToken = draftToken
  sessionStorage.clear(); useEditorStore.getState().reset({ force: true })
  vi.spyOn(window, 'confirm').mockReturnValue(true); vi.stubGlobal('crypto', webcrypto)
  const activeToken = editorActiveDeploymentToken(id, { pack_id: packId, deploy_number: 9, version: 3, tag_name: 'v3', action: 'deploy', spec_json: JSON.stringify(spec) })
  active = { deployNumber: 9, version: 3, tagName: 'v3', action: 'deploy', isActive: true, packId, activeToken }
  deployment = { workflowId: id, deployNumber: 9, version: 3, tagName: 'v3', action: 'deploy', specJson: JSON.stringify(spec) }
  const files = [
    { path: 'workflow.pack.yaml', contentBase64: btoa(stringify({ id: packId, workflows: [id] })), mode: '100644' as const },
    { path: `workflows/${id}.yaml`, contentBase64: btoa(stringify(spec)), mode: '100644' as const },
    { path: 'scripts/a.sh', contentBase64: btoa('old'), mode: '100644' as const },
    { path: 'asset.txt', contentBase64: btoa('unchanged'), mode: '100644' as const },
  ]
  saved = { workflowId: id, packId, baseRef: `origin/wf/${id}`, baseCommit: originalCommit, draftHead: originalCommit, canWrite: true, versionFiles: files }
  vi.mocked(api.workflows.get).mockImplementation(async () => {
    if (!stored) throw { status: 404, body: JSON.stringify({ code: 'WORKFLOW_NOT_FOUND', workflowId: id }) }
    return structuredClone(stored) as never
  })
  vi.mocked(api.workflows.getHistory).mockImplementation(async () => ({ workflowId: id, history: [], active: structuredClone(active) }) as never)
  vi.mocked(api.workflows.getDeploySnapshot).mockImplementation(async () => structuredClone(deployment) as never)
  vi.mocked(fetchJson).mockImplementation(async (url) => {
    if (url.endsWith('/editor-draft')) return { workflowId: id, packId, specJson: JSON.stringify(stored), draftToken: currentToken } as never
    if (url.endsWith('/pack-version?version=draft')) return structuredClone(saved) as never
    throw new Error(`unexpected GET ${url}`)
  })
  vi.stubGlobal('fetch', vi.fn(async (input: string, init?: RequestInit) => {
    if (input === '/api/auto-heal/editor-save-first-draft') {
      const body = JSON.parse(await text(init!.body as Blob)); payloads.push({ url: input, body })
      if (mode === 'first-conflict') return new Response(JSON.stringify({ code: 'BASELINE_STALE' }), { status: 409 })
      if (body.expectedActiveToken !== active.activeToken || body.expectedActiveDeployNumber !== active.deployNumber || stored) throw new Error('bad first write')
      stored = structuredClone(body.spec)
      return new Response(JSON.stringify({ ok: true, saved: true, workflowId: id, packId }))
    }
    if (input.endsWith('/unified-save') && init?.method === 'GET') {
      if (mode === 'unavailable') return new Response('{}', { status: 404 })
      if (mode === 'cap-error') return new Response('{}', { status: 503 })
      if (mode === 'changed-db') stored = { ...stored, title: 'Changed by another editor' }
      if (mode === 'changed-session') useEditorStore.getState().updateSpecField('title', 'Later local edit')
      return new Response(JSON.stringify({ protocol: 'workflow-unified-save-v1' }))
    }
    if (input.endsWith('/unified-save') && init?.method === 'POST') {
      const body = JSON.parse(await text(init.body as Blob)); payloads.push({ url: input, body })
      if (mode === 'git-unknown') return new Response(JSON.stringify({ requestId: body.requestId, outcome: 'unknown', evidence: { db: 'confirmed', git: 'unknown' } }), { status: 409 })
      const parsed = JSON.parse(body.specJson); stored = structuredClone(parsed); currentToken = savedToken
      const files = structuredClone(saved.versionFiles) as Array<{ path: string; contentBase64: string; mode: string }>
      const yaml = files.find(f => f.path === `workflows/${id}.yaml`)!
      yaml.contentBase64 = btoa(stringify(parsed, { sortMapEntries: true, lineWidth: 0 }))
      for (const change of body.changes as Array<{ path: string; contentBase64: string; mode: string }>) {
        const file = files.find(f => f.path === change.path)!; file.contentBase64 = change.contentBase64; file.mode = change.mode
      }
      saved = { ...saved, baseCommit: resultCommit, draftHead: resultCommit, versionFiles: files }
      return new Response(JSON.stringify({ requestId: body.requestId, outcome: 'confirmed', reconciliationRequired: false,
        evidence: { db: 'confirmed', git: 'confirmed', inputHash: 'e'.repeat(64), commit: resultCommit, draftToken: savedToken } }))
    }
    throw new Error(`unexpected request ${input}`)
  }))
})
afterEach(() => { cleanup(); vi.resetAllMocks(); vi.restoreAllMocks(); vi.unstubAllGlobals() })

async function setup({ script = true, mismatch = false, malformed = false } = {}) {
  const source = await readEditorWorkflowSource(id)
  useEditorStore.getState().loadSpec(source.spec, source.databaseSpec, source.activeDeployNumber)
  useEditorStore.getState().updateSpecField('title', 'Edited')
  const session = sessionFor(id), base = structuredClone(saved) as any
  if (mismatch || malformed) base.versionFiles.find((f: { path: string }) => f.path === `workflows/${id}.yaml`).contentBase64 = btoa(malformed ? `id: ${id}\nnodes: [` : stringify({ ...spec, title: 'Other baseline' }))
  publish(session, { ...session.state, version: 'draft', versions: { ...session.state.versions,
    draft: { snapshot: base, files: structuredClone(base.versionFiles), loading: false, error: null, blocked: false, notice: null } } })
  if (script) updateVersion(session, 'draft', { files: base.versionFiles.map((f: { path: string }) => f.path === 'scripts/a.sh' ? { ...f, contentBase64: btoa('new') } : f) })
  return { session, hook: renderHook(() => usePackCanvasSession(id)) }
}

it.each([true, false])('restores the DB draft then unifies YAML and files with script=%s', async script => {
  const { session, hook } = await setup({ script })
  await act(async () => { expect(await hook.result.current.save()).toEqual({ mode: 'unified', dbSaved: true, gitSaved: true, commit: resultCommit }) })
  expect(payloads).toHaveLength(2)
  expect(payloads[0].url).toBe('/api/auto-heal/editor-save-first-draft')
  expect(payloads[0].body).toMatchObject({ workflowId: id, expectedDraftAbsent: true, expectedActiveDeployNumber: 9, expectedActiveToken: active.activeToken })
  expect(payloads[1].body).toMatchObject({ sourceVersion: 'draft', sourceCommit: originalCommit, expectedSavedHead: originalCommit, expectedDraftToken: draftToken })
  expect(JSON.parse(payloads[1].body.specJson).title).toBe('Edited')
  expect(payloads[1].body.changes.map((change: { path: string }) => change.path)).toEqual(script ? ['scripts/a.sh'] : [])
  const files = session.state.versions.draft.snapshot!.versionFiles
  expect(atob(files.find(f => f.path === `workflows/${id}.yaml`)!.contentBase64)).toContain('Edited')
  expect(atob(files.find(f => f.path === 'scripts/a.sh')!.contentBase64)).toBe(script ? 'new' : 'old')
  expect(atob(files.find(f => f.path === 'asset.txt')!.contentBase64)).toBe('unchanged')
  expect(editorWorkflowWriteState(id).phase).toBe('clear')
})

it('reports a DB-only partial and keeps local edits when unified is unavailable', async () => {
  mode = 'unavailable'; const { hook, session } = await setup()
  await act(async () => { await expect(hook.result.current.save()).rejects.toMatchObject({ name: 'PackCanvasPartialSaveError', stages: { db: 'confirmed', git: 'not-started' } }) })
  expect(window.confirm).toHaveBeenCalledWith(expect.stringContaining('Git 未写入'))
  expect(session.state.postSaveVerificationError).toContain('Git YAML 和文件均未写入')
  expect(useEditorStore.getState().spec?.title).toBe('Edited')
  expect(session.state.versions.draft.files.find(f => f.path === 'scripts/a.sh')!.contentBase64).toBe(btoa('new'))
  expect(payloads).toHaveLength(1)
})

it.each(['cap-error', 'changed-db', 'changed-session'] as const)('does not dispatch unified write after %s', async failure => {
  mode = failure; const { session, hook } = await setup()
  await act(async () => { await expect(hook.result.current.save()).rejects.toThrow('数据库') })
  expect(payloads).toHaveLength(1); expect(stored).not.toBeNull()
  expect(session.state.versions.draft.files.find(f => f.path === 'scripts/a.sh')!.contentBase64).toBe(btoa('new'))
})

it('uses the edited active DB document when the first draft Git baseline differs', async () => {
  const { hook, session } = await setup({ mismatch: true })
  expect(hook.result.current.baselineComparison).toBe('unavailable')
  await act(async () => { await expect(hook.result.current.save()).resolves.toMatchObject({ mode: 'unified', commit: resultCommit }) })
  expect(payloads).toHaveLength(2)
  expect(JSON.parse(payloads[1].body.specJson).title).toBe('Edited')
  expect(payloads[1].body.expectedDraftToken).toBe(draftToken)
  expect(payloads[1].body.expectedSavedHead).toBe(originalCommit)
  expect(session.state.versions.draft.snapshot?.baseCommit).toBe(resultCommit)
})

it('attempts first-draft publication from a malformed source without treating comparison as approval', async () => {
  const { hook } = await setup({ malformed: true })
  expect(hook.result.current.baselineComparison).toBe('unavailable')
  await act(async () => { await expect(hook.result.current.save()).resolves.toMatchObject({ mode: 'unified', commit: resultCommit }) })
  expect(payloads).toHaveLength(2)
  expect(JSON.parse(payloads[1].body.specJson).title).toBe('Edited')
  expect(payloads[1].body).toMatchObject({ sourceCommit: originalCommit, expectedSavedHead: originalCommit, expectedDraftToken: draftToken })
})

it('does not create a first draft when divergent YAML file edits would be lost', async () => {
  const { hook, session } = await setup({ mismatch: true })
  act(() => {
    const v = session.state.versions.draft
    updateVersion(session, 'draft', { files: v.files.map(f => f.path === `workflows/${id}.yaml`
      ? { ...f, contentBase64: btoa(stringify({ ...spec, title: 'File edit' })) } : f) })
  })
  await act(async () => { await expect(hook.result.current.save()).rejects.toThrow('画布和文件同时修改') })
  expect(payloads).toHaveLength(0); expect(stored).toBeNull()
  expect(useEditorStore.getState().spec?.title).toBe('Edited')
  expect(atob(session.state.versions.draft.files.find(f => f.path === `workflows/${id}.yaml`)!.contentBase64)).toContain('File edit')
})

it('preserves candidate files and quarantines an uncertain unified result', async () => {
  mode = 'git-unknown'; const { session, hook } = await setup()
  await act(async () => { await expect(hook.result.current.save()).rejects.toThrow('统一保存未能完整确认') })
  expect(payloads).toHaveLength(2); expect(editorWorkflowWriteState(id).phase).toBe('unknown')
  expect(session.state.versions.draft.files.find(f => f.path === 'scripts/a.sh')!.contentBase64).toBe(btoa('new'))
})

it('never attempts unified save if the first-draft active token conflicts', async () => {
  mode = 'first-conflict'; const { hook } = await setup()
  await act(async () => { await expect(hook.result.current.save()).rejects.toThrow('保存冲突') })
  expect(payloads).toHaveLength(1); expect(stored).toBeNull()
})
