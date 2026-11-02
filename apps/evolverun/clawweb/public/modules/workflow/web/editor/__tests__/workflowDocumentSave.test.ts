import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { WorkflowSpec } from '@avernet/clawweb-shared/web/types'
import { fetchJson } from '@avernet/clawweb-shared/web/api/client'
import { useEditorStore } from '../store'
import { saveVerifiedWorkflowDocument, saveWorkflowCanvasDocument } from '../workflowDocumentSave'

const mocks = vi.hoisted(() => ({ read: vi.fn() }))
vi.mock('../../api/workflowDocument', () => ({ readWorkflowDocument: mocks.read }))
vi.mock('@avernet/clawweb-shared/web/api/client', () => ({ fetchJson: vi.fn(), api: { workflows: { getHistory: vi.fn(() => { throw new Error('History must not be read when saving') }) } } }))
vi.mock('../packVersionSession', () => { throw new Error('DB saving must not load or lock Pack state') })
const state = () => useEditorStore.getState()
const document = (): WorkflowSpec => ({ id: 'flow', title: 'Original', version: '2.7', custom: { zero: 0, empty: '', no: false, nested: { value: null } }, nodes: [] })
const missing = () => Object.assign(new Error('Not found'), { status: 404, body: JSON.stringify({ error: 'Not Found', message: 'Workflow "flow" not found' }) })
function deferred<T>() { let resolve!: (value: T) => void; let reject!: (reason?: unknown) => void; const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no }); return { promise, resolve, reject } }
const blobText = (blob: Blob) => new Promise<string>(resolve => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result)); reader.readAsText(blob) })
let stored: WorkflowSpec | null
beforeEach(() => {
  vi.resetAllMocks(); state().reset({ force: true })
  stored = document(); state().loadSpec(stored)
  mocks.read.mockImplementation(async () => { if (stored === null) throw missing(); return structuredClone(stored) })
  vi.mocked(fetchJson).mockImplementation(async (url, init) => {
    expect(url).toBe('/api/auto-heal/save'); expect(init?.method).toBe('POST')
    const body = JSON.parse(await blobText(init!.body as Blob)); stored = body.spec
    return { ok: true, saved: true, workflowId: body.workflowId, packId: body.packId ?? 'pack' } as never
  })
})

