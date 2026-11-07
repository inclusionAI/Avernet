import { act, cleanup, renderHook, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { stringify } from 'yaml'
import { fetchJson } from '@avernet/clawweb-shared/web/api/client'
import { useEditorStore } from '../store'
import { sessionFor, publish, updateVersion, load } from '../packVersionSession'
import { usePackCanvasSession } from '../usePackCanvasSession'
import { saveWorkflowCanvasDocument } from '../workflowDocumentSave'
import { assertEditorWorkflowReadable, editorWorkflowWriteState } from '../workflowWriteGuard'
import { UNIFIED_SAVE_TIMEOUT_MS } from '../../api/workflowUnifiedSave'
import { legacyPackCandidate } from '../packVersionSave'

const mocks = vi.hoisted(() => ({ read: vi.fn() }))
vi.mock('../../api/workflowDocument', async importOriginal => ({ ...(await importOriginal<typeof import('../../api/workflowDocument')>()), readWorkflowDocument: mocks.read }))

vi.mock('@avernet/clawweb-shared/web/api/client', () => ({ fetchJson: vi.fn(), api: { workflows: { get: mocks.read } } }))
const A = 'a'.repeat(40), B = 'b'.repeat(40), token = 'a'.repeat(64), nextToken = 'b'.repeat(64)
const row = (spec: { id: string }, draftToken = token) => ({ workflowId: spec.id, packId: 'pack', specJson: JSON.stringify(spec), draftToken })
const isGitPut = (url: string, init?: RequestInit) => init?.method === 'PUT' && !url.endsWith('/editor-draft')
let sequence = 0
const blobText = (blob: Blob) => new Promise<string>(resolve => { const r = new FileReader(); r.onload = () => resolve(String(r.result)); r.readAsText(blob) })
function setup() {
  const id = `db-canvas-${++sequence}`, spec = { id, title: 'Initial', version: '1', nodes: [] }
  useEditorStore.getState().reset({ force: true }); useEditorStore.getState().loadSpec(spec); useEditorStore.setState({ _databasePackId: 'pack' })
  const files = [{ path: 'workflow.pack.yaml', contentBase64: btoa(stringify({ id: 'pack', workflows: [id] })), mode: '100644' as const },
    { path: `workflows/${id}.yaml`, contentBase64: btoa(stringify(spec)), mode: '100644' as const },
    { path: 'scripts/test.sh', contentBase64: btoa('old'), mode: '100644' as const }]
  const snapshot = { workflowId: id, packId: 'pack', baseRef: `origin/wf/${id}`, baseCommit: A, draftHead: A, canWrite: true, versionFiles: files }
  const session = sessionFor(id)
  let currentToken = token, savedSnapshot = snapshot
  vi.spyOn(window, 'confirm').mockReturnValue(true)
  publish(session, { ...session.state, version: 'draft', versions: { ...session.state.versions,
    draft: { snapshot, files: structuredClone(files), loading: false, error: null, blocked: false, notice: null } } })
  mocks.read.mockResolvedValue(structuredClone(spec))
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (url.endsWith('/unified-save') && init?.method === 'GET') return new Response(JSON.stringify({ protocol: 'workflow-unified-save-v1' }))
    if (url.endsWith('/unified-save') && init?.method === 'POST') {
      const input = JSON.parse(await blobText(init.body as Blob))
      const parsed = JSON.parse(input.specJson)
      mocks.read.mockResolvedValue(parsed); currentToken = nextToken
      const selected = session.state.versions[input.sourceVersion as 'current' | 'draft']
      const base = selected.snapshot!
      const files = structuredClone(selected.files).map(f => f.path === `workflows/${id}.yaml`
        ? { ...f, contentBase64: btoa(stringify(parsed, { sortMapEntries: true, lineWidth: 0 })) } : f)
      savedSnapshot = { ...base, baseRef: `origin/wf/${id}`, baseCommit: B, draftHead: B, versionFiles: files }
      return new Response(JSON.stringify({ requestId: input.requestId, outcome: 'confirmed', reconciliationRequired: false,
        evidence: { db: 'confirmed', git: 'confirmed', inputHash: 'e'.repeat(64), commit: B, draftToken: nextToken } }))
    }
    return new Response(JSON.stringify(await fetchJson(url, init)))
  }))
  vi.mocked(fetchJson).mockImplementation(async (url, init) => {
    if (url.endsWith('/editor-draft') && init?.method === 'GET') return row(await mocks.read(), currentToken)
    if (url.endsWith('/pack-version?version=draft')) return savedSnapshot
    const input = JSON.parse(await blobText(init!.body as Blob))
    if (url.endsWith('/editor-draft')) { expect(input.expectedDraftToken).toBe(token); mocks.read.mockResolvedValue(structuredClone(input.spec)); return row(input.spec, nextToken) }
    if (url === '/api/auto-heal/save') { mocks.read.mockResolvedValue(structuredClone(input.spec)); return { ok: true, saved: true, workflowId: id, packId: 'pack' } }
    return { branch: `wf/${id}`, commit: B, packId: 'pack', version: 1, requestId: input.requestId }
  })
  const hook = renderHook(() => usePackCanvasSession(id, spec))
  return { id, spec, session, hook }
}
function editScript(session: ReturnType<typeof sessionFor>) {
  const state = session.state.versions.draft
  updateVersion(session, 'draft', { files: state.files.map(f => f.path === 'scripts/test.sh' ? { ...f, contentBase64: btoa('new') } : f) })
}
afterEach(() => { cleanup(); vi.resetAllMocks(); vi.restoreAllMocks(); vi.unstubAllGlobals() })
it('fails closed for an active workflow without a trusted draft binding', async () => {
  const { hook } = setup()
  act(() => { useEditorStore.setState({ _databaseSpec: null, _databasePackId: undefined, _activeDeployNumber: 7 }) })
  await act(async () => { await expect(hook.result.current.save()).rejects.toThrow('缺少可信') })
  expect(fetchJson).not.toHaveBeenCalled()
})
it('saves the canvas to DB and projected Git YAML even without script edits', async () => {
  const { hook, id, session } = setup()
  act(() => useEditorStore.getState().updateSpecField('title', 'DB draft'))
  await act(async () => { expect(await hook.result.current.save()).toEqual({ mode: 'unified', dbSaved: true, gitSaved: true, commit: B }) })
  const call = vi.mocked(fetch).mock.calls.find(([url, init]) => String(url).endsWith('/unified-save') && init?.method === 'POST')!
  const input = JSON.parse(await blobText(call[1]!.body as Blob))
  expect(input.expectedDraftToken).toBe(token); expect(JSON.parse(input.specJson)).toMatchObject({ id, title: 'DB draft' }); expect(input.changes).toEqual([])
  expect(window.confirm).not.toHaveBeenCalled()
  expect(session.state.versions.draft.snapshot?.baseCommit).toBe(B)
  expect(atob(session.state.versions.draft.snapshot!.versionFiles.find(f => f.path === `workflows/${id}.yaml`)!.contentBase64)).toContain('DB draft')
  expect(useEditorStore.getState().isDirty).toBe(false)
})
it('preserves a NULL database Pack binding during unified save', async () => {
  const { hook, id, session } = setup()
  const original = vi.mocked(fetchJson).getMockImplementation()!
  act(() => {
    useEditorStore.setState({ _databasePackId: null })
    const version = session.state.versions.draft
    const files = version.files.map(file => file.path === 'workflow.pack.yaml' ? { ...file, contentBase64: btoa(stringify({ id, workflows: [id] })) } : file)
    updateVersion(session, 'draft', { snapshot: { ...version.snapshot!, packId: id, versionFiles: files }, files: structuredClone(files) })
    useEditorStore.getState().updateSpecField('title', 'Local'); editScript(session)
  })
  vi.mocked(fetchJson).mockImplementation(async (url, init) => url.endsWith('/editor-draft') && init?.method === 'GET'
    ? { ...row(await mocks.read(), vi.mocked(fetch).mock.calls.some(([u, i]) => String(u).endsWith('/unified-save') && i?.method === 'POST') ? nextToken : token), packId: null } : original(url, init))
  await act(async () => { expect(await hook.result.current.save()).toEqual({ mode: 'unified', dbSaved: true, gitSaved: true, commit: B }) })
  const call = vi.mocked(fetch).mock.calls.find(([url, init]) => String(url).endsWith('/unified-save') && init?.method === 'POST')!
  expect(JSON.parse(await blobText(call[1]!.body as Blob))).not.toHaveProperty('packId')
  expect(useEditorStore.getState()._databasePackId).toBeNull()
})
it('submits script changes and the DB document in one unified request', async () => {
  const { hook, session } = setup(); act(() => editScript(session))
  await act(async () => { expect(await hook.result.current.save()).toEqual({ mode: 'unified', dbSaved: true, gitSaved: true, commit: B }) })
  const call = vi.mocked(fetch).mock.calls.find(([url, init]) => String(url).endsWith('/unified-save') && init?.method === 'POST')!
  const input = JSON.parse(await blobText(call[1]!.body as Blob))
  expect(input.changes).toHaveLength(1); expect(input.changes[0].path).toBe('scripts/test.sh'); expect(JSON.parse(input.specJson).title).toBe('Initial')
})
it('saves both stores as one candidate without putting YAML in ordinary changes', async () => {
  const { hook, session } = setup()
  act(() => { useEditorStore.getState().updateSpecField('title', 'Changed'); editScript(session) })
  await act(async () => { expect(await hook.result.current.save()).toEqual({ mode: 'unified', dbSaved: true, gitSaved: true, commit: B }) })
  const call = vi.mocked(fetch).mock.calls.find(([url, init]) => String(url).endsWith('/unified-save') && init?.method === 'POST')!
  const input = JSON.parse(await blobText(call[1]!.body as Blob))
  expect(input.changes.map((c: { path: string }) => c.path)).toEqual(['scripts/test.sh'])
  expect(JSON.parse(input.specJson).title).toBe('Changed')
})
it('does not adopt a saved snapshot if another version gets an orphaned file during the unified write', async () => {
  const { hook, session } = setup()
  act(() => { useEditorStore.getState().updateSpecField('title', 'Changed'); editScript(session) })
  const original = vi.mocked(fetch).getMockImplementation()!
  let release!: () => void, started!: () => void
  const gate = new Promise<void>(resolve => { release = resolve }), began = new Promise<void>(resolve => { started = resolve })
  vi.mocked(fetch).mockImplementation(async (url, init) => { if (init?.method === 'POST') { started(); await gate } return original(url, init) })
  let pending!: ReturnType<typeof hook.result.current.save>
  act(() => { pending = hook.result.current.save() }); await began
  act(() => updateVersion(session, 'current', { snapshot: null, files: [{ path: 'orphan', contentBase64: 'eA==', mode: '100644' }] }))
  await act(async () => { release(); await expect(pending).rejects.toThrow('统一保存未能完整确认') })
  expect(session.state.versions.current.files[0].path).toBe('orphan')
  expect(session.state.versions.draft.files.find(f => f.path === 'scripts/test.sh')?.contentBase64).toBe(btoa('new'))
  expect(useEditorStore.getState().isDirty).toBe(true); expect(session.state.postSaveVerificationError).toBeTruthy()
})
it('retains both drafts and locks when the unified response is lost', async () => {
  const { hook, session } = setup()
  act(() => { useEditorStore.getState().updateSpecField('title', 'Changed'); editScript(session) })
  const original = vi.mocked(fetch).getMockImplementation()!
  vi.mocked(fetch).mockImplementation((url, init) => init?.method === 'POST' ? Promise.reject(new Error('lost')) : original(url, init))
  await act(async () => { await expect(hook.result.current.save()).rejects.toThrow('统一保存未能完整确认') })
  expect(useEditorStore.getState().isDirty).toBe(true)
  expect(session.state.postSaveVerificationError).toBeTruthy()
  expect(session.state.versions.draft.files.find(f => f.path === 'scripts/test.sh')?.contentBase64).toBe(btoa('new'))
  expect(vi.mocked(fetchJson).mock.calls.every(([url]) => !url.includes('pack-operations'))).toBe(true)
})
it('keeps both edits and avoids Git when DB save fails', async () => {
  const { hook, session } = setup()
  act(() => { useEditorStore.getState().updateSpecField('title', 'Changed'); editScript(session) })
  vi.mocked(fetchJson).mockRejectedValueOnce(new Error('offline'))
  await act(async () => { await expect(hook.result.current.save()).rejects.toThrow() })
  expect(useEditorStore.getState().isDirty).toBe(true)
  expect(session.state.versions.draft.files.find(f => f.path === 'scripts/test.sh')?.contentBase64).toBe(btoa('new'))
  expect(fetchJson).toHaveBeenCalledTimes(1)
})
it('saves an independent workflow document even when no Git snapshot is available', async () => {
  const { id, session } = setup()
  act(() => {
    updateVersion(session, 'draft', { snapshot: null, files: [], loading: false, error: 'Git unavailable' })
    useEditorStore.getState().updateSpecField('title', 'DB only')
  })
  await act(async () => { expect(await saveWorkflowCanvasDocument(id)).toEqual({ dbSaved: true, commit: null }) })
  expect(vi.mocked(fetchJson).mock.calls.filter(([url, init]) => isGitPut(url, init))).toHaveLength(0)
  expect(useEditorStore.getState().isDirty).toBe(false)
})
it('saves a file-only YAML edit through the shared unified save', async () => {
  const { hook, session, spec, id } = setup(), edited = { ...spec, title: 'From YAML' }
  act(() => editWorkflowFile(session, id, edited))
  await act(async () => { await expect(hook.result.current.save()).resolves.toEqual({ mode: 'unified', dbSaved: true, gitSaved: true, commit: B }) })
  const post = vi.mocked(fetch).mock.calls.find(([, init]) => init?.method === 'POST')!
  const input = JSON.parse(await blobText(post[1]!.body as Blob))
  expect(JSON.parse(input.specJson)).toEqual(edited)
  expect(input.changes).toEqual([])
  expect(useEditorStore.getState().spec).toEqual(edited)
  expect(useEditorStore.getState()._databaseSpec).toEqual(edited)
  expect(atob(session.state.versions.draft.snapshot!.versionFiles.find(f => f.path === `workflows/${id}.yaml`)!.contentBase64)).toBe(stringify(edited, { sortMapEntries: true, lineWidth: 0 }))
})
it('retains canvas changes made while the unified save is in flight', async () => {
  const { hook, id, session } = setup(); const original = vi.mocked(fetch).getMockImplementation()!
  let release!: () => void, started!: () => void
  const gate = new Promise<void>(r => { release = r }), began = new Promise<void>(r => { started = r })
  vi.mocked(fetch).mockImplementation(async (url, init) => { if (init?.method === 'POST') { started(); await gate } return original(url, init) })
  act(() => useEditorStore.getState().updateSpecField('title', 'First'))
  let pending!: ReturnType<typeof hook.result.current.save>; act(() => { pending = hook.result.current.save() }); await began
  act(() => useEditorStore.getState().updateSpecField('title', 'Later'))
  await act(async () => { release(); await expect(pending).resolves.toEqual({ mode: 'unified', dbSaved: true, gitSaved: true, commit: B }) })
  expect(useEditorStore.getState().spec?.title).toBe('Later'); expect(useEditorStore.getState().isDirty).toBe(true)
  expect(useEditorStore.getState()._databaseSpec?.title).toBe('First')
  expect(session.state.versions.draft.snapshot?.baseCommit).toBe(B)
  expect(session.state.postSaveVerificationError).toBeFalsy(); expect(editorWorkflowWriteState(id).phase).toBe('clear')
})
it('does not accept an unverifiable DB save response as success', async () => {
  const { hook } = setup()
  act(() => useEditorStore.getState().updateSpecField('title', 'Changed'))
  vi.mocked(fetchJson).mockResolvedValueOnce(row(useEditorStore.getState()._databaseSpec!)).mockResolvedValueOnce({ ok: true, commit: B })
  await act(async () => { await expect(hook.result.current.save()).rejects.toThrow('统一保存未能完整确认') })
  expect(useEditorStore.getState().isDirty).toBe(true)
})

