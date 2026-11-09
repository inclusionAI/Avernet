import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { stringify } from 'yaml'
import type { WorkflowSpec } from '@avernet/clawweb-shared/web/types'

const transport = vi.hoisted(() => ({ get: vi.fn(), history: vi.fn(), fetchJson: vi.fn(), deploy: vi.fn() }))
vi.mock('../../api/hooks', async () => ({
  useEditorWorkflowDocument: (await import('../../api/useEditorWorkflowDocument')).useEditorWorkflowDocument,
  useAutoHealDeploy: () => ({ mutateAsync: transport.deploy, isPending: false }),
  useFacadeBindings: () => ({ data: [] }),
}))
vi.mock('@avernet/clawweb-shared/web/api/client', () => ({ fetchJson: transport.fetchJson,
  api: { workflows: { get: transport.get, getHistory: transport.history, getDeploySnapshot: vi.fn() } } }))
vi.mock('../../components/WorkflowPackSidebar', () => ({ default: () => null }))
vi.mock('../../components/EditorCanvas', async () => {
  const { useEditorStore } = await import('../../editor/store')
  return { default: () => <div data-testid="canvas">{useEditorStore(s => s.spec?.title)}</div> }
})
vi.mock('../../components/NodePalette', () => ({ default: () => null }))
vi.mock('../../components/NodePropertyPanel', () => ({ default: () => null }))
vi.mock('../../components/editor/WorkflowSettingsPanel', () => ({ default: () => null }))
vi.mock('../../components/YamlEditor', () => ({ default: () => null }))
vi.mock('../../components/WorkflowHistoryPanel', () => ({ default: () => null }))

import Editor from '../Editor'
import { readEditorWorkflowSource } from '../../api/editorWorkflowSource'
import { beginEditorWorkflowWrite, editorWorkflowWriteState, finishEditorWorkflowWrite } from '../../editor/workflowWriteGuard'
import { saveVerifiedWorkflowDocument } from '../../editor/workflowDocumentSave'
import { beginWorkflowTransition, commitWorkflowTransition, prepareWorkflowTransition } from '../../editor/workflowTransition'
import { useEditorStore } from '../../editor/store'
import { publish, sessionFor } from '../../editor/packVersionSession'

function deferred<T>() {
  let resolve!: (value: T) => void, reject!: (reason: unknown) => void
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}
function blobText(blob: Blob): Promise<string> { return new Promise(resolve => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result)); reader.readAsText(blob) }) }
let seq = 0
let db: WorkflowSpec, client: QueryClient, gate: ReturnType<typeof deferred<void>> | null, oldGate: ReturnType<typeof deferred<void>> | null, oldStarted: boolean, writes: number, responseGate: ReturnType<typeof deferred<void>> | null, conflict: boolean, success: boolean, lateCommit: boolean
const row = (spec: WorkflowSpec) => ({ workflowId: spec.id, packId: 'pack', specJson: JSON.stringify(spec), draftToken: (spec.title === 'Original' ? 'a' : 'b').repeat(64) })

