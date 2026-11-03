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
const A = 'a'.repeat(40), B = 'b'.repeat(40), destination = 'flows/moved.yaml'
let sequence = 0
const blobText = (blob: Blob) => new Promise<string>(resolve => { const r = new FileReader(); r.onload = () => resolve(String(r.result)); r.readAsText(blob) })
function setup() {
  const id = `move-canvas-${++sequence}`, spec = { id, title: 'Initial', version: '1', nodes: [] }
  useEditorStore.getState().reset({ force: true }); useEditorStore.getState().loadSpec(spec)
  const path = `workflows/${id}.yaml`, baseline = btoa(`# Keep exact bytes and comments\n${stringify(spec)}`)
  const files = [{ path: 'workflow.pack.yaml', contentBase64: btoa(stringify({ id: 'pack', workflows: [id] })), mode: '100644' as const },
    { path, contentBase64: baseline, mode: '100644' as const }, { path: 'scripts/test.sh', contentBase64: btoa('old'), mode: '100644' as const }]
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
  return { id, spec, path, baseline, session, hook: renderHook(() => usePackCanvasSession(id, spec)) }
}
function move(ctx: ReturnType<typeof setup>, content = ctx.baseline, keepOld = false, packId = 'pack') {
  const state = ctx.session.state.versions.draft, old = state.files.find(file => file.path === ctx.path)!
  updateVersion(ctx.session, 'draft', { files: [...state.files.filter(file => keepOld || file.path !== ctx.path).map(file =>
    file.path === 'workflow.pack.yaml' ? { ...file, contentBase64: btoa(stringify({ id: packId, workflows: [{ id: ctx.id, file: destination }] })) } : file),
  { ...old, path: destination, mode: '100755', contentBase64: content }] })
}
afterEach(() => { cleanup(); vi.resetAllMocks(); vi.restoreAllMocks() })

