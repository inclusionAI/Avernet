import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { stringify } from 'yaml'
import type { WorkflowSpec } from '@avernet/clawweb-shared/web/types'

const mocks = vi.hoisted(() => ({ db: null as WorkflowSpec | null, baseline: null as WorkflowSpec | null, fetchJson: vi.fn(), get: vi.fn(), history: vi.fn(), deploy: vi.fn() }))
vi.mock('../../api/hooks', () => ({
  useEditorWorkflowDocument: () => ({ data: mocks.db, databaseSpec: mocks.baseline, isLoading: false }),
  useAutoHealDeploy: () => ({ mutateAsync: mocks.deploy, isPending: false }),
  useFacadeBindings: () => ({ data: [] }),
}))
vi.mock('@avernet/clawweb-shared/web/api/client', () => ({ fetchJson: mocks.fetchJson,
  api: { workflows: { get: mocks.get, getHistory: mocks.history, getDeploySnapshot: vi.fn() } } }))
vi.mock('../../components/WorkflowPackSidebar', () => ({ default: () => null }))
vi.mock('../../components/EditorCanvas', () => ({ default: () => null }))
vi.mock('../../components/NodePalette', () => ({ default: () => null }))
vi.mock('../../components/NodePropertyPanel', () => ({ default: () => null }))
vi.mock('../../components/editor/WorkflowSettingsPanel', () => ({ default: () => null }))
vi.mock('../../components/YamlEditor', () => ({ default: () => null }))
vi.mock('../../components/WorkflowHistoryPanel', () => ({ default: () => null }))
import { readDatabaseWorkflowDraft } from '../../api/editorWorkflowSource'
import Editor from '../Editor'
import { useEditorStore } from '../../editor/store'
import { publish, sessionFor, updateVersion, type Session } from '../../editor/packVersionSession'
import { editorWorkflowWriteState } from '../../editor/workflowWriteGuard'

