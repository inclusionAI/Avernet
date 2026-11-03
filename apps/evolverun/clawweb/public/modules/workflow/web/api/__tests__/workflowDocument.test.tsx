import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, renderHook, waitFor } from '@testing-library/react'
import type { PropsWithChildren } from 'react'
import { afterEach, expect, it, vi } from 'vitest'
import { stringify } from 'yaml'
import { createWorkflowsRouter } from '../../../server/routes/workflows'
import { readWorkflowDocument, normalizeWorkflowDocument } from '../workflowDocument'
import type { EditorWorkflowSource } from '../editorWorkflowSource'
import { useEditorWorkflowDocument, useDbWorkflow, useAutoHealSave } from '../hooks'
import { usePackCanvasSession } from '../../editor/usePackCanvasSession'
import { prepareWorkflowSession, useEditorStore } from '../../editor/store'
import { beginWorkflowTransition, commitWorkflowTransition } from '../../editor/workflowTransition'
import { sessionFor } from '../../editor/packVersionSession'
import { sameExecutionSpec, mergeCanvasFiles } from '../../editor/packCanvas'
import type { WorkflowSpec } from '@avernet/clawweb-shared/web/types'

let sequence = 0
const A = 'a'.repeat(40)
const clients: QueryClient[] = []
afterEach(() => { cleanup(); clients.splice(0).forEach(c => c.clear()); useEditorStore.getState().reset({ force: true }); vi.restoreAllMocks(); vi.unstubAllGlobals() })
function document() {
  return { id: `get-contract-${++sequence}`, version: '2.7.0', title: 'Original', custom: { version: 'user-version', keep: false },
    facade: { command: 'document-command' }, nodes: [{ id: 'done', title: 'Done', phase: 'execute', executor: { type: 'done' }, _x: 301, _y: 201 }] } as WorkflowSpec
}
function fixture(doc: WorkflowSpec, storage: 'projection' | 'wrapper' | 'json') {
  const yaml = stringify(doc)
  const stored = storage === 'projection' ? { ...doc, content: yaml } : storage === 'wrapper' ? { content: yaml } : doc
  const row = { workflow_id: doc.id, pack_id: 'pack', spec_json: JSON.stringify(stored), version: 19, gmt_modified: 1793453000 }
  const router = createWorkflowsRouter({ findByWorkflowId: async () => row } as never,
    { findByWorkflowId: async () => [{ command: 'binding-command', remark: 'DB facade' }] } as never, null, null, null)
  async function route(path: '/:workflowId' | '/:workflowId/export') {
    const layer = router.stack.find(layer => layer.route?.path === path)!
    return new Promise<Response>((resolve, reject) => {
      let status = 200
      const headers = new Headers()
      const res = {
        status(value: number) { status = value; return res }, setHeader(key: string, value: string) { headers.set(key, value) },
        json(value: unknown) { headers.set('Content-Type', 'application/json'); resolve(new Response(JSON.stringify(value), { status, headers })) },
        send(value: string) { resolve(new Response(value, { status, headers })) },
      }
      layer.route.stack[0].handle({ params: { workflowId: doc.id }, isAdmin: true } as never, res as never, reject)
    })
  }
  const versionFiles = [
    { path: 'workflow.pack.yaml', contentBase64: btoa(stringify({ id: 'pack', workflows: [doc.id] })), mode: '100644' },
    { path: `workflows/${doc.id}.yaml`, contentBase64: btoa(yaml), mode: '100644' },
    { path: 'script', contentBase64: btoa('original script'), mode: '100755' },
  ]
  const snapshot = { workflowId: doc.id, packId: 'pack', baseRef: `origin/wf/${doc.id}`, baseCommit: A, draftHead: null, canWrite: true, versionFiles }
  const fetchMock = vi.fn(async (input: string, init?: RequestInit) => {
    if (input === `/api/workflows/${doc.id}`) return route('/:workflowId')
    if (input === `/api/workflows/${doc.id}/export`) return route('/:workflowId/export')
    if (input.includes('/pack-version') && !init?.method) return new Response(JSON.stringify(snapshot))
    if (input === '/api/auto-heal/save' && init?.method === 'POST') {
      const body = JSON.parse(await readBlob(init.body as Blob))
      const yaml = stringify(body.spec)
      row.spec_json = JSON.stringify(storage === 'projection' ? { ...body.spec, content: yaml } : storage === 'wrapper' ? { content: yaml } : body.spec)
      return new Response(JSON.stringify({ ok: true, saved: true, workflowId: doc.id, packId: 'pack' }))
    }
    throw new Error(`Unexpected request ${input}`)
  })
  vi.stubGlobal('fetch', fetchMock)
  return { route, fetchMock, snapshot, row }
}
function readBlob(blob: Blob): Promise<string> { return new Promise(resolve => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result)); reader.readAsText(blob) }) }
function wrapper({ children }: PropsWithChildren) { return <QueryClientProvider client={clients.at(-1)!}>{children}</QueryClientProvider> }
function client() { clients.push(new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })) }

