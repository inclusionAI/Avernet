import { act, cleanup, renderHook } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { stringify } from 'yaml'
import { fetchJson } from '@avernet/clawweb-shared/web/api/client'
import { useEditorStore } from '../store'
import { sessionFor, publish, updateVersion } from '../packVersionSession'
import { usePackCanvasSession } from '../usePackCanvasSession'

const mocks = vi.hoisted(() => ({ read: vi.fn() }))
vi.mock('../../api/workflowDocument', () => ({ readWorkflowDocument: mocks.read }))

vi.mock('@avernet/clawweb-shared/web/api/client', () => ({ fetchJson: vi.fn() }))
const A = 'a'.repeat(40), B = 'b'.repeat(40)
let sequence = 0
const blobText = (blob: Blob) => new Promise<string>(resolve => { const r = new FileReader(); r.onload = () => resolve(String(r.result)); r.readAsText(blob) })
function setup() {
  const id = `db-canvas-${++sequence}`, spec = { id, title: 'Initial', version: '1', nodes: [] }
  useEditorStore.getState().reset({ force: true }); useEditorStore.getState().loadSpec(spec)
  const files = [{ path: 'workflow.pack.yaml', contentBase64: btoa(stringify({ id: 'pack', workflows: [id] })), mode: '100644' as const },
    { path: `workflows/${id}.yaml`, contentBase64: btoa(stringify(spec)), mode: '100644' as const },
    { path: 'scripts/test.sh', contentBase64: btoa('old'), mode: '100644' as const }]
  const snapshot = { workflowId: id, packId: 'pack', baseRef: `origin/saved/${id}`, baseCommit: A, draftHead: A, canWrite: true, versionFiles: files }
  const session = sessionFor(id)
  publish(session, { ...session.state, version: 'draft', versions: { ...session.state.versions,
    draft: { snapshot, files: structuredClone(files), loading: false, error: null, blocked: false, notice: null } } })
  mocks.read.mockResolvedValue(structuredClone(spec))
  vi.mocked(fetchJson).mockImplementation(async (url, init) => {
    const input = JSON.parse(await blobText(init!.body as Blob))
    if (url === '/api/auto-heal/save') { mocks.read.mockResolvedValue(structuredClone(input.spec)); return { ok: true, saved: true, workflowId: id, packId: 'pack' } }
    return { branch: `saved/${id}`, commit: B, packId: 'pack', version: 1, requestId: input.requestId }
  })
  const hook = renderHook(() => usePackCanvasSession(id, spec))
  return { id, spec, session, hook }
}
function editScript(session: ReturnType<typeof sessionFor>) {
  const state = session.state.versions.draft
  updateVersion(session, 'draft', { files: state.files.map(f => f.path === 'scripts/test.sh' ? { ...f, contentBase64: btoa('new') } : f) })
}
afterEach(() => { cleanup(); vi.resetAllMocks(); vi.restoreAllMocks() })
it('saves a DB-only canvas without inventing a commit', async () => {
  const { hook, id } = setup()
  act(() => useEditorStore.getState().updateSpecField('title', 'DB draft'))
  let result: Awaited<ReturnType<typeof hook.result.current.save>>
  await act(async () => { result = await hook.result.current.save() })
  expect(result!).toEqual({ dbSaved: true, commit: null })
  expect(vi.mocked(fetchJson).mock.calls).toHaveLength(1)
  expect(JSON.parse(await blobText(vi.mocked(fetchJson).mock.calls[0][1]!.body as Blob))).toMatchObject({ workflowId: id, spec: { title: 'DB draft' } })
  expect(useEditorStore.getState().isDirty).toBe(false)
})
it('saves script-only changes by PUT without posting the DB', async () => {
  const { hook, session } = setup(); act(() => editScript(session))
  await act(async () => { expect(await hook.result.current.save()).toEqual({ dbSaved: false, commit: B }) })
  const calls = vi.mocked(fetchJson).mock.calls
  expect(calls).toHaveLength(1); expect(calls[0][1]?.method).toBe('PUT')
  const input = JSON.parse(await blobText(calls[0][1]!.body as Blob))
  expect(input.changes).toHaveLength(1); expect(input.changes[0].path).toBe('scripts/test.sh')
})
it('saves both stores separately and never includes the workflow YAML in the PUT', async () => {
  const { hook, session } = setup()
  act(() => { useEditorStore.getState().updateSpecField('title', 'Changed'); editScript(session) })
  await act(async () => { expect(await hook.result.current.save()).toEqual({ dbSaved: true, commit: B }) })
  const calls = vi.mocked(fetchJson).mock.calls
  expect(calls.map(([url]) => url.includes('auto-heal') ? 'DB' : 'Git')).toEqual(['DB', 'Git'])
  expect(JSON.parse(await blobText(calls[1][1]!.body as Blob)).changes.map((c: { path: string }) => c.path)).toEqual(['scripts/test.sh'])
})
it('reports DB success and preserves files when Git outcome is unknown without journal recovery', async () => {
  const { hook, session } = setup()
  act(() => { useEditorStore.getState().updateSpecField('title', 'Changed'); editScript(session) })
  const original = vi.mocked(fetchJson).getMockImplementation()!
  vi.mocked(fetchJson).mockImplementation((url, init) => init?.method === 'PUT' ? Promise.reject(new Error('lost')) : original(url, init))
  await act(async () => { await expect(hook.result.current.save()).rejects.toThrow('部分成功') })
  expect(useEditorStore.getState().isDirty).toBe(false)
  expect(session.state.versions.draft.blocked).toBe(true)
  expect(session.state.versions.draft.files.find(f => f.path === 'scripts/test.sh')?.contentBase64).toBe(btoa('new'))
  expect(session.state.reconciliation).not.toBeNull()
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
it('saves an existing DB workflow even when no Git snapshot is available', async () => {
  const { hook, session } = setup()
  act(() => {
    updateVersion(session, 'draft', { snapshot: null, files: [], loading: false, error: 'Git unavailable' })
    useEditorStore.getState().updateSpecField('title', 'DB only')
  })
  await act(async () => { expect(await hook.result.current.save()).toEqual({ dbSaved: true, commit: null }) })
  expect(vi.mocked(fetchJson).mock.calls.filter(([, init]) => init?.method === 'PUT')).toHaveLength(0)
  expect(useEditorStore.getState().isDirty).toBe(false)
})
it('routes an edited workflow YAML file to DB rather than Git', async () => {
  const { hook, session, spec, id } = setup()
  act(() => {
    const v = session.state.versions.draft
    updateVersion(session, 'draft', { files: v.files.map(f => f.path === `workflows/${id}.yaml` ? { ...f, contentBase64: btoa(stringify({ ...spec, title: 'From YAML' })) } : f) })
  })
  await act(async () => { expect(await hook.result.current.save()).toEqual({ dbSaved: true, commit: null }) })
  expect(JSON.parse(await blobText(vi.mocked(fetchJson).mock.calls[0][1]!.body as Blob)).spec.title).toBe('From YAML')
  expect(useEditorStore.getState().spec?.title).toBe('From YAML')
  expect(session.state.versions.draft.files).toEqual(session.state.versions.draft.snapshot?.versionFiles)
})
it('retains canvas changes made while the DB save is in flight', async () => {
  const { hook, id } = setup()
  let resolve!: (value: unknown) => void
  vi.mocked(fetchJson).mockReturnValueOnce(new Promise(r => { resolve = r }))
  act(() => useEditorStore.getState().updateSpecField('title', 'First'))
  let pending!: ReturnType<typeof hook.result.current.save>
  await act(async () => { pending = hook.result.current.save(); await Promise.resolve() })
  act(() => useEditorStore.getState().updateSpecField('title', 'Later'))
  mocks.read.mockResolvedValue({ id, title: 'First', version: '1', nodes: [] })
  await act(async () => { resolve({ ok: true, saved: true, workflowId: id, packId: 'pack' }); await pending })
  expect(useEditorStore.getState().spec?.title).toBe('Later')
  expect(useEditorStore.getState().isDirty).toBe(true)
})
it('does not accept an unverifiable DB save response as success', async () => {
  const { hook } = setup()
  act(() => useEditorStore.getState().updateSpecField('title', 'Changed'))
  vi.mocked(fetchJson).mockResolvedValueOnce({ ok: true, commit: B })
  await act(async () => { await expect(hook.result.current.save()).rejects.toThrow('响应无法验证') })
  expect(useEditorStore.getState().isDirty).toBe(true)
})

function editWorkflowFile(session: ReturnType<typeof sessionFor>, id: string, spec: unknown) {
  const version = session.state.versions.draft
  updateVersion(session, 'draft', { files: version.files.map(file => file.path === `workflows/${id}.yaml`
    ? { ...file, contentBase64: btoa(stringify(spec)) } : file) })
}

it('does not silently overwrite a newer DB canvas draft with an edit based on old Git YAML', async () => {
  const { hook, session, spec, id } = setup()
  act(() => useEditorStore.getState().updateSpecField('title', 'New DB title'))
  await act(async () => { await hook.result.current.save() })
  act(() => editWorkflowFile(session, id, { ...spec, config: { fromGit: true } }))
  const originalFiles = structuredClone(session.state.versions.draft.files)
  const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
  vi.mocked(fetchJson).mockClear(); mocks.read.mockClear()
  await act(async () => { await expect(hook.result.current.save()).rejects.toThrow('已取消替换数据库草稿') })
  expect(confirm).toHaveBeenCalledWith(expect.stringMatching(/完整替换数据库草稿.*不是自动合并.*可能丢失数据库修改/))
  expect(fetchJson).not.toHaveBeenCalled(); expect(mocks.read).not.toHaveBeenCalled()
  expect(useEditorStore.getState().spec?.title).toBe('New DB title')
  expect(useEditorStore.getState()._savedSpec?.title).toBe('New DB title')
  expect(session.state.versions.draft.files).toEqual(originalFiles)
})

it('allows explicit selection of a divergent Git-based candidate after a clear warning', async () => {
  const { hook, session, spec, id } = setup()
  act(() => useEditorStore.getState().updateSpecField('title', 'New DB title'))
  await act(async () => { await hook.result.current.save() })
  const edited = { ...spec, title: 'Chosen Git title', config: { enabled: true } }
  act(() => editWorkflowFile(session, id, edited))
  const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
  vi.mocked(fetchJson).mockClear()
  await act(async () => { expect(await hook.result.current.save()).toEqual({ dbSaved: true, commit: null }) })
  expect(confirm).toHaveBeenCalledOnce()
  expect(JSON.parse(await blobText(vi.mocked(fetchJson).mock.calls[0][1]!.body as Blob)).spec).toEqual(edited)
  expect(useEditorStore.getState().spec).toEqual(edited)
  expect(useEditorStore.getState().isDirty).toBe(false)
})

it('allows a manually reconciled YAML candidate to be saved without discarding DB changes', async () => {
  const { hook, session, spec, id } = setup()
  act(() => useEditorStore.getState().updateSpecField('title', 'Preserved DB title'))
  await act(async () => { await hook.result.current.save() })
  const merged = { ...spec, title: 'Preserved DB title', config: { enabled: true } }
  act(() => editWorkflowFile(session, id, merged))
  const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
  vi.mocked(fetchJson).mockClear()
  await act(async () => { await hook.result.current.save() })
  expect(confirm).toHaveBeenCalledOnce()
  expect(JSON.parse(await blobText(vi.mocked(fetchJson).mock.calls[0][1]!.body as Blob)).spec).toEqual(merged)
  expect(useEditorStore.getState().spec).toEqual(merged)
})

it('rechecks both canvas and files after the explicit overwrite confirmation', async () => {
  const { hook, session, spec, id } = setup()
  act(() => useEditorStore.getState().updateSpecField('title', 'New DB title'))
  await act(async () => { await hook.result.current.save() })
  act(() => editWorkflowFile(session, id, { ...spec, title: 'Git edit' }))
  vi.spyOn(window, 'confirm').mockImplementation(() => { editScript(session); return true })
  vi.mocked(fetchJson).mockClear()
  await act(async () => { await expect(hook.result.current.save()).rejects.toThrow('确认期间编辑内容已变化') })
  expect(fetchJson).not.toHaveBeenCalled()
  expect(session.state.versions.draft.files.find(file => file.path === 'scripts/test.sh')?.contentBase64).toBe(btoa('new'))
})

it('blocks a DB save if the DB changed since the editor baseline was read', async () => {
  const { hook, id, session } = setup()
  act(() => { useEditorStore.getState().updateSpecField('title', 'Local edit'); editScript(session) })
  mocks.read.mockResolvedValue({ id, title: 'External edit', version: '1', nodes: [] })
  await act(async () => { await expect(hook.result.current.save()).rejects.toThrow('数据库草稿自读取后已变化') })
  expect(fetchJson).not.toHaveBeenCalled()
  expect(useEditorStore.getState().spec?.title).toBe('Local edit')
  expect(useEditorStore.getState().isDirty).toBe(true)
  expect(session.state.versions.draft.files.find(file => file.path === 'scripts/test.sh')?.contentBase64).toBe(btoa('new'))
})

it('does not mark the request clean or save files when DB normalization changes it', async () => {
  const { hook, id, session } = setup()
  act(() => { useEditorStore.getState().updateSpecField('title', '  Local title  '); editScript(session) })
  const request = useEditorStore.getState().spec!
  mocks.read.mockResolvedValueOnce(useEditorStore.getState()._savedSpec).mockResolvedValueOnce({ ...request, title: 'Local title' })
  vi.mocked(fetchJson).mockResolvedValueOnce({ ok: true, saved: true, workflowId: id, packId: 'pack' })
  await act(async () => { await expect(hook.result.current.save()).rejects.toThrow(/保存内容与请求不同.*title/) })
  expect(useEditorStore.getState().isDirty).toBe(true)
  expect(useEditorStore.getState().spec?.title).toBe('  Local title  ')
  expect(useEditorStore.getState()._savedSpec?.title).toBe('Initial')
  expect(vi.mocked(fetchJson).mock.calls.filter(([, init]) => init?.method === 'PUT')).toHaveLength(0)
  expect(session.state.versions.draft.files.find(file => file.path === 'scripts/test.sh')?.contentBase64).toBe(btoa('new'))
})

it('keeps a YAML file draft when the DB re-read fails after a successful save', async () => {
  const { hook, session, id, spec } = setup()
  act(() => editWorkflowFile(session, id, { ...spec, title: 'File edit' }))
  mocks.read.mockResolvedValueOnce(spec).mockRejectedValueOnce(new Error('offline'))
  vi.mocked(fetchJson).mockResolvedValueOnce({ ok: true, saved: true, workflowId: id, packId: 'pack' })
  await act(async () => { await expect(hook.result.current.save()).rejects.toThrow('数据库已报告保存成功，但无法重新读取核对') })
  expect(useEditorStore.getState().spec?.title).toBe('Initial')
  expect(session.state.versions.draft.files.find(file => file.path === `workflows/${id}.yaml`)?.contentBase64).toBe(btoa(stringify({ ...spec, title: 'File edit' })))
})
it('does not start a DB write if the canvas changes while the baseline is being read', async () => {
  const { hook, spec } = setup()
  let resolve!: (value: unknown) => void
  mocks.read.mockReturnValueOnce(new Promise(r => { resolve = r }))
  act(() => useEditorStore.getState().updateSpecField('title', 'First'))
  let pending!: ReturnType<typeof hook.result.current.save>
  act(() => { pending = hook.result.current.save() })
  act(() => useEditorStore.getState().updateSpecField('title', 'Later'))
  await act(async () => { resolve(spec); await expect(pending).rejects.toThrow('核对数据库期间编辑内容或会话已变化') })
  expect(fetchJson).not.toHaveBeenCalled()
  expect(useEditorStore.getState().spec?.title).toBe('Later')
  expect(useEditorStore.getState().isDirty).toBe(true)
})
it('retains a YAML candidate and a draft begun while the DB write is in flight', async () => {
  const { hook, id, spec, session } = setup()
  const edited = { ...spec, title: 'File edit' }
  act(() => editWorkflowFile(session, id, edited))
  let resolve!: (value: unknown) => void
  vi.mocked(fetchJson).mockReturnValueOnce(new Promise(r => { resolve = r }))
  let pending!: ReturnType<typeof hook.result.current.save>
  await act(async () => { pending = hook.result.current.save(); await Promise.resolve() })
  act(() => useEditorStore.getState().setPendingEdit('workflow:title', 'in progress'))
  mocks.read.mockResolvedValue(edited)
  await act(async () => { resolve({ ok: true, saved: true, workflowId: id, packId: 'pack' }); await expect(pending).rejects.toThrow('部分成功') })
  expect(useEditorStore.getState().pendingEdits['workflow:title']).toBe('in progress')
  expect(useEditorStore.getState().spec?.title).toBe('Initial')
  expect(session.state.versions.draft.files.find(file => file.path === `workflows/${id}.yaml`)?.contentBase64).toBe(btoa(stringify(edited)))
})
it('requires explicit selection when Git would erase DB-only node coordinates', async () => {
  const { hook, session, spec, id } = setup()
  const git = { ...spec, nodes: [{ id: 'n', title: 'Node', phase: 'work', executor: { type: 'done' }, _x: 10, _y: 20 }] }
  const db = { ...git, nodes: [{ ...git.nodes[0], _x: 90 }] }
  act(() => {
    useEditorStore.getState().loadSpec(db)
    const v = session.state.versions.draft
    const baseFiles = v.files.map(file => file.path === `workflows/${id}.yaml` ? { ...file, contentBase64: btoa(stringify(git)) } : file)
    updateVersion(session, 'draft', { snapshot: { ...v.snapshot!, versionFiles: baseFiles }, files: structuredClone(baseFiles) })
    editWorkflowFile(session, id, { ...git, title: 'File title' })
  })
  const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
  await act(async () => { await expect(hook.result.current.save()).rejects.toThrow('已取消替换数据库草稿') })
  expect(confirm).toHaveBeenCalledWith(expect.stringContaining('nodes[0]._x'))
  expect(fetchJson).not.toHaveBeenCalled()
  expect(useEditorStore.getState().spec?.nodes[0]._x).toBe(90)
})