let sequence = 0
const file = (path: string, text: string) => ({ path, contentBase64: btoa(text), mode: '100644' as const })
function text(blob: Blob): Promise<string> { return new Promise(resolve => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result)); reader.readAsText(blob) }) }
async function setup(withClient = true) {
  const id = `partial-cache-${++sequence}`, spec = { id, title: 'Original', version: '1', nodes: [] }
  mocks.db = structuredClone(spec)
  mocks.baseline = await readDatabaseWorkflowDraft(id)
  useEditorStore.getState().loadSpec(spec, mocks.baseline)
  mocks.fetchJson.mockClear(); mocks.get.mockClear()
  const files = [file('workflow.pack.yaml', stringify({ id: 'pack', workflows: [id] })), file(`workflows/${id}.yaml`, stringify(spec)), file('scripts/run', 'original')]
  const snapshot = { workflowId: id, packId: 'pack', canWrite: true, baseRef: `origin/wf/${id}`, baseCommit: 'a'.repeat(40), draftHead: 'a'.repeat(40), versionFiles: files }
  const session = sessionFor(id)
  publish(session, { ...session.state, version: 'draft', versions: { ...session.state.versions, draft: { snapshot, files, loading: false, error: null, blocked: false, notice: null } } })
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } })
  for (const key of [['editor-workflow-document', id], ['editor-workflow-document', 'other'], ['db-workflows'], ['workflow-types']]) client.setQueryData(key, { old: true })
  render(withClient ? <QueryClientProvider client={client}><Editor embedded initialWorkflowId={id} /></QueryClientProvider> : <Editor embedded initialWorkflowId={id} />)
  act(() => {
    useEditorStore.getState().updateSpecField('title', 'Saved DB')
    updateVersion(session, 'draft', { files: files.map(f => f.path === 'scripts/run' ? file('scripts/run', 'Local file') : f) })
  })
  return { id, session, client }
}
type UnifiedInput = { requestId: string; sourceVersion: string; sourceCommit: string; expectedSavedHead: string; expectedDraftToken: string; specJson: string; changes: { path: string; contentBase64: string }[] }
// Existing drafts no longer issue separate DB and Git writes. Exercise the
// corresponding failure at the unified POST or its DB/Git postflight instead.
function enableUnifiedSave(handler: (input: UnifiedInput) => Promise<Response> | Response) {
  const fallback = globalThis.fetch
  const submissions: UnifiedInput[] = []
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (url.endsWith('/unified-save') && init?.method === 'GET') return new Response(JSON.stringify({ protocol: 'workflow-unified-save-v1' }))
    if (url.endsWith('/unified-save') && init?.method === 'POST') {
      const input = JSON.parse(await text(init.body as Blob)) as UnifiedInput
      submissions.push(input)
      return handler(input)
    }
    return fallback(url, init)
  }))
  return submissions
}
function expectSubmission(submissions: UnifiedInput[], id: string, title = 'Saved DB') {
  expect(submissions).toHaveLength(1)
  expect(submissions[0]).toMatchObject({ sourceVersion: 'draft', sourceCommit: 'a'.repeat(40), expectedSavedHead: 'a'.repeat(40), expectedDraftToken: 'a'.repeat(64) })
  expect(submissions[0].requestId).toBeTruthy()
  expect(JSON.parse(submissions[0].specJson)).toMatchObject({ id, title })
  expect(submissions[0].changes).toEqual([expect.objectContaining({ path: 'scripts/run', contentBase64: btoa('Local file') })])
  expect(mocks.fetchJson.mock.calls.filter(([, init]) => init?.method === 'PUT' || init?.method === 'POST')).toHaveLength(0)
}
function expectUncertain(id: string, session: Session, client?: QueryClient, canvas = 'Saved DB') {
  expect(session.state.postSaveVerificationError).toContain('统一保存未能完整确认')
  expect(editorWorkflowWriteState(id).phase).toBe('unknown')
  expect(sessionStorage.getItem(`avernet.editor.pending-workflow-write.v1:${encodeURIComponent(id)}`)).not.toBeNull()
  expect(session.state.versions.draft.snapshot?.baseCommit).toBe('a'.repeat(40))
  expect(session.state.versions.draft.files.find(f => f.path === 'scripts/run')?.contentBase64).toBe(btoa('Local file'))
  const editor = useEditorStore.getState()
  expect(editor.spec?.title).toBe(canvas)
  if (editor.spec?.id === id) {
    expect(editor._databaseSpec?.title).toBe('Original')
    expect(editor._savedSpec?.title).toBe('Original')
    if (canvas === 'Saved DB') expect(editor.isDirty).toBe(true)
  }
  if (client) {
    expect(invalidated(client, ['editor-workflow-document', id])).toBe(true)
    expect(invalidated(client, ['db-workflows'])).toBe(true)
    expect(invalidated(client, ['workflow-types'])).toBe(true)
    expect(invalidated(client, ['editor-workflow-document', 'other'])).toBe(false)
  }
  expect(screen.queryByText(/数据库和草稿\(wf\)分支已确认保存/)).not.toBeInTheDocument()
}
const confirmed = (input: UnifiedInput) => ({ requestId: input.requestId, outcome: 'confirmed', reconciliationRequired: false,
  evidence: { db: 'confirmed', git: 'confirmed', inputHash: 'e'.repeat(64), commit: 'b'.repeat(40), draftToken: 'b'.repeat(64) } })
function invalidated(client: QueryClient, key: unknown[]) { return client.getQueryState(key)?.isInvalidated }
beforeEach(() => {
  vi.resetAllMocks(); useEditorStore.getState().reset({ force: true })
  mocks.history.mockResolvedValue({ history: [], active: null })
  mocks.get.mockImplementation(async () => structuredClone(mocks.db))
  vi.spyOn(window, 'confirm').mockReturnValue(true)
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (url.endsWith('/unified-save') && init?.method === 'GET') return new Response(null, { status: 404 })
    return new Response(JSON.stringify(await mocks.fetchJson(url, init)))
  }))
  mocks.fetchJson.mockImplementation(async (url, init) => {
    if (url.endsWith('/editor-draft') && init?.method === 'GET') {
      return { workflowId: mocks.db!.id, packId: 'pack', specJson: JSON.stringify(mocks.db), draftToken: 'a'.repeat(64) }
    }
    throw new Error(`Unexpected request ${url}`)
  })
})
afterEach(() => { cleanup(); useEditorStore.getState().reset({ force: true }); vi.restoreAllMocks(); vi.unstubAllGlobals() })