it('moves exact baseline bytes and mode without writing DB for an unchanged document', async () => {
  const ctx = setup(); act(() => move(ctx))
  await act(async () => { expect(await ctx.hook.result.current.save()).toEqual({ dbSaved: false, commit: B }) })
  const calls = vi.mocked(fetchJson).mock.calls; expect(calls).toHaveLength(1)
  const input = JSON.parse(await blobText(calls[0][1]!.body as Blob))
  expect(input.changes).toContainEqual({ path: ctx.path, operation: 'delete' })
  expect(input.changes).toContainEqual({ path: destination, operation: 'create', contentBase64: ctx.baseline, mode: '100755' })
})
it('writes moved edited YAML to DB first and the unchanged baseline bytes to Git', async () => {
  const ctx = setup(), edited = { ...ctx.spec, title: 'Edited YAML' }
  act(() => { move(ctx, btoa(stringify(edited))); const v = ctx.session.state.versions.draft
    updateVersion(ctx.session, 'draft', { files: v.files.map(f => f.path === 'scripts/test.sh' ? { ...f, contentBase64: btoa('new') } : f) }) })
  await act(async () => { expect(await ctx.hook.result.current.save()).toEqual({ dbSaved: true, commit: B }) })
  const calls = vi.mocked(fetchJson).mock.calls
  expect(calls.map(([url]) => url.includes('auto-heal') ? 'DB' : 'Git')).toEqual(['DB', 'Git'])
  expect(JSON.parse(await blobText(calls[0][1]!.body as Blob)).spec).toEqual(edited)
  const input = JSON.parse(await blobText(calls[1][1]!.body as Blob))
  expect(input.changes).toContainEqual({ path: destination, operation: 'create', contentBase64: ctx.baseline, mode: '100755' })
  expect(input.changes).toContainEqual({ path: ctx.path, operation: 'delete' })
  expect(input.changes).toContainEqual({ path: 'scripts/test.sh', operation: 'replace', contentBase64: btoa('new'), mode: '100644' })
  expect(useEditorStore.getState().spec).toEqual(edited); expect(useEditorStore.getState().isDirty).toBe(false)
})
it('moves the baseline while saving a canvas-only edit to DB', async () => {
  const ctx = setup(); act(() => { move(ctx); useEditorStore.getState().updateSpecField('title', 'Canvas edit') })
  await act(async () => { expect(await ctx.hook.result.current.save()).toEqual({ dbSaved: true, commit: B }) })
  const calls = vi.mocked(fetchJson).mock.calls
  expect(JSON.parse(await blobText(calls[0][1]!.body as Blob)).spec.title).toBe('Canvas edit')
  expect(JSON.parse(await blobText(calls[1][1]!.body as Blob)).changes).toContainEqual({ path: destination, operation: 'create', contentBase64: ctx.baseline, mode: '100755' })
})
it('retains both drafts when canvas and moved YAML disagree', async () => {
  const ctx = setup(); act(() => { move(ctx, btoa(stringify({ ...ctx.spec, title: 'File' }))); useEditorStore.getState().updateSpecField('title', 'Canvas') })
  const files = structuredClone(ctx.session.state.versions.draft.files)
  await act(async () => { await expect(ctx.hook.result.current.save()).rejects.toThrow('画布和文件同时修改') })
  expect(fetchJson).not.toHaveBeenCalled(); expect(ctx.session.state.versions.draft.files).toEqual(files)
})
it('does not start Git and retains moved YAML when DB fails', async () => {
  const ctx = setup(), edited = btoa(stringify({ ...ctx.spec, title: 'File' }))
  act(() => move(ctx, edited)); vi.mocked(fetchJson).mockRejectedValueOnce(new Error('offline'))
  await act(async () => { await expect(ctx.hook.result.current.save()).rejects.toThrow('offline') })
  expect(vi.mocked(fetchJson).mock.calls.filter(([, init]) => init?.method === 'PUT')).toHaveLength(0)
  expect(ctx.session.state.versions.draft.files.find(f => f.path === destination)?.contentBase64).toBe(edited)
})
it('reports partial success and preserves the structural candidate when Git is unknown', async () => {
  const ctx = setup(), edited = { ...ctx.spec, title: 'File' }
  act(() => move(ctx, btoa(stringify(edited))))
  const original = vi.mocked(fetchJson).getMockImplementation()!
  vi.mocked(fetchJson).mockImplementation((url, init) => init?.method === 'PUT' ? Promise.reject(new Error('lost')) : original(url, init))
  await act(async () => { await expect(ctx.hook.result.current.save()).rejects.toThrow('部分成功：工作流已保存到数据库') })
  expect(useEditorStore.getState().spec).toEqual(edited); expect(useEditorStore.getState().isDirty).toBe(false)
  expect(ctx.session.state.versions.draft.files.find(f => f.path === destination)?.contentBase64).toBe(ctx.baseline)
  expect(ctx.session.state.versions.draft.files.some(f => f.path === ctx.path)).toBe(false)
  expect(ctx.session.state.versions.draft.blocked).toBe(true)
})
it('retains the candidate if it changes during the database write', async () => {
  const ctx = setup(), edited = { ...ctx.spec, title: 'File' }
  act(() => move(ctx, btoa(stringify(edited))))
  let resolve!: (value: unknown) => void; vi.mocked(fetchJson).mockReturnValueOnce(new Promise(r => { resolve = r }))
  let pending!: ReturnType<typeof ctx.hook.result.current.save>
  await act(async () => { pending = ctx.hook.result.current.save(); await Promise.resolve() })
  act(() => { const v = ctx.session.state.versions.draft; updateVersion(ctx.session, 'draft', { files: v.files.map(f => f.path === 'scripts/test.sh' ? { ...f, contentBase64: btoa('later') } : f) }) })
  mocks.read.mockResolvedValue(edited)
  await act(async () => { resolve({ ok: true, saved: true, workflowId: ctx.id, packId: 'pack' }); await expect(pending).rejects.toThrow('部分成功') })
  expect(ctx.session.state.versions.draft.files.find(f => f.path === destination)?.contentBase64).toBe(btoa(stringify(edited)))
  expect(ctx.session.state.versions.draft.files.find(f => f.path === 'scripts/test.sh')?.contentBase64).toBe(btoa('later'))
  expect(vi.mocked(fetchJson).mock.calls.filter(([, init]) => init?.method === 'PUT')).toHaveLength(0)
})
it('fails closed on an edited retained old YAML path', async () => {
  const ctx = setup(); act(() => { move(ctx, ctx.baseline, true); const v = ctx.session.state.versions.draft
    updateVersion(ctx.session, 'draft', { files: v.files.map(f => f.path === ctx.path ? { ...f, contentBase64: btoa('edited old file') } : f) }) })
  await act(async () => { await expect(ctx.hook.result.current.save()).rejects.toThrow('旧路径的 YAML 也已修改') })
  expect(fetchJson).not.toHaveBeenCalled()
})