it.each(['projection', 'wrapper', 'json'] as const)('normalizes actual GET %s response before caching, switching, binding and saving', async storage => {
  const doc = document(), { route, fetchMock } = fixture(doc, storage)
  const raw = await (await route('/:workflowId')).json()
  expect(raw.version).toBe(19); expect(raw.updatedAt).toBeGreaterThan(0); expect(raw.facade.command).toBe('binding-command')
  expect(typeof raw.content === 'string').toBe(storage === 'projection')
  useEditorStore.getState().reset({ force: true }); client()
  const { result } = renderHook(() => useEditorWorkflowDocument(doc.id), { wrapper })
  await waitFor(() => expect(result.current.isSuccess).toBe(true))
  const loaded = result.current.data!
  expect(loaded).toEqual(doc)
  expect(prepareWorkflowSession(loaded)).not.toBeNull()
  const approval = beginWorkflowTransition()!
  act(() => { expect(commitWorkflowTransition(approval, loaded, doc.id)).toBe(true) })
  const { result: canvas } = renderHook(() => usePackCanvasSession(doc.id, loaded))
  await waitFor(() => expect(canvas.current.snapshot).toBeTruthy())
  expect(canvas.current.error).toBeNull(); expect(useEditorStore.getState().positions.done).toEqual({ x: 301, y: 201 })
  act(() => useEditorStore.getState().updateSpecField('title', 'Edited title'))
  await act(async () => { await canvas.current.save() })
  const writes = fetchMock.mock.calls.filter(([url, init]) => url === '/api/auto-heal/save' && init?.method === 'POST')
  expect(writes).toHaveLength(1)
  expect(writes[0][1]!.headers).toMatchObject({ 'X-Pack-Version-CSRF': '1' })
  expect(fetchMock.mock.calls.some(([, init]) => init?.method === 'PUT')).toBe(false)
  const body = JSON.parse(await readBlob(writes[0][1]!.body as Blob))
  const submitted = body.spec
  expect(submitted).toMatchObject({ version: '2.7.0', title: 'Edited title', facade: doc.facade, custom: doc.custom })
  expect(submitted).not.toHaveProperty('content'); expect(submitted).not.toHaveProperty('updatedAt')
  expect(body.workflowId).toBe(doc.id)
  expect(useEditorStore.getState()._savedSpec).toEqual(submitted)
})

it.each(['projection', 'wrapper', 'json'] as const)('does not strip genuinely authored content/metadata fields from %s', async storage => {
  const doc = { ...document(), content: 'user payload, not another envelope', updatedAt: 'user field', extra: { nested: true } }
  fixture(doc, storage)
  const loaded = await readWorkflowDocument(doc.id)
  expect(loaded).toEqual(doc)
  expect(sameExecutionSpec(loaded, { ...doc, version: '2.8.0' })).toBe(false)
  expect(sameExecutionSpec(loaded, { ...doc, content: 'different' })).toBe(false)
  expect(sameExecutionSpec(loaded, { ...doc, updatedAt: 'different' })).toBe(false)
  expect(sameExecutionSpec(loaded, { ...doc, facade: { command: 'other' } })).toBe(false)
})
it('never masks a real DB/export version disagreement with Git', async () => {
  const doc = document(); const { snapshot } = fixture({ ...doc, version: '2.6.0' }, 'wrapper')
  const loaded = await readWorkflowDocument(doc.id)
  const files = snapshot.versionFiles.map(file => file.path.startsWith('workflows/') ? { ...file, contentBase64: btoa(stringify(doc)) } : file)
  expect(() => mergeCanvasFiles({ ...snapshot, versionFiles: files } as never, files as never, loaded, loaded)).toThrow('数据库初始文档与 Git 基线不一致')
})
it('allows a genuine numeric source version in a session without changing it to a DB release ordinal', () => {
  const doc = { ...document(), version: 2.7 } as unknown as WorkflowSpec
  const prepared = prepareWorkflowSession(doc)
  expect(prepared!.spec.version).toBe(2.7)
})
it.each(['bad-yaml', 'wrong-id', 'contradiction'])('fails closed on a malformed API content envelope: %s', kind => {
  const doc = document()
  const response = { ...doc, version: 19, updatedAt: 100,
    content: kind === 'bad-yaml' ? 'nodes: [' : stringify(kind === 'wrong-id' ? { ...doc, id: 'other' } : doc),
    ...(kind === 'contradiction' ? { title: 'Conflicting projection' } : {}) }
  expect(() => normalizeWorkflowDocument(response, doc.id)).toThrow()
})
it.each([401, 403, 404])('does not replace a current draft if the original document export returns %s', async status => {
  const doc = document(), { fetchMock } = fixture(doc, 'json')
  const original = fetchMock.getMockImplementation()!
  fetchMock.mockImplementation((url, init) => url.endsWith('/export') ? Promise.resolve(new Response('Unavailable', { status })) : original(url, init))
  useEditorStore.getState().reset({ force: true }); useEditorStore.getState().loadSpec({ ...doc, id: 'previous' })
  useEditorStore.getState().updateSpecField('title', 'Keep draft')
  await expect(readWorkflowDocument(doc.id)).rejects.toThrow('未替换当前草稿')
  expect(useEditorStore.getState().spec?.title).toBe('Keep draft')
})
it('does not manufacture a hybrid document when DB changes between GET and export', async () => {
  const doc = document(), { fetchMock } = fixture(doc, 'json')
  const original = fetchMock.getMockImplementation()!
  fetchMock.mockImplementation((url, init) => url.endsWith('/export')
    ? Promise.resolve(new Response(stringify({ ...doc, title: 'Changed during read' }))) : original(url, init))
  await expect(readWorkflowDocument(doc.id)).rejects.toThrow('投影与原文执行内容不一致')
})

