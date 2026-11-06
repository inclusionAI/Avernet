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
import { publish, sessionFor, updateVersion } from '../../editor/packVersionSession'

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
  const snapshot = { workflowId: id, packId: 'pack', canWrite: true, baseRef: `origin/saved/${id}`, baseCommit: 'a'.repeat(40), draftHead: 'a'.repeat(40), versionFiles: files }
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
const isGitPut = (url: string, init?: RequestInit) => init?.method === 'PUT' && !url.endsWith('/editor-draft')
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
    if (url.endsWith('/editor-draft')) {
      if (init?.method === 'GET') return { workflowId: mocks.db!.id, packId: 'pack', specJson: JSON.stringify(mocks.db), draftToken: 'a'.repeat(64) }
      const input = JSON.parse(await text(init.body as Blob)); expect(input.expectedDraftToken).toBe('a'.repeat(64)); mocks.db = input.spec
      return { workflowId: mocks.db!.id, packId: 'pack', specJson: JSON.stringify(mocks.db), draftToken: 'b'.repeat(64) }
    }
    if (url === '/api/auto-heal/save') {
      const input = JSON.parse(await text(init.body as Blob)); mocks.db = input.spec
      return { ok: true, saved: true, workflowId: input.workflowId, packId: 'pack' }
    }
    throw new Error(`Unexpected request ${url}`)
  })
})
afterEach(() => { cleanup(); useEditorStore.getState().reset({ force: true }); vi.restoreAllMocks(); vi.unstubAllGlobals() })

it('probes capability before asking for compatibility consent and sends no write on cancellation', async () => {
  const { id, session, client } = await setup()
  vi.mocked(window.confirm).mockReturnValue(false)
  fireEvent.click(screen.getByRole('button', { name: '保存', exact: true }))
  expect(await screen.findByText(/已取消兼容保存；未发送写入/)).toBeInTheDocument()
  const calls = vi.mocked(fetch).mock.calls.filter(([url]) => String(url).endsWith('/unified-save'))
  expect(calls).toHaveLength(1)
  expect(calls[0][0]).toBe(`/api/workflows/${id}/unified-save`)
  expect(calls[0][1]).toMatchObject({ method: 'GET', cache: 'no-store', headers: { 'X-Pack-Version-CSRF': '1' } })
  expect(window.confirm).toHaveBeenCalledWith(expect.stringContaining('两阶段不是原子操作'))
  expect(vi.mocked(fetch).mock.invocationCallOrder[0]).toBeLessThan(vi.mocked(window.confirm).mock.invocationCallOrder[0])
  expect(mocks.fetchJson.mock.calls.filter(([, init]) => init?.method === 'PUT' || init?.method === 'POST')).toHaveLength(0)
  expect(mocks.db?.title).toBe('Original')
  expect(useEditorStore.getState().isDirty).toBe(true)
  expect(session.state.versions.draft.files.find(f => f.path === 'scripts/run')?.contentBase64).toBe(btoa('Local file'))
  expect(invalidated(client, ['editor-workflow-document', id])).toBe(false)
  expect(invalidated(client, ['db-workflows'])).toBe(false)
  expect(invalidated(client, ['workflow-types'])).toBe(false)
  expect(invalidated(client, ['editor-workflow-document', 'other'])).toBe(false)
})