describe('DB-only verified canvas saves', () => {
  it('persists only applied memory fields as a CSRF Blob without any Git options', async () => {
    state().updateSpecField('title', 'Applied')
    expect(fetchJson).not.toHaveBeenCalled()
    expect(await saveWorkflowCanvasDocument('flow')).toEqual({ dbSaved: true, commit: null })
    const init = vi.mocked(fetchJson).mock.calls[0][1]!
    expect(init.headers).toEqual({ 'X-Pack-Version-CSRF': '1' }); expect(init.body).toBeInstanceOf(Blob)
    expect(JSON.parse(await blobText(init.body as Blob))).toEqual({ workflowId: 'flow', spec: { ...document(), title: 'Applied' } })
    expect(mocks.read).toHaveBeenCalledTimes(2)
    expect(state()._databaseSpec).toEqual(stored); expect(state().isDirty).toBe(false)
  })
  it('creates a DB draft from a null baseline, without createOnly or a Git dependency', async () => {
    stored = null; state().loadSpec(document(), null, 9)
    state().updateSpecField('title', 'Applied active edit')
    expect(await saveWorkflowCanvasDocument('flow')).toEqual({ dbSaved: true, commit: null })
    expect(state()._databaseSpec!.title).toBe('Applied active edit')
    expect(JSON.parse(await blobText(vi.mocked(fetchJson).mock.calls[0][1]!.body as Blob))).toEqual({ workflowId: 'flow', spec: stored, expectedDraftAbsent: true, expectedActiveDeployNumber: 9 })
  })
  it('keeps an absent-draft baseline and local edits when insertion races and returns 409', async () => {
    stored = null; state().loadSpec(document(), null, 9)
    state().updateSpecField('title', 'Local applied edit')
    const before = state()
    vi.mocked(fetchJson).mockRejectedValueOnce(Object.assign(new Error('DRAFT_ALREADY_EXISTS'), { status: 409, body: '{"code":"DRAFT_ALREADY_EXISTS"}' }))
    await expect(saveWorkflowCanvasDocument('flow')).rejects.toThrow('DRAFT_ALREADY_EXISTS')
    expect(JSON.parse(await blobText(vi.mocked(fetchJson).mock.calls[0][1]!.body as Blob)).expectedDraftAbsent).toBe(true)
    expect(state()).toBe(before); expect(state().isDirty).toBe(true); expect(state()._databaseSpec).toBeNull()
  })
  it('persists the initial active source on explicit save even before a canvas edit', async () => {
    stored = null; state().loadSpec(document(), null, 9)
    expect(state().isDirty).toBe(false)
    expect(await saveWorkflowCanvasDocument('flow')).toEqual({ dbSaved: true, commit: null })
  })
  it('checks the DB before returning a clean no-op', async () => {
    expect(await saveWorkflowCanvasDocument('flow')).toEqual({ dbSaved: false, commit: null })
    expect(mocks.read).toHaveBeenCalledOnce(); expect(fetchJson).not.toHaveBeenCalled()
  })
  it('settles canvas dirtiness when the explicitly applied candidate already matches the DB draft', async () => {
    state().loadSpec({ ...document(), title: 'Different canvas source' }, document())
    state().importYaml(document())
    expect(state().isDirty).toBe(true)
    expect(await saveWorkflowCanvasDocument('flow')).toEqual({ dbSaved: false, commit: null })
    expect(state().isDirty).toBe(false); expect(state()._savedSpec).toEqual(document())
    expect(mocks.read).toHaveBeenCalledOnce(); expect(fetchJson).not.toHaveBeenCalled()
  })
  it.each(['created', 'changed', 'deleted'] as const)('rejects remote draft %s since load without POST', async kind => {
    if (kind === 'created') { state().loadSpec(document(), null, 9); stored = { ...document(), title: 'Another actor created this' } }
    else if (kind === 'changed') stored = { ...document(), title: 'Remote edit' }
    else stored = null
    state().updateSpecField('title', 'Local edit')
    const before = state()
    await expect(saveWorkflowCanvasDocument('flow')).rejects.toThrow('数据库草稿')
    expect(fetchJson).not.toHaveBeenCalled(); expect(state()).toBe(before)
  })
  it.each([new Error('offline'), Object.assign(new Error('bad'), { status: 404, body: '{"error":"Not Found"}' })])('does not confuse read failure with absence', async cause => {
    stored = null; state().loadSpec(document(), null, 9)
    mocks.read.mockRejectedValueOnce(cause)
    await expect(saveWorkflowCanvasDocument('flow')).rejects.toThrow('无法核对')
    expect(fetchJson).not.toHaveBeenCalled(); expect(state()._databaseSpec).toBeNull()
  })
  it.each(['pending', 'validation', 'wrong-id', 'new'] as const)('blocks invalid save state before reading: %s', async kind => {
    if (kind === 'pending') state().setPendingEdit('yaml', '')
    if (kind === 'validation') state().setValidationErrors([{ type: 'missing-field', message: 'invalid' }])
    if (kind === 'new') { state().reset({ force: true }); state().createNew('flow', 'New') }
    await expect(saveWorkflowCanvasDocument(kind === 'wrong-id' ? 'other' : 'flow')).rejects.toThrow()
    expect(fetchJson).not.toHaveBeenCalled(); expect(mocks.read).not.toHaveBeenCalled()
  })
  it('locks concurrent clicks within a session/workflow and releases after failure', async () => {
    state().updateSpecField('title', 'Save')
    const beforeRead = deferred<WorkflowSpec>(); mocks.read.mockReturnValueOnce(beforeRead.promise)
    const saving = saveWorkflowCanvasDocument('flow')
    await expect(saveWorkflowCanvasDocument('flow')).rejects.toThrow('正在保存')
    beforeRead.reject(new Error('offline')); await expect(saving).rejects.toThrow('无法核对')
    expect(await saveWorkflowCanvasDocument('flow')).toEqual({ dbSaved: true, commit: null })
    expect(fetchJson).toHaveBeenCalledOnce()
  })
  it('cancels POST if the user edits during preflight', async () => {
    const beforeRead = deferred<WorkflowSpec>(); mocks.read.mockReturnValueOnce(beforeRead.promise)
    state().updateSpecField('title', 'Save'); const saving = saveWorkflowCanvasDocument('flow')
    state().updateSpecField('title', 'Newer'); beforeRead.resolve(document())
    await expect(saving).rejects.toThrow('会话已变化')
    expect(fetchJson).not.toHaveBeenCalled(); expect(state().spec!.title).toBe('Newer')
  })
  it.each(['edit', 'switch', 'same-id-new-session'] as const)('preserves editor changes made after POST: %s', async kind => {
    const posted = deferred<void>(), response = deferred<unknown>()
    vi.mocked(fetchJson).mockImplementationOnce(async (_url, init) => { stored = JSON.parse(await blobText(init!.body as Blob)).spec; posted.resolve(); return response.promise as never })
    state().updateSpecField('title', 'Save'); const saving = saveWorkflowCanvasDocument('flow'); await posted.promise
    if (kind === 'edit') { state().updateSpecField('title', 'Newer'); state().setPendingEdit('yaml', 'Keep unapplied') }
    else { state().reset({ force: true }); state().loadSpec({ ...document(), id: kind === 'switch' ? 'other' : 'flow', title: 'New session' }) }
    const latest = state()
    response.resolve({ ok: true, saved: true, workflowId: 'flow', packId: 'pack' })
    expect(await saving).toEqual({ dbSaved: true, commit: null })
    if (kind === 'edit') {
      expect(state().spec!.title).toBe('Newer'); expect(state().pendingEdits).toEqual({ yaml: 'Keep unapplied' })
      expect(state()._databaseSpec!.title).toBe('Save'); expect(state().isDirty).toBe(true)
    } else expect(state()).toBe(latest)
  })
  it.each(['field-loss', 'unknown-post', 'bad-receipt', 'readback-failure'] as const)('keeps the local draft and baselines after %s', async kind => {
    state().updateSpecField('title', 'Save'); const before = state()
    if (kind === 'unknown-post') vi.mocked(fetchJson).mockRejectedValueOnce(new Error('offline'))
    if (kind === 'bad-receipt') vi.mocked(fetchJson).mockResolvedValueOnce({ ok: true, saved: false } as never)
    if (kind === 'field-loss') mocks.read.mockResolvedValueOnce(document()).mockResolvedValueOnce({ ...document(), title: 'Save', custom: undefined })
    if (kind === 'readback-failure') mocks.read.mockResolvedValueOnce(document()).mockRejectedValueOnce(new Error('offline'))
    await expect(saveWorkflowCanvasDocument('flow')).rejects.toThrow(/数据库/)
    expect(state()).toBe(before)
  })
  it('supports an explicit identity write as data, without Pack/Git requests or state', async () => {
    const onStart = vi.fn(), onResponse = vi.fn()
    const result = await saveVerifiedWorkflowDocument('flow', document(), document(), () => true,
      { packId: 'renamed', identityWrite: { newPackId: 'renamed', onStart, onResponse } })
    expect(result).toEqual(document()); expect(onStart).toHaveBeenCalledOnce(); expect(onResponse).toHaveBeenCalledWith('renamed')
    expect(JSON.parse(await blobText(vi.mocked(fetchJson).mock.calls[0][1]!.body as Blob)).packId).toBe('renamed')
  })
})