it('probes capability and blocks legacy writes when unified save is unavailable', async () => {
  const { id, session, client } = await setup()
  vi.mocked(window.confirm).mockReturnValue(false)
  fireEvent.click(screen.getByRole('button', { name: '保存', exact: true }))
  expect(await screen.findByText(/统一保存不可用，无法同步草稿\(wf\)工作流 YAML/)).toBeInTheDocument()
  const calls = vi.mocked(fetch).mock.calls.filter(([url]) => String(url).endsWith('/unified-save'))
  expect(calls).toHaveLength(1)
  expect(calls[0][0]).toBe(`/api/workflows/${id}/unified-save`)
  expect(calls[0][1]).toMatchObject({ method: 'GET', cache: 'no-store', headers: { 'X-Pack-Version-CSRF': '1' } })
  expect(window.confirm).not.toHaveBeenCalled()
  expect(mocks.fetchJson.mock.calls.filter(([, init]) => init?.method === 'PUT' || init?.method === 'POST')).toHaveLength(0)
  expect(mocks.db?.title).toBe('Original')
  expect(useEditorStore.getState().isDirty).toBe(true)
  expect(session.state.versions.draft.files.find(f => f.path === 'scripts/run')?.contentBase64).toBe(btoa('Local file'))
  expect(invalidated(client, ['editor-workflow-document', id])).toBe(false)
  expect(invalidated(client, ['db-workflows'])).toBe(false)
  expect(invalidated(client, ['workflow-types'])).toBe(false)
  expect(invalidated(client, ['editor-workflow-document', 'other'])).toBe(false)
})