it.each([
  ['conflict', { status: 409, body: '{"code":"VERSION_CONFLICT"}' }],
  ['failure', { status: 500, body: '{"code":"REQUEST_FAILED"}' }],
])('invalidates the persisted DB caches after Git %s while preserving the candidate', async (_name, failure) => {
  const { id, session, client } = await setup(), original = mocks.fetchJson.getMockImplementation()!
  mocks.fetchJson.mockImplementation((url, init) => isGitPut(url, init) ? Promise.reject(failure) : original(url, init))
  fireEvent.click(screen.getByRole('button', { name: '保存', exact: true }))
  expect(await screen.findByText(/数据库已保存；/)).toBeInTheDocument()
  expect(mocks.db?.title).toBe('Saved DB')
  const writes = mocks.fetchJson.mock.calls.filter(([, init]) => init?.method === 'PUT')
  expect(writes.map(([url]) => url.endsWith('/editor-draft') ? 'DB' : 'Git')).toEqual(['DB', 'Git'])
  expect(session.state.versions.draft.files.find(f => f.path === 'scripts/run')?.contentBase64).toBe(btoa('Local file'))
  expect(invalidated(client, ['editor-workflow-document', id])).toBe(true)
  expect(invalidated(client, ['db-workflows'])).toBe(true)
  expect(invalidated(client, ['workflow-types'])).toBe(true)
  expect(invalidated(client, ['editor-workflow-document', 'other'])).toBe(false)
})
it('does not invalidate as a DB success when the baseline check fails before a write', async () => {
  const { id, client } = await setup()
  mocks.fetchJson.mockRejectedValue(new Error('offline'))
  fireEvent.click(screen.getByRole('button', { name: '保存', exact: true }))
  await screen.findByText(/无法核对数据库草稿/)
  expect(mocks.fetchJson.mock.calls.filter(([, init]) => init?.method === 'PUT')).toHaveLength(0)
  expect(invalidated(client, ['editor-workflow-document', id])).toBe(false)
  expect(invalidated(client, ['db-workflows'])).toBe(false)
})
it('does not report a DB write for a Git-only failure and conservatively invalidates its sources', async () => {
  const { id, client, session } = await setup(), original = mocks.fetchJson.getMockImplementation()!
  act(() => useEditorStore.getState().updateSpecField('title', 'Original'))
  mocks.fetchJson.mockImplementation((url, init) => isGitPut(url, init) ? Promise.reject({ status: 409, body: '{"code":"VERSION_CONFLICT"}' }) : original(url, init))
  fireEvent.click(screen.getByRole('button', { name: '保存', exact: true }))
  await screen.findByText(/Git 工作流 YAML 未更新，请勿盲目重试/)
  expect(screen.queryByText(/数据库已保存；/)).not.toBeInTheDocument()
  expect(mocks.fetchJson.mock.calls.filter(([url]) => url === '/api/auto-heal/save')).toHaveLength(0)
  expect(mocks.fetchJson.mock.calls.filter(([url, init]) => url.endsWith('/editor-draft') && init?.method === 'PUT')).toHaveLength(0)
  expect(mocks.db?.title).toBe('Original')
  expect(mocks.fetchJson.mock.calls.filter(([url, init]) => isGitPut(url, init))).toHaveLength(1)
  expect(session.state.versions.draft.files.find(f => f.path === 'scripts/run')?.contentBase64).toBe(btoa('Local file'))
  // Git's outcome cannot be proven from the error; Editor quarantines the
  // workflow and invalidates its source caches without reporting a DB write.
  expect(invalidated(client, ['editor-workflow-document', id])).toBe(true)
  expect(invalidated(client, ['db-workflows'])).toBe(true)
  expect(invalidated(client, ['workflow-types'])).toBe(true)
  expect(invalidated(client, ['editor-workflow-document', 'other'])).toBe(false)
})
it('invalidates only the original workflow when the editor session changes before Git fails', async () => {
  const { id, client, session } = await setup(), original = mocks.fetchJson.getMockImplementation()!
  let rejectGit!: (error: unknown) => void
  mocks.fetchJson.mockImplementation((url, init) => isGitPut(url, init) ? new Promise((_resolve, reject) => { rejectGit = reject }) : original(url, init))
  fireEvent.click(screen.getByRole('button', { name: '保存', exact: true }))
  await waitFor(() => expect(rejectGit).toBeTypeOf('function'))
  act(() => { useEditorStore.getState().reset({ force: true }); useEditorStore.getState().loadSpec({ id: 'other', title: 'Other session', version: '1', nodes: [] }) })
  await act(async () => { rejectGit({ status: 409, body: '{"code":"VERSION_CONFLICT"}' }); await Promise.resolve() })
  await waitFor(() => expect(session.state.saving).toBe(false))
  expect(invalidated(client, ['editor-workflow-document', id])).toBe(true)
  expect(invalidated(client, ['editor-workflow-document', 'other'])).toBe(false)
  expect(useEditorStore.getState().spec?.title).toBe('Other session')
  expect(screen.queryByText(/数据库已保存；/)).not.toBeInTheDocument()
})
it('still reports partial success without a query client', async () => {
  const { session } = await setup(false), original = mocks.fetchJson.getMockImplementation()!
  mocks.fetchJson.mockImplementation((url, init) => isGitPut(url, init) ? Promise.reject({ status: 409, body: '{"code":"VERSION_CONFLICT"}' }) : original(url, init))
  fireEvent.click(screen.getByRole('button', { name: '保存', exact: true }))
  expect(await screen.findByText(/数据库已保存；/)).toBeInTheDocument()
  expect(session.state.versions.draft.files.find(f => f.path === 'scripts/run')?.contentBase64).toBe(btoa('Local file'))
})