function editWorkflowFile(session: ReturnType<typeof sessionFor>, id: string, spec: unknown) {
  const version = session.state.versions.draft
  updateVersion(session, 'draft', { files: version.files.map(file => file.path === `workflows/${id}.yaml`
    ? { ...file, contentBase64: btoa(stringify(spec)) } : file) })
}

it('saves an explicitly edited file based on the latest verified saved tree', async () => {
  const { hook, session, spec, id } = setup()
  act(() => useEditorStore.getState().updateSpecField('title', 'New DB title'))
  await act(async () => { await hook.result.current.save() })
  const edited = { ...spec, config: { fromGit: true } }
  act(() => editWorkflowFile(session, id, edited))
  await act(async () => { await expect(hook.result.current.save()).resolves.toMatchObject({ mode: 'unified', dbSaved: true, gitSaved: true }) })
  const posts = vi.mocked(fetch).mock.calls.filter(([, init]) => init?.method === 'POST')
  const input = JSON.parse(await blobText(posts[1][1]!.body as Blob))
  expect(input.sourceCommit).toBe(B)
  expect(JSON.parse(input.specJson)).toEqual(edited)
  expect(useEditorStore.getState()._databaseSpec).toEqual(edited)
  expect(useEditorStore.getState().spec).toEqual(edited)
})
it('requires reconciliation when both canvas and file have divergent edits', async () => {
  const { hook, session, spec, id } = setup(), edited = { ...spec, title: 'Chosen Git title', config: { enabled: true } }
  act(() => { editWorkflowFile(session, id, edited); useEditorStore.getState().updateSpecField('title', 'Canvas title') })
  const files = structuredClone(session.state.versions.draft.files)
  await act(async () => { await expect(hook.result.current.save()).rejects.toThrow('画布和文件同时修改') })
  expect(useEditorStore.getState().spec?.title).toBe('Canvas title')
  expect(session.state.versions.draft.files).toEqual(files)
  expect(vi.mocked(fetch).mock.calls.every(([, init]) => init?.method === 'GET')).toBe(true)
  act(() => useEditorStore.getState().importYaml(edited))
  await act(async () => { expect(await hook.result.current.save()).toEqual({ mode: 'unified', dbSaved: true, gitSaved: true, commit: B }) })
  expect(useEditorStore.getState().spec).toEqual(edited)
})
it('saves a manually reconciled YAML candidate only after applying it to the canvas', async () => {
  const { hook, session, spec, id } = setup(), merged = { ...spec, title: 'Preserved DB title', config: { enabled: true } }
  act(() => { editWorkflowFile(session, id, merged); useEditorStore.getState().importYaml(merged) })
  await act(async () => { await hook.result.current.save() })
  expect(useEditorStore.getState().spec).toEqual(merged); expect(useEditorStore.getState().isDirty).toBe(false)
})
it('rechecks file candidates when files change during the database baseline read', async () => {
  const { hook, session } = setup()
  const original = vi.mocked(fetchJson).getMockImplementation()!
  act(() => useEditorStore.getState().updateSpecField('title', 'Canvas edit'))
  vi.mocked(fetchJson).mockImplementation(async (url, init) => {
    if (url.endsWith('/editor-draft') && init?.method === 'GET') editScript(session)
    return original(url, init)
  })
  await act(async () => { await expect(hook.result.current.save()).rejects.toThrow('核对数据库期间编辑内容或会话已变化') })
  expect(session.state.versions.draft.files.find(file => file.path === 'scripts/test.sh')?.contentBase64).toBe(btoa('new'))
  expect(vi.mocked(fetch).mock.calls.every(([, init]) => init?.method === 'GET')).toBe(true)
})