// The former DB-success/Git-failure cases now use partial unified receipts.
it.each([
  ['partial Git conflict', 409, { outcome: 'partial', reconciliationRequired: true, evidence: { db: 'confirmed', git: 'unknown' } }],
  ['partial Git failure', 500, { outcome: 'partial', reconciliationRequired: true, evidence: { db: 'confirmed', git: 'not_started' } }],
])('invalidates source caches after a unified %s while preserving both candidates', async (_name, status, response) => {
  const { id, session, client } = await setup()
  const submissions = enableUnifiedSave(input => new Response(JSON.stringify({ requestId: input.requestId, ...response }), { status }))
  fireEvent.click(screen.getByRole('button', { name: '保存', exact: true }))
  await waitFor(() => expect(invalidated(client, ['editor-workflow-document', id])).toBe(true))
  expectSubmission(submissions, id)
  expectUncertain(id, session, client)
  expect(session.state.postSaveVerificationError).toContain('统一保存结果为 partial')
  expect(mocks.db?.title).toBe('Original') // A response claim alone is not a verified local DB baseline.
  fireEvent.click(screen.getByRole('button', { name: '保存', exact: true }))
  await waitFor(() => expect(screen.getByText(/数据库写入结果未终结/)).toBeInTheDocument())
  expect(submissions).toHaveLength(1)
})
it('does not invalidate as a DB success when the baseline check fails before a write', async () => {
  const { id, client } = await setup()
  const originalFetch = globalThis.fetch
  vi.stubGlobal('fetch', vi.fn((url: string, init?: RequestInit) => url.endsWith('/unified-save') && init?.method === 'GET'
    ? Promise.resolve(new Response(JSON.stringify({ protocol: 'workflow-unified-save-v1' }))) : originalFetch(url, init)))
  mocks.fetchJson.mockRejectedValue(new Error('offline'))
  fireEvent.click(screen.getByRole('button', { name: '保存', exact: true }))
  await screen.findByText(/无法核对数据库草稿/)
  expect(mocks.fetchJson.mock.calls.filter(([, init]) => init?.method === 'PUT' || init?.method === 'POST')).toHaveLength(0)
  expect(vi.mocked(fetch).mock.calls.filter(([url, init]) => String(url).endsWith('/unified-save') && init?.method === 'POST')).toHaveLength(0)
  expect(editorWorkflowWriteState(id).phase).toBe('clear')
  expect(invalidated(client, ['editor-workflow-document', id])).toBe(false)
  expect(invalidated(client, ['db-workflows'])).toBe(false)
  expect(invalidated(client, ['workflow-types'])).toBe(false)
})
it('quarantines a file-only unified rejection without claiming a DB success', async () => {
  const { id, client, session } = await setup()
  act(() => useEditorStore.getState().updateSpecField('title', 'Original'))
  const submissions = enableUnifiedSave(input => new Response(JSON.stringify({ requestId: input.requestId, outcome: 'rejected',
    reconciliationRequired: false, evidence: { db: 'not_started', git: 'not_started' } }), { status: 409 }))
  fireEvent.click(screen.getByRole('button', { name: '保存', exact: true }))
  await waitFor(() => expect(invalidated(client, ['editor-workflow-document', id])).toBe(true))
  expectSubmission(submissions, id, 'Original')
  expectUncertain(id, session, client, 'Original')
  expect(session.state.postSaveVerificationError).toContain('统一保存结果为 rejected')
  expect(mocks.db?.title).toBe('Original')
})
it('invalidates only the original workflow after a late unified failure and keeps the replacement session', async () => {
  const { id, client, session } = await setup()
  let rejectPost!: (error: Error) => void
  const submissions = enableUnifiedSave(() => new Promise((_resolve, reject) => { rejectPost = reject }))
  fireEvent.click(screen.getByRole('button', { name: '保存', exact: true }))
  await waitFor(() => expect(rejectPost).toBeTypeOf('function'))
  expect(editorWorkflowWriteState(id).phase).toBe('pending')
  act(() => { useEditorStore.getState().reset({ force: true }); useEditorStore.getState().loadSpec({ id: 'other', title: 'Other session', version: '1', nodes: [] }) })
  await act(async () => { rejectPost(new Error('connection lost')); await Promise.resolve() })
  await waitFor(() => expect(invalidated(client, ['editor-workflow-document', id])).toBe(true))
  expectSubmission(submissions, id)
  expectUncertain(id, session, client, 'Other session')
  expect(useEditorStore.getState().spec?.id).toBe('other')
  expect(screen.queryByText(/统一保存未能完整确认/)).not.toBeInTheDocument()
})
it('reports an uncertain partial unified outcome without a query client', async () => {
  const { id, session } = await setup(false)
  const submissions = enableUnifiedSave(input => new Response(JSON.stringify({ requestId: input.requestId, outcome: 'partial',
    reconciliationRequired: true, evidence: { db: 'confirmed', git: 'unknown' } }), { status: 200 }))
  fireEvent.click(screen.getByRole('button', { name: '保存', exact: true }))
  await waitFor(() => expect(session.state.postSaveVerificationError).toContain('统一保存结果为 partial'))
  expectSubmission(submissions, id)
  expectUncertain(id, session)
  await waitFor(() => expect(screen.getByText(/统一保存结果为 partial/)).toBeVisible())
})