it.each(['wrapper', 'json'] as const)('preserves workflow-shaped user content with a DIFFERENT real version in %s', async storage => {
  const base = document(), doc = { ...base, content: stringify({ ...base, version: '1.0.0' }) }
  fixture(doc, storage)
  const loaded = await readWorkflowDocument(doc.id)
  expect(loaded).toEqual(doc)
  expect(loaded.version).toBe('2.7.0')
  expect(loaded.content).toBe(doc.content)
})
it('permits a legitimate absent source version without substituting the DB release ordinal', async () => {
  const doc = document(); delete doc.version
  fixture(doc, 'projection')
  const loaded = await readWorkflowDocument(doc.id)
  expect(loaded.version).toBeUndefined()
  expect(prepareWorkflowSession(loaded)).not.toBeNull()
})
it('keeps shared RunDetail numeric-version cache isolated from the editor document cache', async () => {
  const doc = document(); fixture(doc, 'projection'); client()
  const { result } = renderHook(() => ({ editor: useEditorWorkflowDocument(doc.id), runtime: useDbWorkflow(doc.id) }), { wrapper })
  await waitFor(() => expect(result.current.editor.isSuccess && result.current.runtime.isSuccess).toBe(true))
  expect(result.current.editor.data!.version).toBe('2.7.0')
  expect(result.current.runtime.data!.version).toBe(19)
  const queryClient = clients.at(-1)!
  expect(queryClient.getQueryData<EditorWorkflowSource>(['editor-workflow-document', doc.id])!.spec.version).toBe('2.7.0')
  expect(queryClient.getQueryData<WorkflowSpec>(['db-workflow', doc.id])!.version).toBe(19)
  await act(async () => { await queryClient.invalidateQueries({ queryKey: ['editor-workflow-document'] }) })
  expect(result.current.runtime.data!.version).toBe(19)
})

it('successful write invalidates only the editor workflow key and preserves the shared numeric cache', async () => {
  const doc = document(), { fetchMock, row } = fixture(doc, 'projection'); client()
  const original = fetchMock.getMockImplementation()!
  fetchMock.mockImplementation(async (url, init) => {
    if (url === '/api/auto-heal/save') {
      const next = { ...doc, title: 'Saved remotely' }
      row.spec_json = JSON.stringify({ ...next, content: stringify(next) })
      return new Response(JSON.stringify({ ok: true, saved: true, workflowId: doc.id, packId: 'pack', branch: `saved/${doc.id}`, commit: A, version: 20 }))
    }
    return original(url, init)
  })
  const { result } = renderHook(() => ({ editor: useEditorWorkflowDocument(doc.id), runtime: useDbWorkflow(doc.id), save: useAutoHealSave() }), { wrapper })
  await waitFor(() => expect(result.current.editor.isSuccess && result.current.runtime.isSuccess).toBe(true))
  await act(async () => { await result.current.save.mutateAsync({ workflowId: doc.id, spec: { ...doc, title: 'Saved remotely' } }) })
  await waitFor(() => expect(result.current.editor.data?.title).toBe('Saved remotely'))
  expect(result.current.editor.data!.version).toBe('2.7.0')
  expect(result.current.runtime.data!.version).toBe(19)
  expect(clients.at(-1)!.getQueryData<WorkflowSpec>(['db-workflow', doc.id])!.version).toBe(19)
})