it.each(['invalid JSON', 'content differs'] as const)('invalidates source caches when DB acknowledges save but receipt %s', async mode => {
  const { id, client, session } = await setup(), original = mocks.fetchJson.getMockImplementation()!
  if (mode === 'invalid JSON') {
    const originalFetch = globalThis.fetch
    vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
      if (url.endsWith('/editor-draft') && init?.method === 'PUT') return new Response('{', { status: 200 })
      return originalFetch(url, init)
    }))
  }
  else mocks.fetchJson.mockImplementation(async (url, init) => {
    if (url.endsWith('/editor-draft') && init?.method === 'PUT') {
      const input = JSON.parse(await text(init.body as Blob)); mocks.db = input.spec
      return { workflowId: id, packId: 'pack', specJson: JSON.stringify({ ...input.spec, title: 'Different on server' }), draftToken: 'b'.repeat(64) }
    }
    return original(url, init)
  })
  fireEvent.click(screen.getByRole('button', { name: '保存', exact: true }))
  expect(await screen.findByText(mode === 'invalid JSON' ? /响应不是有效 JSON/ : /保存内容与请求不同/)).toHaveAttribute('role', 'alert')
  expect(vi.mocked(fetch).mock.calls.filter(([url, init]) => String(url).endsWith('/editor-draft') && init?.method === 'PUT')).toHaveLength(1)
  expect(mocks.fetchJson.mock.calls.filter(([url, init]) => isGitPut(url, init))).toHaveLength(0)
  expect(useEditorStore.getState().isDirty).toBe(true)
  expect(useEditorStore.getState().spec?.title).toBe('Saved DB')
  expect(session.state.versions.draft.files.find(f => f.path === 'scripts/run')?.contentBase64).toBe(btoa('Local file'))
  expect(invalidated(client, ['editor-workflow-document', id])).toBe(true)
  expect(invalidated(client, ['db-workflows'])).toBe(true)
  expect(invalidated(client, ['workflow-types'])).toBe(true)
  expect(invalidated(client, ['editor-workflow-document', 'other'])).toBe(false)
})
it.each(['wrong workflow', 'missing token', 'no response'] as const)('retains the draft for an unverifiable DB outcome: %s', async mode => {
  const { id, client, session } = await setup(), original = mocks.fetchJson.getMockImplementation()!
  mocks.fetchJson.mockImplementation(async (url, init) => {
    if (url.endsWith('/editor-draft') && init?.method === 'PUT') {
      if (mode === 'no response') throw new Error('network')
      const input = JSON.parse(await text(init.body as Blob)); mocks.db = input.spec
      return { workflowId: mode === 'wrong workflow' ? 'other' : id, packId: 'pack', specJson: JSON.stringify(input.spec), draftToken: mode === 'missing token' ? undefined : 'b'.repeat(64) }
    }
    return original(url, init)
  })
  fireEvent.click(screen.getByRole('button', { name: '保存', exact: true }))
  expect(await screen.findByText(mode === 'no response' ? /数据库保存失败或结果未知/ : /回执无法验证/)).toHaveAttribute('role', 'alert')
  expect(mocks.fetchJson.mock.calls.filter(([url, init]) => isGitPut(url, init))).toHaveLength(0)
  expect(useEditorStore.getState().isDirty).toBe(true)
  expect(session.state.versions.draft.files.find(f => f.path === 'scripts/run')?.contentBase64).toBe(btoa('Local file'))
  // Lost responses may follow a committed write, so stale caches must not
  // become the next editor session's trusted source.
  expect(invalidated(client, ['editor-workflow-document', id])).toBe(true)
  expect(invalidated(client, ['db-workflows'])).toBe(true)
})
it('retains both drafts and does not invalidate caches when identity writes are unsupported', async () => {
  const { id, client, session } = await setup()
  act(() => updateVersion(session, 'draft', { files: session.state.versions.draft.files.map(f => f.path === 'workflow.pack.yaml' ? file(f.path, stringify({ id: 'renamed', workflows: [id] })) : f) }))
  fireEvent.click(screen.getByRole('button', { name: '保存', exact: true }))
  expect(await screen.findByText(/暂不支持更改 Pack 身份/)).toHaveAttribute('role', 'alert')
  expect(session.state.uncertainRename).toBeNull()
  expect(mocks.fetchJson).not.toHaveBeenCalled()
  expect(session.state.versions.draft.files.find(f => f.path === 'scripts/run')?.contentBase64).toBe(btoa('Local file'))
  expect(useEditorStore.getState().isDirty).toBe(true)
  expect(invalidated(client, ['editor-workflow-document', id])).toBe(false)
  expect(invalidated(client, ['db-workflows'])).toBe(false)
})