it.each(['request', 'baseline'] as const)('rejects mismatched %s identity before DB calls', async kind => {
  const wrong = { ...document(), id: 'other' }
  await expect(saveVerifiedWorkflowDocument('flow', kind === 'baseline' ? wrong : document(), kind === 'request' ? wrong : document(), () => true)).rejects.toThrow('身份不匹配')
  expect(mocks.read).not.toHaveBeenCalled(); expect(fetchJson).not.toHaveBeenCalled()
})
it('checks an explicit expected identity and does not mislabel DB failure as a Git fault', async () => {
  const onResponse = vi.fn()
  vi.mocked(fetchJson).mockResolvedValueOnce({ ok: true, saved: true, workflowId: 'flow', packId: 'different' } as never)
  await expect(saveVerifiedWorkflowDocument('flow', document(), document(), () => true,
    { packId: 'renamed', identityWrite: { newPackId: 'renamed', onStart: vi.fn(), onResponse } })).rejects.toThrow('身份与预期不一致')
  expect(onResponse).toHaveBeenCalledWith('different'); expect(mocks.read).toHaveBeenCalledOnce()
})
it.each([{ ok: true, saved: true, workflowId: 'other', packId: 'pack' }, { ok: true, saved: true, workflowId: 'flow', packId: '' }, null])('rejects unverifiable DB acknowledgement %j', async receipt => {
  vi.mocked(fetchJson).mockResolvedValueOnce(receipt as never)
  await expect(saveVerifiedWorkflowDocument('flow', document(), document(), () => true)).rejects.toThrow('响应无法验证')
  expect(mocks.read).toHaveBeenCalledOnce()
})
it('freezes caller-owned request and baseline while the preflight is in flight', async () => {
  const baseline = document(), request = { ...document(), title: 'Requested' }, reading = deferred<WorkflowSpec>()
  mocks.read.mockReturnValueOnce(reading.promise)
  const saving = saveVerifiedWorkflowDocument('flow', baseline, request, () => true)
  baseline.title = 'Caller mutated baseline'; request.title = 'Caller mutated request'; reading.resolve(document())
  expect((await saving).title).toBe('Requested')
})

it('preserves historical null version through load, edit and the verified DB save', async () => {
  stored = { ...document(), version: null }
  state().loadSpec(stored)
  state().updateSpecField('title', 'Edited legacy draft')
  expect(state().actionError).toBeNull()
  expect(await saveWorkflowCanvasDocument('flow')).toEqual({ dbSaved: true, commit: null })
  const submitted = JSON.parse(await blobText(vi.mocked(fetchJson).mock.calls[0][1]!.body as Blob)).spec
  expect(submitted).toEqual({ ...document(), version: null, title: 'Edited legacy draft' })
  expect(state().spec!.version).toBeNull(); expect(state()._databaseSpec!.version).toBeNull()
  expect(state().isDirty).toBe(false)
})
it('does not silently accept normalization of a legacy null version on save', async () => {
  stored = { ...document(), version: null }
  state().loadSpec(stored)
  state().updateSpecField('title', 'Edited legacy draft')
  const before = state()
  mocks.read.mockResolvedValueOnce(stored).mockResolvedValueOnce({ ...document(), title: 'Edited legacy draft', version: '1' })
  await expect(saveWorkflowCanvasDocument('flow')).rejects.toThrow('保存内容与请求不同（字段：version）')
  expect(state()).toBe(before); expect(state().spec!.version).toBeNull()
})