it('switches to an existing destination while routing its edited YAML only to DB', async () => {
  const ctx = setup(), edited = { ...ctx.spec, title: 'Existing target' }
  act(() => { move(ctx, btoa(stringify(edited)), true); const v = ctx.session.state.versions.draft
    const target = v.files.find(f => f.path === destination)!
    updateVersion(ctx.session, 'draft', { snapshot: { ...v.snapshot!, versionFiles: [...v.snapshot!.versionFiles, target] } }) })
  await act(async () => { expect(await ctx.hook.result.current.save()).toEqual({ dbSaved: true, commit: B }) })
  const calls = vi.mocked(fetchJson).mock.calls
  const git = JSON.parse(await blobText(calls[1][1]!.body as Blob))
  expect(git.changes).toContainEqual({ path: destination, operation: 'replace', contentBase64: ctx.baseline, mode: '100755' })
  expect(git.changes.some((change: { path: string }) => change.path === ctx.path)).toBe(false)
  expect(JSON.parse(await blobText(calls[0][1]!.body as Blob)).spec).toEqual(edited)
})
it('rejects a mismatched destination workflow identity before writing either store', async () => {
  const ctx = setup(); act(() => move(ctx, btoa(stringify({ ...ctx.spec, id: 'different' }))))
  await act(async () => { await expect(ctx.hook.result.current.save()).rejects.toThrow('身份与当前画布不一致') })
  expect(fetchJson).not.toHaveBeenCalled()
})
it('reports an unknown DB outcome without sending the Git move', async () => {
  const ctx = setup(); act(() => move(ctx, btoa(stringify({ ...ctx.spec, title: 'File' }))))
  vi.mocked(fetchJson).mockRejectedValueOnce(new Error('connection lost'))
  await act(async () => { await expect(ctx.hook.result.current.save()).rejects.toThrow('数据库保存失败或结果未知') })
  expect(vi.mocked(fetchJson).mock.calls.filter(([, init]) => init?.method === 'PUT')).toHaveLength(0)
})
it('does not overwrite a changed DB Pack identity and stops before Git on mismatch', async () => {
  const ctx = setup(); act(() => move(ctx, btoa(stringify({ ...ctx.spec, title: 'File' }))))
  vi.mocked(fetchJson).mockResolvedValueOnce({ ok: true, saved: true, workflowId: ctx.id, packId: 'other-pack' })
  await act(async () => { await expect(ctx.hook.result.current.save()).rejects.toThrow('数据库已报告保存，但返回的 Pack 身份与预期不一致') })
  const calls = vi.mocked(fetchJson).mock.calls
  expect(JSON.parse(await blobText(calls[0][1]!.body as Blob))).not.toHaveProperty('packId')
  expect(calls.filter(([, init]) => init?.method === 'PUT')).toHaveLength(0)
  expect(ctx.session.state.versions.draft.files.find(f => f.path === destination)?.contentBase64).toBe(btoa(stringify({ ...ctx.spec, title: 'File' })))
})