// A separate DB write receipt is now either the unified receipt or the
// subsequent DB/Git postflight; neither alone can establish complete success.
it.each(['invalid JSON', 'postflight DB content differs'] as const)('invalidates source caches for unified %s', async mode => {
  const { id, client, session } = await setup()
  const original = mocks.fetchJson.getMockImplementation()!
  let postflightReads = 0
  const submissions = enableUnifiedSave(input => {
    if (mode === 'invalid JSON') return new Response('{', { status: 200 })
    const savedSpec = JSON.parse(input.specJson) as WorkflowSpec
    mocks.db = { ...savedSpec, title: 'Different on server' }
    const savedFiles = session.state.versions.draft.files.map(f => f.path === `workflows/${id}.yaml`
      ? file(f.path, stringify(savedSpec, { sortMapEntries: true, lineWidth: 0 })) : f)
    const saved = { ...session.state.versions.draft.snapshot!, baseCommit: 'b'.repeat(40), draftHead: 'b'.repeat(40), versionFiles: savedFiles }
    mocks.fetchJson.mockImplementation((url, init) => {
      if (url.endsWith('/editor-draft') && init?.method === 'GET') {
        postflightReads++
        return Promise.resolve({ workflowId: id, packId: 'pack', specJson: JSON.stringify(mocks.db), draftToken: 'b'.repeat(64) })
      }
      if (url.endsWith('version=draft')) { postflightReads++; return Promise.resolve(saved) }
      return original(url, init)
    })
    return new Response(JSON.stringify(confirmed(input)))
  })
  fireEvent.click(screen.getByRole('button', { name: '保存', exact: true }))
  await waitFor(() => expect(invalidated(client, ['editor-workflow-document', id])).toBe(true))
  expectSubmission(submissions, id)
  expectUncertain(id, session, client)
  if (mode === 'invalid JSON') expect(session.state.postSaveVerificationError).toContain('响应不可用')
  else { expect(postflightReads).toBe(2); expect(session.state.postSaveVerificationError).toContain('postflight mismatch') }
})
// Unified receipts correlate by requestId, rather than carrying workflowId.
it.each(['wrong request', 'missing token', 'no response'] as const)('retains drafts for an unverifiable unified outcome: %s', async mode => {
  const { id, client, session } = await setup()
  const submissions = enableUnifiedSave(input => {
    if (mode === 'no response') throw new Error('network')
    const receipt = confirmed(input)
    if (mode === 'wrong request') receipt.requestId = 'other-request'
    if (mode === 'missing token') Reflect.deleteProperty(receipt.evidence, 'draftToken')
    return new Response(JSON.stringify(receipt))
  })
  fireEvent.click(screen.getByRole('button', { name: '保存', exact: true }))
  await waitFor(() => expect(invalidated(client, ['editor-workflow-document', id])).toBe(true))
  expectSubmission(submissions, id)
  expectUncertain(id, session, client)
  expect(session.state.postSaveVerificationError).toContain(mode === 'no response' ? 'network' : mode === 'missing token' ? '统一保存结果为 confirmed' : '响应无效')
})
it('retains both drafts and does not invalidate caches when identity writes are unsupported', async () => {
  const { id, client, session } = await setup()
  vi.mocked(fetch).mockImplementation(async (url, init) => {
    if (String(url).endsWith('/unified-save') && init?.method === 'GET') return new Response(JSON.stringify({ protocol: 'workflow-unified-save-v1' }))
    throw new Error(`Unexpected request ${url}`)
  })
  act(() => updateVersion(session, 'draft', { files: session.state.versions.draft.files.map(f => f.path === 'workflow.pack.yaml' ? file(f.path, stringify({ id: 'renamed', workflows: [id] })) : f) }))
  fireEvent.click(screen.getByRole('button', { name: '保存', exact: true }))
  expect(await screen.findByText(/统一保存暂不支持修改 Pack manifest/)).toHaveAttribute('role', 'alert')
  expect(session.state.uncertainRename).toBeNull()
  expect(mocks.fetchJson).not.toHaveBeenCalled()
  expect(session.state.versions.draft.files.find(f => f.path === 'scripts/run')?.contentBase64).toBe(btoa('Local file'))
  expect(useEditorStore.getState().isDirty).toBe(true)
  expect(invalidated(client, ['editor-workflow-document', id])).toBe(false)
  expect(invalidated(client, ['db-workflows'])).toBe(false)
})