it('blocks a DB save if the DB changed since the editor baseline was read', async () => {
  const { hook, id, session } = setup()
  act(() => { useEditorStore.getState().updateSpecField('title', 'Local edit'); editScript(session) })
  mocks.read.mockResolvedValue({ id, title: 'External edit', version: '1', nodes: [] })
  await act(async () => { await expect(hook.result.current.save()).rejects.toThrow('数据库草稿自读取后已变化') })
  expect(vi.mocked(fetchJson).mock.calls.map(([, init]) => init?.method)).toEqual(['GET'])
  expect(useEditorStore.getState().spec?.title).toBe('Local edit')
  expect(useEditorStore.getState().isDirty).toBe(true)
  expect(session.state.versions.draft.files.find(file => file.path === 'scripts/test.sh')?.contentBase64).toBe(btoa('new'))
})

it('does not mark the request clean or save files when DB normalization changes it', async () => {
  const { hook, id, session } = setup()
  act(() => { useEditorStore.getState().updateSpecField('title', '  Local title  '); editScript(session) })
  const request = useEditorStore.getState().spec!
  vi.mocked(fetchJson).mockResolvedValueOnce(row(useEditorStore.getState()._savedSpec!)).mockResolvedValueOnce(row({ ...request, title: 'Local title' }, nextToken))
  await act(async () => { await expect(hook.result.current.save()).rejects.toThrow('postflight mismatch') })
  expect(useEditorStore.getState().isDirty).toBe(true)
  expect(useEditorStore.getState().spec?.title).toBe('  Local title  ')
  expect(useEditorStore.getState()._savedSpec?.title).toBe('Initial')
  expect(vi.mocked(fetchJson).mock.calls.filter(([url, init]) => isGitPut(url, init))).toHaveLength(0)
  expect(session.state.versions.draft.files.find(file => file.path === 'scripts/test.sh')?.contentBase64).toBe(btoa('new'))
})

it('keeps the YAML draft when the unified receipt cannot be parsed after dispatch', async () => {
  const { hook, session, id, spec } = setup(), edited = { ...spec, title: 'File edit' }
  act(() => { editWorkflowFile(session, id, edited); useEditorStore.getState().importYaml(edited) })
  const original = vi.mocked(fetch).getMockImplementation()!
  vi.mocked(fetch).mockImplementation((url, init) => init?.method === 'POST' ? Promise.resolve(new Response('{', { status: 200 })) : original(url, init))
  await act(async () => { await expect(hook.result.current.save()).rejects.toThrow('统一保存未能完整确认') })
  expect(useEditorStore.getState().isDirty).toBe(true)
  expect(session.state.versions.draft.files.find(file => file.path === `workflows/${id}.yaml`)?.contentBase64).toBe(btoa(stringify(edited)))
  expect(session.state.postSaveVerificationError).toBeTruthy()
})
it('does not start a DB write if the canvas changes while the baseline is being read', async () => {
  const { hook, spec } = setup()
  let resolve!: (value: unknown) => void
  vi.mocked(fetchJson).mockReturnValueOnce(new Promise(r => { resolve = r }))
  act(() => useEditorStore.getState().updateSpecField('title', 'First'))
  let pending!: ReturnType<typeof hook.result.current.save>
  act(() => { pending = hook.result.current.save() })
  act(() => useEditorStore.getState().updateSpecField('title', 'Later'))
  await act(async () => { resolve(row(spec)); await expect(pending).rejects.toThrow('读取能力期间编辑会话已变化') })
  expect(vi.mocked(fetch).mock.calls.every(([, init]) => init?.method === 'GET')).toBe(true)
  expect(useEditorStore.getState().spec?.title).toBe('Later')
  expect(useEditorStore.getState().isDirty).toBe(true)
})
it('retains an applied YAML candidate and a draft begun during unified save', async () => {
  const { hook, id, spec, session } = setup(), edited = { ...spec, title: 'File edit' }
  act(() => { editWorkflowFile(session, id, edited); useEditorStore.getState().importYaml(edited) })
  const original = vi.mocked(fetch).getMockImplementation()!
  let release!: () => void, started!: () => void
  const gate = new Promise<void>(r => { release = r }), began = new Promise<void>(r => { started = r })
  vi.mocked(fetch).mockImplementation(async (url, init) => { if (init?.method === 'POST') { started(); await gate } return original(url, init) })
  let pending!: ReturnType<typeof hook.result.current.save>; act(() => { pending = hook.result.current.save() }); await began
  act(() => useEditorStore.getState().setPendingEdit('workflow:title', 'in progress'))
  await act(async () => { release(); await expect(pending).resolves.toEqual({ mode: 'unified', dbSaved: true, gitSaved: true, commit: B }) })
  expect(useEditorStore.getState().pendingEdits['workflow:title']).toBe('in progress')
  expect(useEditorStore.getState()._databaseSpec?.title).toBe('File edit')
  expect(session.state.versions.draft.snapshot?.baseCommit).toBe(B)
  expect(session.state.versions.draft.files.find(file => file.path === `workflows/${id}.yaml`)?.contentBase64)
    .toBe(btoa(stringify(edited, { sortMapEntries: true, lineWidth: 0 })))
  expect(session.state.postSaveVerificationError).toBeFalsy(); expect(editorWorkflowWriteState(id).phase).toBe('clear')
  await expect(hook.result.current.save()).rejects.toThrow('未应用的配置草稿')
})
it('requires explicit selection when Git would erase DB-only node coordinates', async () => {
  const { hook, session, spec, id } = setup()
  const git = { ...spec, nodes: [{ id: 'n', title: 'Node', phase: 'work', executor: { type: 'done' }, _x: 10, _y: 20 }] }
  const db = { ...git, nodes: [{ ...git.nodes[0], _x: 90 }] }
  act(() => {
    useEditorStore.getState().loadSpec(db); useEditorStore.setState({ _databasePackId: 'pack' })
    const v = session.state.versions.draft
    const baseFiles = v.files.map(file => file.path === `workflows/${id}.yaml` ? { ...file, contentBase64: btoa(stringify(git)) } : file)
    updateVersion(session, 'draft', { snapshot: { ...v.snapshot!, versionFiles: baseFiles }, files: structuredClone(baseFiles) })
    editWorkflowFile(session, id, { ...git, title: 'File title' })
  })
  await act(async () => { await expect(hook.result.current.save()).rejects.toThrow('画布和文件同时修改') })
  expect(window.confirm).not.toHaveBeenCalled()
  expect(fetchJson).not.toHaveBeenCalled()
  expect(useEditorStore.getState().spec?.nodes[0]._x).toBe(90)
})