function renameTransport(ctx: ReturnType<typeof setup>) {
  vi.mocked(fetchJson).mockImplementation(async (url, init) => {
    const input = JSON.parse(await blobText(init!.body as Blob))
    if (url === '/api/auto-heal/save') {
      mocks.read.mockResolvedValue(structuredClone(input.spec))
      return { ok: true, saved: true, workflowId: ctx.id, packId: input.packId }
    }
    return { branch: `saved/${ctx.id}`, commit: B, packId: 'renamed', version: 1, requestId: input.requestId }
  })
}
it('renames Pack identity in DB then Git while preserving the full candidate inventory', async () => {
  const ctx = setup(); renameTransport(ctx)
  act(() => { move(ctx, ctx.baseline, false, 'renamed'); const v = ctx.session.state.versions.draft
    updateVersion(ctx.session, 'draft', { files: [...v.files.map(f => f.path === 'scripts/test.sh' ? { ...f, contentBase64: btoa('new') } : f),
      { path: 'assets/image.bin', contentBase64: btoa('\u0000\u00ff'), mode: '100644' }] }) })
  await act(async () => { expect(await ctx.hook.result.current.save()).toEqual({ dbSaved: true, commit: B }) })
  const calls = vi.mocked(fetchJson).mock.calls
  expect(calls.map(([url]) => url.includes('auto-heal') ? 'DB' : 'Git')).toEqual(['DB', 'Git'])
  expect(JSON.parse(await blobText(calls[0][1]!.body as Blob))).toMatchObject({ workflowId: ctx.id, packId: 'renamed', spec: ctx.spec })
  const changes = JSON.parse(await blobText(calls[1][1]!.body as Blob)).changes
  expect(changes).toContainEqual({ path: destination, operation: 'create', contentBase64: ctx.baseline, mode: '100755' })
  expect(changes).toContainEqual({ path: 'assets/image.bin', operation: 'create', contentBase64: btoa('\u0000\u00ff'), mode: '100644' })
  expect(changes).toContainEqual({ path: 'scripts/test.sh', operation: 'replace', contentBase64: btoa('new'), mode: '100644' })
  expect(ctx.session.state.versions.draft.snapshot?.packId).toBe('renamed')
  expect(ctx.session.state.versions.draft.snapshot?.versionFiles.map(f => f.path)).toEqual(expect.arrayContaining(['workflow.pack.yaml', destination, 'assets/image.bin', 'scripts/test.sh']))
})
it('renames identity only, without moving the selected workflow', async () => {
  const ctx = setup(); renameTransport(ctx)
  act(() => { const v = ctx.session.state.versions.draft
    updateVersion(ctx.session, 'draft', { files: v.files.map(f => f.path === 'workflow.pack.yaml'
      ? { ...f, contentBase64: btoa(stringify({ id: 'renamed', workflows: [ctx.id] })) } : f) }) })
  await act(async () => { expect(await ctx.hook.result.current.save()).toEqual({ dbSaved: true, commit: B }) })
  const calls = vi.mocked(fetchJson).mock.calls
  expect(JSON.parse(await blobText(calls[0][1]!.body as Blob)).packId).toBe('renamed')
  expect(JSON.parse(await blobText(calls[1][1]!.body as Blob)).changes.map((c: { path: string }) => c.path)).toEqual(['workflow.pack.yaml'])
})
it('combines identity and path rename with a DB-only YAML edit', async () => {
  const ctx = setup(), edited = { ...ctx.spec, title: 'Edited YAML' }; renameTransport(ctx)
  act(() => move(ctx, btoa(stringify(edited)), false, 'renamed'))
  await act(async () => { expect(await ctx.hook.result.current.save()).toEqual({ dbSaved: true, commit: B }) })
  const calls = vi.mocked(fetchJson).mock.calls
  expect(JSON.parse(await blobText(calls[0][1]!.body as Blob))).toMatchObject({ packId: 'renamed', spec: edited })
  expect(JSON.parse(await blobText(calls[1][1]!.body as Blob)).changes).toContainEqual({ path: destination, operation: 'create', contentBase64: ctx.baseline, mode: '100755' })
  expect(useEditorStore.getState().spec).toEqual(edited)
})
it('locks the rename after an unknown DB result; Git was not started', async () => {
  const ctx = setup(); act(() => move(ctx, ctx.baseline, false, 'renamed'))
  vi.mocked(fetchJson).mockRejectedValueOnce(new Error('lost DB response'))
  await act(async () => { await expect(ctx.hook.result.current.save()).rejects.toThrow(/数据库已发送，结果未知；Git 保存未发起/) })
  expect(fetchJson).toHaveBeenCalledOnce()
  await act(async () => { await expect(ctx.hook.result.current.save()).rejects.toThrow('不能自动重试') })
  expect(fetchJson).toHaveBeenCalledOnce()
  expect(ctx.session.state.versions.current.blocked).toBe(true); expect(ctx.session.state.versions.draft.blocked).toBe(true)
})
it('locks when DB returns another Pack identity; Git was not started', async () => {
  const ctx = setup(); act(() => move(ctx, ctx.baseline, false, 'renamed'))
  vi.mocked(fetchJson).mockResolvedValueOnce({ ok: true, saved: true, workflowId: ctx.id, packId: 'wrong' })
  await act(async () => { await expect(ctx.hook.result.current.save()).rejects.toThrow(/服务报告已保存 Pack ID wrong.*Git 保存未发起/) })
  expect(fetchJson).toHaveBeenCalledOnce()
  await act(async () => { await expect(ctx.hook.result.current.save()).rejects.toThrow('不能自动重试') })
  expect(fetchJson).toHaveBeenCalledOnce()
})
it('reports confirmed DB identity and uncertain Git outcome and prevents another save', async () => {
  const ctx = setup(); renameTransport(ctx); act(() => move(ctx, ctx.baseline, false, 'renamed'))
  const original = vi.mocked(fetchJson).getMockImplementation()!
  vi.mocked(fetchJson).mockImplementation((url, init) => init?.method === 'PUT' ? Promise.reject(new Error('lost Git response')) : original(url, init))
  await act(async () => { await expect(ctx.hook.result.current.save()).rejects.toThrow(/数据库已确认 Pack ID renamed，文档已核对；Git 保存已发起，结果未确认/) })
  expect(fetchJson).toHaveBeenCalledTimes(2)
  await act(async () => { await expect(ctx.hook.result.current.save()).rejects.toThrow('不能自动重试') })
  expect(fetchJson).toHaveBeenCalledTimes(2)
  expect(ctx.session.state.versions.draft.files.find(f => f.path === destination)?.contentBase64).toBe(ctx.baseline)
  expect(ctx.session.state.reconciliation).not.toBeNull()
})
it('does not lock when preflight fails before DB request', async () => {
  const ctx = setup(); renameTransport(ctx); act(() => move(ctx, ctx.baseline, false, 'renamed'))
  mocks.read.mockRejectedValueOnce(new Error('offline'))
  await act(async () => { await expect(ctx.hook.result.current.save()).rejects.toThrow('无法核对数据库草稿') })
  expect(fetchJson).not.toHaveBeenCalled()
  await act(async () => { expect(await ctx.hook.result.current.save()).toEqual({ dbSaved: true, commit: B }) })
})
it('reports a confirmed DB rename and unstarted Git when the draft replacement is cancelled', async () => {
  const ctx = setup(); renameTransport(ctx)
  act(() => {
    const draft = ctx.session.state.versions.draft
    publish(ctx.session, { ...ctx.session.state, version: 'current', versions: { ...ctx.session.state.versions,
      current: { ...draft, snapshot: { ...draft.snapshot!, baseRef: `origin/wf/${ctx.id}`, draftHead: B }, files: structuredClone(draft.files) } } })
    const current = ctx.session.state.versions.current
    updateVersion(ctx.session, 'current', { files: current.files.map(f => f.path === 'workflow.pack.yaml'
      ? { ...f, contentBase64: btoa(stringify({ id: 'renamed', workflows: [ctx.id] })) } : f) })
  })
  vi.spyOn(window, 'confirm').mockReturnValue(false)
  await act(async () => { await expect(ctx.hook.result.current.save()).rejects.toThrow(/数据库已确认 Pack ID renamed，文档已核对；Git 保存未发起/) })
  expect(fetchJson).toHaveBeenCalledOnce()
  await act(async () => { await expect(ctx.hook.result.current.save()).rejects.toThrow('不能自动重试') })
})
it('reports the DB acknowledgement but does not start Git when DB reread fails', async () => {
  const ctx = setup(); renameTransport(ctx); act(() => move(ctx, ctx.baseline, false, 'renamed'))
  const original = vi.mocked(fetchJson).getMockImplementation()!
  vi.mocked(fetchJson).mockImplementation(async (url, init) => {
    const result = await original(url, init)
    if (url === '/api/auto-heal/save') mocks.read.mockRejectedValueOnce(new Error('reread offline'))
    return result
  })
  await act(async () => { await expect(ctx.hook.result.current.save()).rejects.toThrow(/服务报告已保存 Pack ID renamed，文档尚未核对；Git 保存未发起/) })
  expect(fetchJson).toHaveBeenCalledOnce()
})
it('preserves the guard even if a local version is unblocked or the selected version changes', async () => {
  const ctx = setup(); act(() => move(ctx, ctx.baseline, false, 'renamed'))
  vi.mocked(fetchJson).mockRejectedValueOnce(new Error('lost'))
  await act(async () => { await expect(ctx.hook.result.current.save()).rejects.toThrow('结果未知') })
  act(() => { updateVersion(ctx.session, 'current', { blocked: false }); publish(ctx.session, { ...ctx.session.state, version: 'current' }) })
  await act(async () => { await expect(ctx.hook.result.current.save()).rejects.toThrow('不能自动重试') })
  expect(fetchJson).toHaveBeenCalledOnce()
})
it('keeps other workflows and unchanged binary resources in the renamed complete Pack', async () => {
  const ctx = setup(); renameTransport(ctx)
  const other = { path: 'workflows/other.yaml', contentBase64: btoa('id: other\ntitle: Other\n'), mode: '100644' as const }
  const asset = { path: 'assets/keep.dat', contentBase64: btoa('\u0000\u00ff'), mode: '100755' as const }
  act(() => {
    const v = ctx.session.state.versions.draft
    const base = [...v.files.map(f => f.path === 'workflow.pack.yaml' ? { ...f, contentBase64: btoa(stringify({ id: 'pack', workflows: [ctx.id, 'other'] })) } : f), other, asset]
    updateVersion(ctx.session, 'draft', { snapshot: { ...v.snapshot!, versionFiles: base }, files: structuredClone(base) })
    const updated = ctx.session.state.versions.draft
    updateVersion(ctx.session, 'draft', { files: updated.files.map(f => f.path === 'workflow.pack.yaml'
      ? { ...f, contentBase64: btoa(stringify({ id: 'renamed', workflows: [ctx.id, 'other'] })) } : f) })
  })
  await act(async () => { expect(await ctx.hook.result.current.save()).toEqual({ dbSaved: true, commit: B }) })
  const saved = ctx.session.state.versions.draft.snapshot!.versionFiles
  expect(saved).toContainEqual(other); expect(saved).toContainEqual(asset)
  const git = JSON.parse(await blobText(vi.mocked(fetchJson).mock.calls[1][1]!.body as Blob))
  expect(git.changes.map((c: { path: string }) => c.path)).toEqual(['workflow.pack.yaml'])
})