beforeEach(() => {
  vi.resetAllMocks(); useEditorStore.getState().reset({ force: true })
  db = { id: `uncertain-${++seq}`, title: 'Original', version: '1', nodes: [] }
  gate = null; oldGate = null; oldStarted = false; writes = 0; responseGate = null; conflict = false; success = false; lateCommit = false
  client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity, placeholderData: previous => previous } } })
  transport.get.mockImplementation(async () => ({ id: db.id }))
  transport.history.mockResolvedValue({ history: [], active: null })
  transport.fetchJson.mockImplementation(async (url: string, init?: RequestInit) => {
    if (!url.endsWith('/editor-draft') || init?.method !== 'GET') throw new Error(`Unexpected ${url}`)
    const snapshot = structuredClone(db)
    if (oldGate && !oldStarted) { oldStarted = true; await oldGate.promise }
    else if (gate) await gate.promise
    return row(snapshot)
  })
  vi.stubGlobal('fetch', vi.fn(async (url: string, init: RequestInit) => {
    if (url.endsWith('/unified-save') && init?.method === 'GET') return new Response(JSON.stringify({ protocol: 'workflow-unified-save-v1' }))
    if (init?.method !== 'POST' && init?.method !== 'PUT') throw new Error(`Unexpected fetch ${url}`)
    writes++
    if (conflict) return new Response(JSON.stringify({ code: 'DRAFT_CONFLICT' }), { status: 409 })
    const request = JSON.parse(await blobText(init.body as Blob))
    if (!lateCommit) db = init.method === 'POST' ? JSON.parse(request.specJson) : request.spec
    if (responseGate) await responseGate.promise
    if (success && init.method === 'PUT') return new Response(JSON.stringify(row(db)), { status: 200 })
    throw new Error('connection lost')
  }))
})
afterEach(() => { cleanup(); useEditorStore.getState().reset({ force: true }); client.clear(); vi.unstubAllGlobals() })

async function setup() {
  const id = db.id
  const files = [
    { path: 'workflow.pack.yaml', contentBase64: btoa(stringify({ id: 'pack', workflows: [id] })), mode: '100644' as const },
    { path: `workflows/${id}.yaml`, contentBase64: btoa(stringify(db)), mode: '100644' as const },
  ]
  const snapshot = { packPath: '', workflowId: id, packId: 'pack', canWrite: true, baseRef: `origin/wf/${id}`, baseCommit: 'a'.repeat(40), draftHead: 'a'.repeat(40), versionFiles: files }
  const session = sessionFor(id)
  publish(session, { ...session.state, version: 'draft', versions: { ...session.state.versions, draft: { snapshot, files, loading: false, error: null, blocked: false, notice: null } } })
  const page = render(<QueryClientProvider client={client}><Editor embedded initialWorkflowId={id} /></QueryClientProvider>)
  await waitFor(() => expect(useEditorStore.getState().spec?.title).toBe('Original'))
  act(() => useEditorStore.getState().updateSpecField('title', 'Committed'))
  return { id, page }
}

it('blocks two real QueryClients before a pending write settles, then remains blocked after response loss', async () => {
  const { id, page } = await setup()
  const second = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } })
  const cached = await readEditorWorkflowSource(id)
  second.setQueryData(['editor-workflow-document', id], cached)
  oldGate = deferred<void>()
  const oldRead = client.fetchQuery({ queryKey: ['editor-workflow-document', id], queryFn: () => readEditorWorkflowSource(id), staleTime: 0 }).then(value => ({ value }), error => ({ error }))
  await waitFor(() => expect(oldStarted).toBe(true))
  responseGate = deferred<void>()
  fireEvent.click(screen.getByRole('button', { name: '保存', exact: true }))
  await waitFor(() => expect(writes).toBe(1))
  expect(editorWorkflowWriteState(id).phase).toBe('pending')
  expect(useEditorStore.getState().spec?.title).toBe('Committed')
  expect(useEditorStore.getState().isDirty).toBe(true)
  page.unmount(); act(() => useEditorStore.getState().reset({ force: true }))
  render(<QueryClientProvider client={second}><Editor embedded initialWorkflowId={id} /></QueryClientProvider>)
  expect(screen.getByText('加载工作流…')).toBeInTheDocument()
  expect(useEditorStore.getState().spec).toBeNull()
  await act(async () => { oldGate!.resolve(); await oldRead })
  expect((await oldRead).error).toBeInstanceOf(Error)
  expect(useEditorStore.getState().spec).toBeNull()
  await act(async () => { responseGate!.resolve(); await responseGate!.promise })
  expect(await screen.findByText(/数据库写入结果未终结/)).toBeInTheDocument()
  expect(useEditorStore.getState().spec).toBeNull()
  expect(writes).toBe(1)
  second.clear()
})