it.each([404, 403, 503])('does not save DB from the Pack page after a draft read fails with %i', async status => {
  const { id, session, hook } = setup()
  const previous = vi.mocked(fetchJson).getMockImplementation()!
  vi.mocked(fetchJson).mockImplementation((url, init) => url.includes('/pack-version?version=draft')
    ? Promise.reject(Object.assign(new Error('read failed'), { status })) : previous(url, init))
  act(() => {
    useEditorStore.getState().updateSpecField('title', 'unsaved canvas')
    updateVersion(session, 'draft', { snapshot: null, files: [], error: null })
    load(session, id, 'draft')
  })
  await waitFor(() => expect(session.state.versions.draft.error).not.toBeNull())
  await act(async () => { await expect(hook.result.current.save()).rejects.toThrow(/版本.*读取/) })
  expect(vi.mocked(fetchJson).mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(0)
  expect(useEditorStore.getState().spec?.title).toBe('unsaved canvas')
  expect(useEditorStore.getState().isDirty).toBe(true)
  expect(session.state.version).toBe('draft')
})

it.each([false, true])('does not save DB while the selected snapshot is loading (cached=%s)', async cached => {
  const { id, session, hook } = setup()
  let resolve!: (value: unknown) => void
  const pending = new Promise<unknown>(done => { resolve = done })
  const existing = session.state.versions.draft.snapshot!
  vi.mocked(fetchJson).mockImplementation((url) => url.includes('/pack-version?version=draft') ? pending : Promise.reject(new Error('unexpected write')))
  act(() => {
    useEditorStore.getState().updateSpecField('title', 'unsaved while loading')
    if (!cached) updateVersion(session, 'draft', { snapshot: null, files: [] })
    load(session, id, 'draft')
  })
  expect(session.state.versions.draft.loading).toBe(true)
  await act(async () => { await expect(hook.result.current.save()).rejects.toThrow(/版本.*读取/) })
  expect(vi.mocked(fetchJson).mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(0)
  expect(useEditorStore.getState().spec?.title).toBe('unsaved while loading')
  expect(useEditorStore.getState().isDirty).toBe(true)
  await act(async () => { resolve(existing); await pending })
})

it.each([404, 403, 503])('blocks the Pack save after a cached draft refresh fails with %i', async status => {
  const { id, session, hook } = setup()
  const cached = session.state.versions.draft.snapshot
  const cachedFiles = structuredClone(session.state.versions.draft.files)
  let rejectRead!: (reason: unknown) => void
  const pending = new Promise<unknown>((_, reject) => { rejectRead = reject })
  const original = vi.mocked(fetchJson).getMockImplementation()!
  vi.mocked(fetchJson).mockImplementation((url, init) => url.includes('/pack-version?version=draft') ? pending : original(url, init))
  act(() => load(session, id, 'draft'))
  act(() => useEditorStore.getState().updateSpecField('title', 'unsaved during refresh'))
  await act(async () => { rejectRead(Object.assign(new Error('unavailable'), { status })); await pending.catch(() => {}) })
  expect(session.state.versions.draft.snapshot).toBe(cached)
  expect(session.state.versions.draft.files).toEqual(cachedFiles)
  expect(session.state.versions.draft.error).not.toBeNull()
  await act(async () => { await expect(hook.result.current.save()).rejects.toThrow(/版本.*读取/) })
  expect(vi.mocked(fetchJson).mock.calls.filter(([, init]) => init?.method === 'POST' || init?.method === 'PUT')).toHaveLength(0)
  expect(useEditorStore.getState().spec?.title).toBe('unsaved during refresh')
  expect(useEditorStore.getState().isDirty).toBe(true)
})

it('allows Pack saving again after a successful refresh replaces a failed cached refresh', async () => {
  const { id, session, hook } = setup()
  const cached = session.state.versions.draft.snapshot!
  const original = vi.mocked(fetchJson).getMockImplementation()!
  vi.mocked(fetchJson).mockImplementationOnce(() => Promise.reject(Object.assign(new Error('unavailable'), { status: 503 })))
  act(() => load(session, id, 'draft'))
  await waitFor(() => expect(session.state.versions.draft.error).not.toBeNull())
  vi.mocked(fetchJson).mockImplementationOnce(() => Promise.resolve(cached))
  act(() => load(session, id, 'draft'))
  await waitFor(() => expect(session.state.versions.draft.loading).toBe(false))
  expect(session.state.versions.draft.error).toBeNull()
  vi.mocked(fetchJson).mockImplementation(original)
  act(() => useEditorStore.getState().updateSpecField('title', 'saved after recovery'))
  await act(async () => { expect(await hook.result.current.save()).toEqual({ mode: 'unified', dbSaved: true, gitSaved: true, commit: B }) })
  expect(useEditorStore.getState().isDirty).toBe(false)
})

it('does not retry a correlated rejection from an unjournaled unified save', async () => {
  const { session, hook } = setup(); act(() => editScript(session))
  const original = vi.mocked(fetch).getMockImplementation()!
  vi.mocked(fetch).mockImplementation(async (url, init) => {
    if (init?.method !== 'POST') return original(url, init)
    const input = JSON.parse(await blobText(init.body as Blob))
    return new Response(JSON.stringify({ requestId: input.requestId, outcome: 'rejected', evidence: { db: 'not_started', git: 'not_started' }, reconciliationRequired: false }), { status: 409 })
  })
  await act(async () => { await expect(hook.result.current.save()).rejects.toThrow('统一保存未能完整确认') })
  expect(session.state.postSaveVerificationError).toBeTruthy()
  await expect(hook.result.current.save()).rejects.toThrow('人工核对')
  expect(vi.mocked(fetch).mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1)
})

it.each(['empty', 'missing manifest', 'missing workflow YAML', 'empty manifest'] as const)('refuses an invalid Git snapshot (%s) before saving a dirty or clean DB', async kind => {
  for (const isDirty of [false, true]) {
    const { hook, session } = setup()
    const state = session.state.versions.draft
    const files = kind === 'empty' ? [] : state.files
      .filter(f => kind === 'missing manifest' ? f.path !== 'workflow.pack.yaml' : kind === 'missing workflow YAML' ? !f.path.startsWith('workflows/') : true)
      .map(f => kind === 'empty manifest' && f.path === 'workflow.pack.yaml' ? { ...f, contentBase64: '' } : f)
    act(() => {
      updateVersion(session, 'draft', { snapshot: { ...state.snapshot!, versionFiles: files }, files: structuredClone(files) })
      if (isDirty) useEditorStore.getState().updateSpecField('title', 'Local change')
    })
    await act(async () => { await expect(hook.result.current.save()).rejects.toThrow() })
    expect(fetchJson).not.toHaveBeenCalled()
    cleanup(); vi.resetAllMocks()
  }
})
it('checks the remote DB baseline even when the local DB document is clean', async () => {
  const { hook, spec } = setup()
  mocks.read.mockResolvedValue({ ...spec, title: 'Changed by another client' })
  await act(async () => { await expect(hook.result.current.save()).rejects.toThrow('数据库草稿自读取后已变化') })
  expect(vi.mocked(fetchJson).mock.calls.map(([url, init]) => [url, init?.method])).toEqual([[`/api/workflows/${spec.id}/editor-draft`, 'GET']])
})

it('reports an unverified remote baseline when a clean DB cannot be read', async () => {
  const { hook } = setup()
  mocks.read.mockRejectedValue(new Error('offline'))
  await act(async () => { await expect(hook.result.current.save()).rejects.toThrow('无法核对数据库草稿') })
  expect(fetchJson).toHaveBeenCalledTimes(1)
})

function setGitBaseline(session: ReturnType<typeof sessionFor>, id: string, text: string) {
  const current = session.state.versions.draft
  const files = current.files.map(file => file.path === `workflows/${id}.yaml` ? { ...file, contentBase64: btoa(text) } : file)
  updateVersion(session, 'draft', { snapshot: { ...current.snapshot!, versionFiles: structuredClone(files) }, files })
}

it('publishes a clean DB document when the selected Git baseline differs', async () => {
  const { hook, session, id, spec } = setup()
  expect(hook.result.current.baselineComparison).toBe('same')
  act(() => setGitBaseline(session, id, stringify({ ...spec, title: 'Old Git' })))
  expect(hook.result.current.baselineComparison).toBe('different')
  await act(async () => { await expect(hook.result.current.save()).resolves.toMatchObject({ mode: 'unified' }) })
  const post = vi.mocked(fetch).mock.calls.find(([, init]) => init?.method === 'POST')!
  const input = JSON.parse(await blobText(post[1]!.body as Blob))
  expect(JSON.parse(input.specJson)).toEqual(spec)
  expect(input).toMatchObject({ expectedDraftToken: token, sourceCommit: A, expectedSavedHead: A, changes: [] })
  expect(hook.result.current.baselineComparison).toBe('same')
})

it('compares the selected snapshot, not unsubmitted files or canvas edits', () => {
  const { hook, session, id, spec } = setup()
  act(() => { editWorkflowFile(session, id, { ...spec, title: 'File edit' }); useEditorStore.getState().updateSpecField('title', 'Canvas edit') })
  expect(hook.result.current.baselineComparison).toBe('same')
  act(() => { const v = session.state.versions.draft; publish(session, { ...session.state, version: 'current', versions: { ...session.state.versions,
    current: { ...v, snapshot: { ...v.snapshot!, versionFiles: v.snapshot!.versionFiles.map(f => f.path === `workflows/${id}.yaml` ? { ...f, contentBase64: btoa(stringify({ ...spec, title: 'Other' })) } : f) } } } }) })
  expect(hook.result.current.baselineComparison).toBe('different')
})

it('opens a comparison with raw syntactically invalid Git YAML', async () => {
  const { hook, session, id } = setup(), text = `id: ${id}\nnodes: [\n`
  act(() => setGitBaseline(session, id, text))
  expect(hook.result.current.baselineComparison).toBe('uncomparable')
  let review!: Awaited<ReturnType<typeof hook.result.current.prepareReconciliation>>
  await act(async () => { review = await hook.result.current.prepareReconciliation() })
  expect(review.gitYaml).toBe(text)
  expect(hook.result.current.currentReconciliation(review).gitYaml).toBe(text)
  act(() => hook.result.current.cancelReconciliation(review))
})

it.each(['malformed', 'empty', 'invalid utf8'] as const)('does not let %s comparison block default save', async kind => {
  const { hook, session, id } = setup()
  act(() => {
    setGitBaseline(session, id, kind === 'malformed' ? `id: ${id}\nnodes: [` : '')
    if (kind === 'invalid utf8') {
      const v = session.state.versions.draft
      const files = v.files.map(f => f.path === `workflows/${id}.yaml` ? { ...f, contentBase64: '/w==' } : f)
      updateVersion(session, 'draft', { snapshot: { ...v.snapshot!, versionFiles: files }, files: structuredClone(files) })
    }
  })
  expect(hook.result.current.baselineComparison).toBe(kind === 'invalid utf8' ? 'unavailable' : 'uncomparable')
  if (kind === 'invalid utf8') await act(async () => { await expect(hook.result.current.prepareReconciliation()).rejects.toThrow() })
  const original = vi.mocked(fetch).getMockImplementation()!
  // Model a server pre-write rejection; the client must still quarantine an
  // untrusted response instead of assuming that no write occurred.
  vi.mocked(fetch).mockImplementation(async (url, init) => {
    if (init?.method !== 'POST') return original(url, init)
    const input = JSON.parse(await blobText(init.body as Blob))
    return new Response(JSON.stringify({ requestId: input.requestId, outcome: 'rejected', reconciliationRequired: false,
      evidence: { db: 'not_started', git: 'not_started' } }), { status: 409 })
  })
  await act(async () => { await expect(hook.result.current.save()).rejects.toThrow('统一保存未能完整确认') })
  expect(vi.mocked(fetch).mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1)
  expect(editorWorkflowWriteState(id).phase).toBe('unknown')
  expect(session.state.versions.draft.files.find(f => f.path === `workflows/${id}.yaml`)!.contentBase64)
    .toBe(kind === 'invalid utf8' ? '/w==' : btoa(kind === 'malformed' ? `id: ${id}\nnodes: [` : ''))
})

it('still verifies the DB baseline when Git differs', async () => {
  const { hook, session, id, spec } = setup()
  act(() => { setGitBaseline(session, id, stringify({ ...spec, title: 'Old Git' })); useEditorStore.getState().updateSpecField('title', 'Local') })
  mocks.read.mockResolvedValue({ ...spec, title: 'Remote DB change' })
  await act(async () => { await expect(hook.result.current.save()).rejects.toThrow('数据库草稿自读取后已变化') })
  expect(vi.mocked(fetch).mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false)
  expect(useEditorStore.getState().spec?.title).toBe('Local')
})

it('retains later edits while saving a mismatched Git baseline', async () => {
  const { hook, session, id, spec } = setup()
  act(() => { setGitBaseline(session, id, stringify({ ...spec, title: 'Old Git' })); useEditorStore.getState().updateSpecField('title', 'Submitted') })
  const original = vi.mocked(fetch).getMockImplementation()!
  let release!: () => void, started!: () => void
  const gate = new Promise<void>(r => { release = r }), began = new Promise<void>(r => { started = r })
  vi.mocked(fetch).mockImplementation(async (url, init) => {
    const response = await original(url, init)
    if (init?.method === 'POST') { started(); await gate }
    return response
  })
  let pending!: ReturnType<typeof hook.result.current.save>
  act(() => { pending = hook.result.current.save() }); await began
  act(() => { useEditorStore.getState().updateSpecField('title', 'Later'); editScript(session) })
  await act(async () => { release(); await expect(pending).resolves.toMatchObject({ mode: 'unified' }) })
  expect(useEditorStore.getState().spec?.title).toBe('Later')
  expect(useEditorStore.getState()._databaseSpec?.title).toBe('Submitted')
  expect(useEditorStore.getState().isDirty).toBe(true)
  expect(atob(session.state.versions.draft.files.find(f => f.path === 'scripts/test.sh')!.contentBase64)).toBe('new')
})

it.each(['id-only', 'missing-title', 'missing-nodes'] as const)('saves the edited DB candidate over an incomplete Git baseline (%s)', async kind => {
  const { hook, session, id, spec } = setup()
  const legacy = kind === 'id-only' ? { id } : kind === 'missing-title' ? { id, nodes: [] } : { id, title: 'Old' }
  act(() => { setGitBaseline(session, id, stringify(legacy)); useEditorStore.getState().updateSpecField('title', 'DB edit') })
  expect(hook.result.current.baselineComparison).toBe('uncomparable')
  await act(async () => { await expect(hook.result.current.save()).resolves.toMatchObject({ mode: 'unified' }) })
  const post = vi.mocked(fetch).mock.calls.find(([, init]) => init?.method === 'POST')!
  const input = JSON.parse(await blobText(post[1]!.body as Blob))
  expect(JSON.parse(input.specJson)).toEqual({ ...spec, title: 'DB edit' })
  expect(input).toMatchObject({ expectedDraftToken: token, sourceCommit: A, expectedSavedHead: A, changes: [] })
  expect(hook.result.current.baselineComparison).toBe('same')
})

it.each([false, true])('preserves divergent YAML edits with an incomparable baseline (confirm=%s)', async confirmed => {
  const { hook, session, id, spec } = setup()
  act(() => { setGitBaseline(session, id, stringify({ id })); editWorkflowFile(session, id, { ...spec, title: 'YAML candidate' }) })
  const originalFiles = structuredClone(session.state.versions.draft.files)
  const confirm = vi.spyOn(window, 'confirm').mockReturnValue(confirmed)
  await act(async () => { await expect(hook.result.current.save()).rejects.toThrow('画布和文件同时修改') })
  expect(confirm).not.toHaveBeenCalled(); expect(fetchJson).not.toHaveBeenCalled()
  expect(session.state.versions.draft.files).toEqual(originalFiles)
  expect(useEditorStore.getState().spec).toEqual(spec)
})

it('saves an incomparable YAML edit already matching the canvas', async () => {
  const { hook, session, id, spec } = setup()
  act(() => { setGitBaseline(session, id, stringify({ id })); editWorkflowFile(session, id, { ...spec, title: 'Same' }); useEditorStore.getState().updateSpecField('title', 'Same') })
  await act(async () => { await expect(hook.result.current.save()).resolves.toMatchObject({ mode: 'unified' }) })
  expect(useEditorStore.getState().spec?.title).toBe('Same')
})

it('retains conflict protection for a legacy baseline when canvas and candidate diverge', async () => {
  const { hook, session, id, spec } = setup()
  act(() => { setGitBaseline(session, id, stringify({ id })); editWorkflowFile(session, id, { ...spec, title: 'YAML' }); useEditorStore.getState().updateSpecField('title', 'Canvas') })
  await act(async () => { await expect(hook.result.current.save()).rejects.toThrow('画布和文件同时修改') })
  expect(fetchJson).not.toHaveBeenCalled()
  expect(useEditorStore.getState().spec?.title).toBe('Canvas')
  expect(atob(session.state.versions.draft.files.find(f => f.path === `workflows/${id}.yaml`)!.contentBase64)).toContain('YAML')
})

it.each(['wrong id', 'invalid candidate', 'missing manifest', 'read failure'] as const)('fails closed with a legacy baseline on %s', async kind => {
  const { hook, session, id, spec } = setup()
  act(() => {
    setGitBaseline(session, id, stringify({ id: kind === 'wrong id' ? 'other' : id }))
    if (kind === 'invalid candidate') editWorkflowFile(session, id, { id, title: 'missing nodes' })
    if (kind === 'missing manifest') updateVersion(session, 'draft', { files: session.state.versions.draft.files.filter(f => f.path !== 'workflow.pack.yaml') })
    if (kind === 'read failure') updateVersion(session, 'draft', { readFailure: 'partial read' })
    useEditorStore.getState().updateSpecField('title', 'DB edit')
  })
  await act(async () => { await expect(hook.result.current.save()).rejects.toThrow() })
  expect(fetchJson).not.toHaveBeenCalled()
  expect(useEditorStore.getState().spec).toEqual({ ...spec, title: 'DB edit' })
})

it('saves script and canvas changes over an incomplete legacy Git baseline', async () => {
  const { hook, session, id } = setup()
  act(() => { setGitBaseline(session, id, stringify({ id })); editScript(session); useEditorStore.getState().updateSpecField('title', 'DB edit') })
  await act(async () => { await expect(hook.result.current.save()).resolves.toMatchObject({ mode: 'unified' }) })
  const post = vi.mocked(fetch).mock.calls.find(([, init]) => init?.method === 'POST')!
  const input = JSON.parse(await blobText(post[1]!.body as Blob))
  expect(input.changes.map((c: { path: string }) => c.path)).toEqual(['scripts/test.sh'])
  expect(JSON.parse(input.specJson).title).toBe('DB edit')
  expect(atob(session.state.versions.draft.files.find(f => f.path === 'scripts/test.sh')!.contentBase64)).toBe('new')
})

it.each([{ title: 3 }, { nodes: 'invalid' }, { nodes: [{ id: 'bad' }] }])('saves DB over Git with invalid editor fields: %j', async fields => {
  const { hook, session, id } = setup()
  act(() => { setGitBaseline(session, id, stringify({ id, ...fields })); useEditorStore.getState().updateSpecField('title', 'DB edit') })
  expect(hook.result.current.baselineComparison).toBe('uncomparable')
  await act(async () => { await expect(hook.result.current.save()).resolves.toMatchObject({ mode: 'unified' }) })
  expect(useEditorStore.getState()._databaseSpec?.title).toBe('DB edit')
})

it('preflights Pack rename even with an incomparable Git baseline', async () => {
  const { hook, session, id, spec } = setup()
  act(() => {
    setGitBaseline(session, id, stringify({ id }))
    const v = session.state.versions.draft
    updateVersion(session, 'draft', { files: v.files.map(file => file.path === 'workflow.pack.yaml'
      ? { ...file, contentBase64: btoa(stringify({ id: 'renamed-pack', workflows: [id] })) } : file) })
  })
  const originalFiles = structuredClone(session.state.versions.draft.files)
  await act(async () => { await expect(hook.result.current.save()).rejects.toThrow('Pack manifest') })
  expect(fetchJson).not.toHaveBeenCalled(); expect(mocks.read).not.toHaveBeenCalled()
  expect(session.state.uncertainRename).toBeNull()
  expect(session.state.versions.draft.files).toEqual(originalFiles)
  expect(useEditorStore.getState().spec).toEqual(spec)
})

// Existing drafts no longer use the two-stage DB-only + legacy Pack path.
// Exercise each old candidate shape against both unavailable capability codes,
// without silently claiming a permission result or an attempted Git operation.
it.each([401, 404] as const)('capability HTTP %i blocks existing-draft writes and retains every candidate shape', async status => {
  // Each shape has its own workflow/session and separately checked write calls.
  for (const shape of ['canvas-and-script', 'moved-yaml', 'write-denied', 'historical-yaml', 'divergent-yaml', 'script-only'] as const) {
    const { hook, session, id, spec } = setup()
    const originalFetch = vi.mocked(fetch).getMockImplementation()!
    vi.mocked(fetch).mockImplementation((url, init) => String(url).endsWith('/unified-save') && init?.method === 'GET'
      ? Promise.resolve(new Response(null, { status })) : originalFetch(url, init))
    act(() => {
      if (shape === 'historical-yaml') setGitBaseline(session, id, `# historical document\nid: ${id}\n`)
      editScript(session)
      if (shape === 'canvas-and-script') useEditorStore.getState().updateSpecField('title', 'DB only')
      if (shape === 'write-denied') {
        const v = session.state.versions.draft
        updateVersion(session, 'draft', { snapshot: { ...v.snapshot!, canWrite: false } })
        useEditorStore.getState().updateSpecField('title', 'saved DB')
      }
      if (shape === 'divergent-yaml') {
        editWorkflowFile(session, id, { ...spec, title: 'File' })
        useEditorStore.getState().updateSpecField('title', 'Canvas')
      }
      if (shape === 'moved-yaml') {
        const v = session.state.versions.draft, target = 'flows/new.yaml'
        updateVersion(session, 'draft', { files: v.files.map(f => f.path === 'workflow.pack.yaml'
          ? { ...f, contentBase64: btoa(stringify({ id: 'pack', workflows: [{ id, file: target }] })) }
          : f.path === `workflows/${id}.yaml` ? { ...f, path: target, contentBase64: btoa(stringify({ ...spec, title: 'local YAML' })) } : f) })
        useEditorStore.getState().updateSpecField('title', 'local YAML')
      }
    })
    expect(session.state.versions.draft.files.find(f => f.path === 'scripts/test.sh')?.contentBase64, shape).toBe(btoa('new'))
    expect(session.state.versions.draft.snapshot?.versionFiles.find(f => f.path === 'scripts/test.sh')?.contentBase64, shape).toBe(btoa('old'))
    const beforeFiles = structuredClone(session.state.versions.draft.files)
    const beforeSnapshot = structuredClone(session.state.versions.draft.snapshot)
    const beforeSpec = structuredClone(useEditorStore.getState().spec)
    await act(async () => { await expect(hook.result.current.save()).rejects.toThrow('统一保存不可用，无法同步草稿(wf)工作流 YAML；数据库和 Git 均未写入，本地修改仍保留。') })
    expect(session.state.versions.draft.files, shape).toEqual(beforeFiles)
    expect(session.state.versions.draft.snapshot, shape).toEqual(beforeSnapshot)
    expect(useEditorStore.getState().spec, shape).toEqual(beforeSpec)
    expect(useEditorStore.getState()._databaseSpec, shape).toEqual(spec)
    expect(editorWorkflowWriteState(id).phase, shape).toBe('clear')
    expect(vi.mocked(fetch).mock.calls.filter(([, init]) => init?.method !== 'GET'), shape).toEqual([])
    expect(vi.mocked(fetchJson).mock.calls.filter(([, init]) => init?.method && init.method !== 'GET'), shape).toEqual([])
    expect(vi.mocked(fetch).mock.calls.filter(([url, init]) => String(url).endsWith('/unified-save') && init?.method === 'GET'), shape).toHaveLength(1)
    hook.unmount(); vi.resetAllMocks(); vi.restoreAllMocks(); vi.unstubAllGlobals()
  }
})

it.each([403, 503] as const)('capability HTTP %i fails closed without turning errors into legacy permission', async status => {
  const { hook, session, id, spec } = setup()
  const original = vi.mocked(fetch).getMockImplementation()!
  vi.mocked(fetch).mockImplementation((url, init) => String(url).endsWith('/unified-save') && init?.method === 'GET'
    ? Promise.resolve(new Response(null, { status })) : original(url, init))
  act(() => { editScript(session); useEditorStore.getState().updateSpecField('title', 'unsaved') })
  await act(async () => { await expect(hook.result.current.save()).rejects.toThrow(`统一保存能力读取失败（HTTP ${status}），未发送写入。`) })
  expect(useEditorStore.getState()._databaseSpec).toEqual(spec)
  expect(useEditorStore.getState().spec?.title).toBe('unsaved')
  expect(session.state.versions.draft.files.find(f => f.path === 'scripts/test.sh')?.contentBase64).toBe(btoa('new'))
  expect(editorWorkflowWriteState(id).phase).toBe('clear')
  expect(vi.mocked(fetch).mock.calls.filter(([, init]) => init?.method !== 'GET')).toEqual([])
  expect(vi.mocked(fetchJson).mock.calls.filter(([, init]) => init?.method && init.method !== 'GET')).toEqual([])
})

it.each(['changed-incomplete', 'wrong-identity'] as const)('legacy candidate validation specifically rejects %s YAML', async variant => {
  const { hook, session, id, spec } = setup()
  const originalFetch = vi.mocked(fetch).getMockImplementation()!
  vi.mocked(fetch).mockImplementation((url, init) => String(url).endsWith('/unified-save') && init?.method === 'GET'
    ? Promise.resolve(new Response(null, { status: 404 })) : originalFetch(url, init))
  act(() => { setGitBaseline(session, id, stringify({ id })); editScript(session); const v = session.state.versions.draft
    updateVersion(session, 'draft', { files: v.files.map(f => f.path === `workflows/${id}.yaml` ? { ...f, contentBase64: btoa(stringify({ id: variant === 'wrong-identity' ? 'other' : id, extra: true })) } : f) }) })
  const v = session.state.versions.draft
  expect(() => legacyPackCandidate(v.files, id, v.snapshot!)).toThrow(variant === 'wrong-identity'
    ? '工作流 YAML 身份与当前画布不一致。' : '必须包含非空 id、title，以及 nodes 数组')
  await act(async () => { await expect(hook.result.current.save()).rejects.toThrow('统一保存不可用，无法同步草稿(wf)工作流 YAML；数据库和 Git 均未写入，本地修改仍保留。') })
  expect(vi.mocked(fetch).mock.calls.some(([, init]) => init?.method !== 'GET')).toBe(false)
  expect(vi.mocked(fetchJson).mock.calls.some(([url, init]) => isGitPut(url, init))).toBe(false)
  expect(session.state.versions.draft.files.find(f => f.path === 'scripts/test.sh')?.contentBase64).toBe(btoa('new'))
  expect(useEditorStore.getState()._databaseSpec).toEqual(spec)
})

it('legacy candidate validation preserves historical YAML bytes across a path move', () => {
  const { session, id } = setup(), target = 'flows/legacy.yaml', original = `# historical document\nid: ${id}\n`
  act(() => { setGitBaseline(session, id, original); const v = session.state.versions.draft
    updateVersion(session, 'draft', { files: v.files.map(f => f.path === 'workflow.pack.yaml'
      ? { ...f, contentBase64: btoa(stringify({ id: 'pack', workflows: [{ id, file: target }] })) }
      : f.path === `workflows/${id}.yaml` ? { ...f, path: target } : f) }) })
  const v = session.state.versions.draft
  expect(legacyPackCandidate(v.files, id, v.snapshot!)).toEqual({ packId: 'pack', path: target, spec: null })
  expect(atob(v.files.find(f => f.path === target)!.contentBase64)).toBe(original)
  expect(vi.mocked(fetch).mock.calls).toEqual([]); expect(vi.mocked(fetchJson).mock.calls).toEqual([])
})

it.each([true, false])('unified save rejects divergent canvas and YAML without writes, canWrite=%s', async canWrite => {
  const { hook, session, id, spec } = setup()
  act(() => { useEditorStore.getState().updateSpecField('title', 'Canvas'); editScript(session); const v = session.state.versions.draft
    updateVersion(session, 'draft', { snapshot: { ...v.snapshot!, canWrite }, files: v.files.map(f => f.path === `workflows/${id}.yaml` ? { ...f, contentBase64: btoa(stringify({ ...spec, title: 'File' })) } : f) }) })
  await act(async () => { await expect(hook.result.current.save()).rejects.toThrow('画布和文件同时修改了工作流 YAML，请先统一内容；草稿仍保留。') })
  expect(vi.mocked(fetch).mock.calls.some(([, init]) => init?.method === 'POST' || init?.method === 'PUT')).toBe(false)
  expect(vi.mocked(fetchJson).mock.calls.some(([url, init]) => isGitPut(url, init))).toBe(false)
  expect(useEditorStore.getState()._databaseSpec).toEqual(spec)
  expect(useEditorStore.getState().isDirty).toBe(true)
  expect(atob(session.state.versions.draft.files.find(f => f.path === `workflows/${id}.yaml`)!.contentBase64)).toContain('File')
  expect(session.state.versions.draft.files.find(f => f.path === 'scripts/test.sh')?.contentBase64).toBe(btoa('new'))
})

it('unknown unified response persists a write marker across reload and blocks retry', async () => {
  const { hook, session, id, spec } = setup()
  const original = vi.mocked(fetch).getMockImplementation()!
  vi.mocked(fetch).mockImplementation((url, init) => init?.method === 'POST' ? Promise.reject(new Error('lost')) : original(url, init))
  act(() => { editScript(session); useEditorStore.getState().updateSpecField('title', 'unsaved') })
  await act(async () => { await expect(hook.result.current.save()).rejects.toThrow('统一保存未能完整确认') })
  expect(editorWorkflowWriteState(id).phase).toBe('unknown')
  expect(() => assertEditorWorkflowReadable(id)).toThrow('人工核对')
  expect(useEditorStore.getState()._databaseSpec).toEqual(spec)
  expect(useEditorStore.getState().spec?.title).toBe('unsaved')
  expect(session.state.versions.draft.files.find(f => f.path === 'scripts/test.sh')?.contentBase64).toBe(btoa('new'))
  expect(vi.mocked(fetchJson).mock.calls.some(([url, init]) => isGitPut(url, init))).toBe(false)
  const symbol = Symbol.for('avernet.editor.workflow-write-guard.v1')
  const root = globalThis as typeof globalThis & { [key: symbol]: unknown }, previous = root[symbol]
  try {
    delete root[symbol]; vi.resetModules()
    const reloaded = await import('../workflowWriteGuard')
    expect(reloaded.editorWorkflowWriteState(id).phase).toBe('unknown')
    expect(() => reloaded.assertEditorWorkflowReadable(id)).toThrow('人工核对')
  } finally { root[symbol] = previous }
  const before = vi.mocked(fetch).mock.calls.length
  await act(async () => { await expect(hook.result.current.save()).rejects.toThrow('人工核对') })
  expect(vi.mocked(fetch)).toHaveBeenCalledTimes(before)
})

it('unified timeout stays locked after a late success response', async () => {
  const { hook, session, id, spec } = setup()
  const original = vi.mocked(fetch).getMockImplementation()!
  let release!: () => void, start!: () => void
  const gate = new Promise<void>(r => { release = r }), began = new Promise<void>(r => { start = r })
  vi.mocked(fetch).mockImplementation(async (url, init) => {
    if (String(url).endsWith('/unified-save') && init?.method === 'POST') { start(); await gate }
    return original(url, init)
  })
  act(() => { editScript(session); useEditorStore.getState().updateSpecField('title', 'unsaved') })
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
  try {
    let pending!: ReturnType<typeof hook.result.current.save>
    act(() => { pending = hook.result.current.save() })
    const rejection = expect(pending).rejects.toThrow('统一保存未能完整确认')
    await began
    await act(async () => { await vi.advanceTimersByTimeAsync(UNIFIED_SAVE_TIMEOUT_MS); await rejection })
    expect(editorWorkflowWriteState(id).phase).toBe('unknown')
    const key = `avernet.editor.pending-workflow-write.v1:${encodeURIComponent(id)}`
    const marker = sessionStorage.getItem(key)
    expect(marker).not.toBeNull()
    await act(async () => { release(); await Promise.resolve(); await Promise.resolve() })
    expect(editorWorkflowWriteState(id).phase).toBe('unknown')
    expect(sessionStorage.getItem(key)).toBe(marker)
    expect(useEditorStore.getState()._databaseSpec).toEqual(spec)
    expect(useEditorStore.getState().spec?.title).toBe('unsaved')
    expect(session.state.versions.draft.files.find(f => f.path === 'scripts/test.sh')?.contentBase64).toBe(btoa('new'))
    expect(vi.mocked(fetchJson).mock.calls.some(([url, init]) => isGitPut(url, init))).toBe(false)
  } finally { vi.useRealTimers() }
})

it('ignores an earlier draft read that resolves after a current-version unified save', async () => {
  const { hook, id, session } = setup()
  const oldSnapshot = structuredClone(session.state.versions.draft.snapshot!)
  const original = vi.mocked(fetchJson).getMockImplementation()!
  let resolveOld!: (snapshot: typeof oldSnapshot) => void
  vi.mocked(fetchJson).mockImplementation((url, init) => {
    if (url.endsWith('/pack-version?version=draft') && init?.signal) return new Promise(resolve => { resolveOld = resolve })
    return original(url, init)
  })
  act(() => {
    load(session, id, 'draft')
    const currentSnapshot = { ...oldSnapshot, baseRef: `refs/tags/deploy/${id}/#1` }
    publish(session, { ...session.state, version: 'current', versions: { ...session.state.versions,
      current: { ...session.state.versions.current, snapshot: currentSnapshot, files: structuredClone(currentSnapshot.versionFiles), loading: false } } })
    useEditorStore.getState().updateSpecField('title', 'Saved canvas')
  })
  expect(resolveOld).toBeTypeOf('function')
  await act(async () => { await hook.result.current.save() })
  act(() => updateVersion(session, 'draft', { files: session.state.versions.draft.files.map(file => file.path === 'scripts/test.sh'
    ? { ...file, contentBase64: btoa('new edit after save') } : file) }))
  await act(async () => { resolveOld(oldSnapshot); await Promise.resolve() })
  expect(session.state.versions.draft.snapshot?.baseCommit).toBe(B)
  expect(session.state.versions.draft.files.find(file => file.path === 'scripts/test.sh')?.contentBase64).toBe(btoa('new edit after save'))
})

function yamlWithNewNodeFixture() {
  const ctx = setup()
  const spec = { ...ctx.spec, nodes: [{ id: 'old', title: 'Old', phase: 'execute', executor: { type: 'done' } }] }
  const candidate = { ...spec, nodes: [...spec.nodes, { id: 'added', title: 'Added', phase: 'execute', executor: { type: 'done' } }] }
  act(() => {
    useEditorStore.getState().loadSpec(spec); useEditorStore.setState({ _databasePackId: 'pack' })
    mocks.read.mockResolvedValue(structuredClone(spec))
    const version = ctx.session.state.versions.draft
    const files = version.files.map(file => file.path === `workflows/${ctx.id}.yaml` ? { ...file, contentBase64: btoa(stringify(spec)) } : file)
    updateVersion(ctx.session, 'draft', { snapshot: { ...version.snapshot!, versionFiles: structuredClone(files) }, files })
    editWorkflowFile(ctx.session, ctx.id, candidate)
  })
  return { ...ctx, spec, candidate }
}

it('rejects before POST when layout moves during YAML-only DB baseline read', async () => {
  const { hook, session, id, candidate } = yamlWithNewNodeFixture()
  const original = vi.mocked(fetchJson).getMockImplementation()!
  let release!: () => void, start!: () => void
  const gate = new Promise<void>(r => { release = r }), began = new Promise<void>(r => { start = r })
  vi.mocked(fetchJson).mockImplementation(async (url, init) => {
    if (url.endsWith('/editor-draft') && init?.method === 'GET') { start(); await gate }
    return original(url, init)
  })
  let saving!: ReturnType<typeof hook.result.current.save>
  act(() => { saving = hook.result.current.save() }); await began
  act(() => useEditorStore.getState().moveNode('old', 701, 303))
  await act(async () => { release(); await expect(saving).rejects.toThrow('核对数据库期间编辑内容或会话已变化') })
  expect(useEditorStore.getState().positions.old).toEqual({ x: 701, y: 303 })
  expect(useEditorStore.getState().isLayoutDirty).toBe(true)
  expect(atob(session.state.versions.draft.files.find(f => f.path === `workflows/${id}.yaml`)!.contentBase64)).toContain('id: added')
  expect(candidate.nodes).toHaveLength(2)
  expect(vi.mocked(fetch).mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(0)
  expect(editorWorkflowWriteState(id).phase).toBe('clear')
})

it.each(['title', 'layout'] as const)('quarantines YAML-only confirmation when %s changes during POST', async kind => {
  const { hook, session, id, spec } = yamlWithNewNodeFixture()
  const original = vi.mocked(fetch).getMockImplementation()!
  let release!: () => void, start!: () => void
  const gate = new Promise<void>(r => { release = r }), began = new Promise<void>(r => { start = r })
  vi.mocked(fetch).mockImplementation(async (url, init) => {
    if (String(url).endsWith('/unified-save') && init?.method === 'POST') { start(); await gate }
    return original(url, init)
  })
  let saving!: ReturnType<typeof hook.result.current.save>
  act(() => { saving = hook.result.current.save() }); await began
  act(() => {
    if (kind === 'title') useEditorStore.getState().updateSpecField('title', 'New local title')
    else useEditorStore.getState().moveNode('old', 701, 303)
  })
  await act(async () => { release(); await expect(saving).rejects.toThrow('文件 YAML 已写入，但保存期间画布或布局发生变化') })
  expect(useEditorStore.getState().spec?.nodes.map(n => n.id)).toEqual(['old'])
  expect(useEditorStore.getState()._databaseSpec).toEqual(spec)
  if (kind === 'title') expect(useEditorStore.getState().spec?.title).toBe('New local title')
  else expect(useEditorStore.getState().positions.old).toEqual({ x: 701, y: 303 })
  expect(atob(session.state.versions.draft.files.find(f => f.path === `workflows/${id}.yaml`)!.contentBase64)).toContain('id: added')
  expect(editorWorkflowWriteState(id).phase).toBe('unknown')
  expect(session.state.postSaveVerificationError).toContain('人工核对')
  await expect(hook.result.current.save()).rejects.toThrow('人工核对')
  expect(vi.mocked(fetch).mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1)
})

it('allows read-only comparison while unapplied form input is retained', async () => {
  const { hook } = setup()
  act(() => useEditorStore.getState().setPendingEdit('workflow:title', 'unapplied'))
  let review!: Awaited<ReturnType<typeof hook.result.current.prepareReconciliation>>
  await act(async () => { review = await hook.result.current.prepareReconciliation() })
  expect(review.candidateSpecJson).toContain('Initial')
  expect(useEditorStore.getState().pendingEdits['workflow:title']).toBe('unapplied')
  act(() => hook.result.current.cancelReconciliation(review))
  expect(useEditorStore.getState().pendingEdits['workflow:title']).toBe('unapplied')
})