it('does not unlock when a GET can happen before the server commits late', async () => {
  const { id, page } = await setup(); lateCommit = true
  fireEvent.click(screen.getByRole('button', { name: '保存', exact: true }))
  await screen.findByText(/统一保存未能完整确认/)
  expect(db.title).toBe('Original')
  expect(editorWorkflowWriteState(id).phase).toBe('unknown')
  await expect(readEditorWorkflowSource(id)).rejects.toThrow(/人工核对/)
  expect(useEditorStore.getState().spec?.title).toBe('Committed')
  expect(useEditorStore.getState().isDirty).toBe(true)
  db = { ...db, title: 'Committed late' }
  page.unmount(); act(() => useEditorStore.getState().reset({ force: true }))
  render(<QueryClientProvider client={client}><Editor embedded initialWorkflowId={id} /></QueryClientProvider>)
  expect(await screen.findByText(/数据库写入结果未终结/)).toBeInTheDocument()
  expect(useEditorStore.getState().spec).toBeNull()
  expect(writes).toBe(1)
})

it('keeps later edits and blocks repeat saves in the original dirty session', async () => {
  const { id } = await setup()
  fireEvent.click(screen.getByRole('button', { name: '保存', exact: true }))
  await screen.findByText(/统一保存未能完整确认/)
  act(() => useEditorStore.getState().updateSpecField('title', 'Later local edit'))
  fireEvent.click(screen.getByRole('button', { name: '保存', exact: true }))
  expect((await screen.findAllByText(/数据库写入结果未终结/)).length).toBeGreaterThan(0)
  expect(editorWorkflowWriteState(id).phase).toBe('unknown')
  expect(useEditorStore.getState().spec?.title).toBe('Later local edit')
  expect(useEditorStore.getState().isDirty).toBe(true)
  expect(writes).toBe(1)
})

it('clears the DB-only CAS guard for a verified pre-write conflict without losing the draft', async () => {
  const { id } = await setup(); conflict = true
  const source = await readEditorWorkflowSource(id)
  await expect(saveVerifiedWorkflowDocument(id, source.databaseSpec, { ...source.spec, title: 'Committed' }, () => true,
    { expectedRawPackId: 'pack' })).rejects.toThrow('数据库草稿保存冲突')
  expect(editorWorkflowWriteState(id).phase).toBe('clear')
  expect((await readEditorWorkflowSource(id)).spec.title).toBe('Original')
  expect(useEditorStore.getState().spec?.title).toBe('Committed')
  expect(useEditorStore.getState().isDirty).toBe(true)
  expect(writes).toBe(1)
})

it('quarantines an unverified unified conflict instead of trusting a possible proxy response', async () => {
  const { id } = await setup(); conflict = true
  fireEvent.click(screen.getByRole('button', { name: '保存', exact: true }))
  await screen.findByText(/统一保存未能完整确认/)
  expect(editorWorkflowWriteState(id).phase).toBe('unknown')
  await expect(readEditorWorkflowSource(id)).rejects.toThrow(/人工核对/)
  expect(useEditorStore.getState().spec?.title).toBe('Committed')
  expect(useEditorStore.getState().isDirty).toBe(true)
  expect(writes).toBe(1)
})

it('unlocks after verified success and rejects a pre-write prepared transition at final commit', async () => {
  const { id } = await setup(); success = true; responseGate = deferred<void>()
  vi.spyOn(window, 'confirm').mockReturnValue(true)
  const source = await readEditorWorkflowSource(id)
  const approval = beginWorkflowTransition()!
  const prepared = prepareWorkflowTransition(approval, source.spec, id)!
  const save = saveVerifiedWorkflowDocument(id, source.databaseSpec, { ...source.spec, title: 'Committed' }, () => true, { expectedRawPackId: 'pack' })
  await waitFor(() => expect(writes).toBe(1))
  await expect(readEditorWorkflowSource(id)).rejects.toThrow(/正在进行/)
  expect(commitWorkflowTransition(approval, prepared, id, false, source.databaseSpec)).toBe(false)
  await act(async () => { responseGate!.resolve(); await save })
  expect(editorWorkflowWriteState(id).phase).toBe('clear')
  expect(commitWorkflowTransition(approval, prepared, id, false, source.databaseSpec)).toBe(false)
  expect((await readEditorWorkflowSource(id)).spec.title).toBe('Committed')
  vi.restoreAllMocks()
})

it('rejects a source read that was already in flight when the write started', async () => {
  const { id } = await setup(); success = true
  const baseline = await readEditorWorkflowSource(id)
  oldGate = deferred<void>()
  const pending = readEditorWorkflowSource(id).then(value => ({ value }), error => ({ error }))
  await waitFor(() => expect(oldStarted).toBe(true))
  await saveVerifiedWorkflowDocument(id, baseline.databaseSpec, { ...baseline.spec, title: 'Committed' }, () => true, { expectedRawPackId: 'pack' })
  await act(async () => { oldGate!.resolve(); await pending })
  expect((await pending).error).toBeInstanceOf(Error)
})

it('does not dispatch a PUT when the pre-dispatch marker cannot be persisted', async () => {
  const { id } = await setup()
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('quota') })
  fireEvent.click(screen.getByRole('button', { name: '保存', exact: true }))
  expect(await screen.findByText(/无法安全记录数据库写入状态/)).toBeInTheDocument()
  expect(writes).toBe(0)
  expect(editorWorkflowWriteState(id).phase).toBe('unknown')
  expect(useEditorStore.getState().spec?.title).toBe('Committed')
  expect(useEditorStore.getState().isDirty).toBe(true)
  vi.restoreAllMocks()
})


it('does not deploy a clean-looking old draft while an earlier DB write has unknown outcome', async () => {
  const { id } = await setup(); lateCommit = true
  fireEvent.click(screen.getByRole('button', { name: '保存', exact: true }))
  await screen.findByText(/统一保存未能完整确认/)
  act(() => useEditorStore.getState().updateSpecField('title', 'Original'))
  expect(useEditorStore.getState().isDirty).toBe(false)
  fireEvent.click(screen.getByRole('button', { name: '部署', exact: true }))
  expect(await screen.findByText('版本文件存在未保存更改或保存状态不确定，请先处理后再部署。')).toBeInTheDocument()
  expect(screen.getByText(/数据库写入结果未终结，需要人工核对；不能将读取结果视为已确认基线/)).toBeInTheDocument()
  expect(transport.deploy).not.toHaveBeenCalled()
  expect(editorWorkflowWriteState(id).phase).toBe('unknown')
})

it('rejects a deployment candidate if a DB write occurs between confirmation and dispatch', async () => {
  const { id } = await setup()
  act(() => useEditorStore.getState().updateSpecField('title', 'Original'))
  const original = transport.fetchJson.getMockImplementation()!
  transport.fetchJson.mockImplementation(async (url: string, init?: RequestInit) => {
    if (url.includes('/pack-version?version=draft')) return sessionFor(id).state.versions.draft.snapshot
    if (url.includes('/snapshot-v2/state')) return { protocol: 'editor-deployment-state-v1', workflowId: id, packId: 'pack', canDeploy: true, savedCommit: 'a'.repeat(40), activeCommit: null }
    return original(url, init)
  })
  fireEvent.click(screen.getByRole('button', { name: '部署', exact: true }))
  await screen.findByRole('button', { name: '确认部署' })
  act(() => { const owner = beginEditorWorkflowWrite(id); finishEditorWorkflowWrite(id, owner) })
  fireEvent.click(screen.getByRole('checkbox', { name: /我已核对/ }))
  fireEvent.click(screen.getByRole('button', { name: '确认部署', exact: true }))
  expect(await screen.findByText(/数据库写入状态已变化/)).toBeInTheDocument()
  expect(transport.deploy).not.toHaveBeenCalled()
})
