import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { WorkflowSpec } from '@avernet/clawweb-shared/web/types'
import { fetchJson } from '@avernet/clawweb-shared/web/api/client'
import { useEditorStore } from '../store'
import { saveVerifiedWorkflowDocument, saveWorkflowCanvasDocument } from '../workflowDocumentSave'

const mocks = vi.hoisted(() => ({ read: vi.fn(), validateSnapshot: vi.fn() }))
vi.mock('../../api/workflowDocument', async importOriginal => ({ ...(await importOriginal<typeof import('../../api/workflowDocument')>()), readWorkflowDocument: mocks.read }))
vi.mock('@avernet/clawweb-shared/web/api/client', () => ({ fetchJson: vi.fn(), api: { workflows: { get: mocks.read, getHistory: vi.fn(() => { throw new Error('History must not be read when saving') }) } } }))
// The source reader imports this validator, but DB-only saves must never invoke
// it or access any Pack session functions (unmocked exports fail closed).
vi.mock('../packVersionSession', () => ({ validateSnapshot: mocks.validateSnapshot }))
const state = () => useEditorStore.getState()
let workflowId = ''
let workflowSequence = 0
const document = (): WorkflowSpec => ({ id: workflowId, title: 'Original', version: '2.7', custom: { zero: 0, empty: '', no: false, nested: { value: null } }, nodes: [] })
const missing = () => Object.assign(new Error('Not found'), { status: 404, body: JSON.stringify({ error: 'Not Found', message: `Workflow "${workflowId}" not found` }) })
function deferred<T>() { let resolve!: (value: T) => void; let reject!: (reason?: unknown) => void; const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no }); return { promise, resolve, reject } }
const blobText = (blob: Blob) => new Promise<string>(resolve => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result)); reader.readAsText(blob) })
let stored: WorkflowSpec | null
const token = 'a'.repeat(64), savedToken = 'b'.repeat(64)
const draft = (spec: WorkflowSpec = stored!, draftToken = token, packId: string | null = 'pack') => ({ workflowId, packId, specJson: JSON.stringify(spec), draftToken })
const casCalls = () => vi.mocked(fetchJson).mock.calls.filter(([url]) => url === `/api/workflows/${workflowId}/editor-draft`)
const bodyAt = async (index: number) => JSON.parse(await blobText(vi.mocked(fetchJson).mock.calls[index][1]!.body as Blob))
afterEach(() => {
  try {
    expect(mocks.validateSnapshot).not.toHaveBeenCalled()
    const isPackRequest = ([url]: [unknown, ...unknown[]]) => /\/(?:pack-version|unified-save)(?:\?|$)/.test(String(url))
    expect(vi.mocked(fetch).mock.calls.filter(isPackRequest)).toEqual([])
    expect(vi.mocked(fetchJson).mock.calls.filter(isPackRequest)).toEqual([])
  } finally { vi.unstubAllGlobals() }
})
beforeEach(() => {
  sessionStorage.clear()
  workflowId = `document-save-fixture-${++workflowSequence}`
  // Exercise real Response handling while retaining the existing call spies.
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => new Response(JSON.stringify(await fetchJson(url, init)))))
  vi.resetAllMocks(); state().reset({ force: true })
  mocks.validateSnapshot.mockImplementation(() => { throw new Error('DB saving must not inspect Pack snapshots') })
  stored = document(); state().loadSpec(stored); useEditorStore.setState({ _databasePackId: 'pack' })
  mocks.read.mockImplementation(async () => { if (stored === null) throw missing(); return structuredClone(stored) })
  vi.mocked(fetchJson).mockImplementation(async (url, init) => {
    if (url === `/api/workflows/${workflowId}/editor-draft`) {
      if (init?.method === 'GET') {
        if (stored === null) throw Object.assign(new Error('not found'), { status: 404 })
        return draft() as never
      }
      expect(init?.method).toBe('PUT')
      const body = JSON.parse(await blobText(init.body as Blob))
      expect(body.expectedDraftToken).toBe(token)
      stored = body.spec
      return draft(stored!, savedToken) as never
    }
    expect(url).toBe('/api/auto-heal/editor-save-first-draft'); expect(init?.method).toBe('POST')
    const body = JSON.parse(await blobText(init!.body as Blob)); stored = body.spec
    return { ok: true, saved: true, workflowId: body.workflowId, packId: body.packId ?? 'pack' } as never
  })
})

describe('DB-only verified canvas saves', () => {
  it('persists only applied memory fields as a CSRF Blob without any Git options', async () => {
    state().updateSpecField('title', 'Applied')
    expect(fetchJson).not.toHaveBeenCalled()
    expect(await saveWorkflowCanvasDocument(workflowId)).toEqual({ dbSaved: true, commit: null })
    const calls = casCalls(); expect(calls).toHaveLength(2)
    expect(calls.map(([, init]) => init?.method)).toEqual(['GET', 'PUT'])
    for (const [, init] of calls) { expect(init?.headers).toMatchObject({ 'X-Pack-Version-CSRF': '1' }); expect(init?.cache).toBe('no-store') }
    expect(calls[1][1]!.headers).toMatchObject({ 'Content-Type': 'application/json' })
    expect(calls[1][1]!.body).toBeInstanceOf(Blob)
    expect(await bodyAt(1)).toEqual({ expectedDraftToken: token, spec: { ...document(), title: 'Applied' } })
    expect(mocks.read).not.toHaveBeenCalled()
    expect(state()._databaseSpec).toEqual(stored); expect(state().isDirty).toBe(false)
  })
  it.each([false, true])('rejects first-draft save without trusted pack identity (edited=%s)', async edited => {
    stored = null; state().loadSpec(document(), null, 9)
    if (edited) state().updateSpecField('title', 'Applied active edit')
    const before = state()
    await expect(saveWorkflowCanvasDocument(workflowId)).rejects.toThrow('缺少可信')
    expect(fetchJson).not.toHaveBeenCalled(); expect(state()).toBe(before)
  })
  it('retains first-draft authored content without attempting a write', async () => {
    const content = 'authored content'
    stored = null; state().loadSpec({ ...document(), content }, null, 9)
    state().updateSpecField('title', 'Edited')
    await expect(saveWorkflowCanvasDocument(workflowId)).rejects.toThrow('缺少可信')
    expect(state().spec?.content).toBe(content); expect(state().isDirty).toBe(true)
    expect(fetchJson).not.toHaveBeenCalled()
  })
  it('checks the DB before returning a clean no-op', async () => {
    expect(await saveWorkflowCanvasDocument(workflowId)).toEqual({ dbSaved: false, commit: null })
    expect(mocks.read).not.toHaveBeenCalled(); expect(casCalls().map(([, init]) => init?.method)).toEqual(['GET'])
  })
  it('settles canvas dirtiness when the explicitly applied candidate already matches the DB draft', async () => {
    state().loadSpec({ ...document(), title: 'Different canvas source' }, document()); useEditorStore.setState({ _databasePackId: 'pack' })
    state().importYaml(document())
    expect(state().isDirty).toBe(true)
    expect(await saveWorkflowCanvasDocument(workflowId)).toEqual({ dbSaved: false, commit: null })
    expect(state().isDirty).toBe(false); expect(state()._savedSpec).toEqual(document())
    expect(mocks.read).not.toHaveBeenCalled(); expect(casCalls().map(([, init]) => init?.method)).toEqual(['GET'])
  })
  it.each(['created', 'changed', 'deleted'] as const)('rejects remote draft %s since load without POST', async kind => {
    if (kind === 'created') { state().loadSpec(document(), null, 9); stored = { ...document(), title: 'Another actor created this' } }
    else if (kind === 'changed') stored = { ...document(), title: 'Remote edit' }
    else stored = null
    state().updateSpecField('title', 'Local edit')
    const before = state()
    await expect(saveWorkflowCanvasDocument(workflowId)).rejects.toThrow(kind === 'created' ? '缺少可信' : '数据库草稿')
    expect(casCalls().map(([, init]) => init?.method)).toEqual(kind === 'created' ? [] : ['GET']); expect(state()).toBe(before)
  })
  it.each([new Error('offline'), Object.assign(new Error('bad'), { status: 404, body: '{"error":"Not Found"}' })])('does not confuse read failure with absence', async cause => {
    stored = null; state().loadSpec(document(), null, 9)
    mocks.read.mockRejectedValueOnce(cause)
    await expect(saveWorkflowCanvasDocument(workflowId)).rejects.toThrow('缺少可信')
    expect(fetchJson).not.toHaveBeenCalled(); expect(state()._databaseSpec).toBeNull()
  })
  it.each(['pending', 'validation', 'wrong-id', 'new'] as const)('blocks invalid save state before reading: %s', async kind => {
    if (kind === 'pending') state().setPendingEdit('yaml', '')
    if (kind === 'validation') state().setValidationErrors([{ type: 'missing-field', message: 'invalid' }])
    if (kind === 'new') { state().reset({ force: true }); state().createNew(workflowId, 'New') }
    await expect(saveWorkflowCanvasDocument(kind === 'wrong-id' ? 'other' : workflowId)).rejects.toThrow()
    expect(fetchJson).not.toHaveBeenCalled(); expect(mocks.read).not.toHaveBeenCalled()
  })
  it('locks concurrent clicks within a session/workflow and releases after failure', async () => {
    state().updateSpecField('title', 'Save')
    const beforeRead = deferred<unknown>(); vi.mocked(fetchJson).mockReturnValueOnce(beforeRead.promise as never)
    const saving = saveWorkflowCanvasDocument(workflowId)
    await expect(saveWorkflowCanvasDocument(workflowId)).rejects.toThrow('正在保存')
    beforeRead.reject(new Error('offline')); await expect(saving).rejects.toThrow('无法核对')
    expect(await saveWorkflowCanvasDocument(workflowId)).toEqual({ dbSaved: true, commit: null })
    expect(casCalls().map(([, init]) => init?.method)).toEqual(['GET', 'GET', 'PUT'])
  })
  it('cancels PUT if the user edits during preflight', async () => {
    const beforeRead = deferred<unknown>(); vi.mocked(fetchJson).mockReturnValueOnce(beforeRead.promise as never)
    state().updateSpecField('title', 'Save'); const saving = saveWorkflowCanvasDocument(workflowId)
    state().updateSpecField('title', 'Newer'); beforeRead.resolve(draft(document()))
    await expect(saving).rejects.toThrow('会话已变化')
    expect(casCalls().map(([, init]) => init?.method)).toEqual(['GET']); expect(state().spec!.title).toBe('Newer')
  })
  it.each(['edit', 'switch', 'same-id-new-session'] as const)('preserves editor changes made after PUT: %s', async kind => {
    const posted = deferred<void>(), response = deferred<unknown>()
    vi.mocked(fetchJson).mockResolvedValueOnce(draft() as never).mockImplementationOnce(async (_url, init) => { stored = JSON.parse(await blobText(init!.body as Blob)).spec; posted.resolve(); return response.promise as never })
    state().updateSpecField('title', 'Save'); const saving = saveWorkflowCanvasDocument(workflowId); await posted.promise
    if (kind === 'edit') { state().updateSpecField('title', 'Newer'); state().setPendingEdit('yaml', 'Keep unapplied') }
    else { state().reset({ force: true }); state().loadSpec({ ...document(), id: kind === 'switch' ? 'other' : workflowId, title: 'New session' }) }
    const latest = state()
    response.resolve(draft(stored!, savedToken))
    expect(await saving).toEqual({ dbSaved: true, commit: null })
    if (kind === 'edit') {
      expect(state().spec!.title).toBe('Newer'); expect(state().pendingEdits).toEqual({ yaml: 'Keep unapplied' })
      expect(state()._databaseSpec!.title).toBe('Save'); expect(state().isDirty).toBe(true)
    } else expect(state()).toBe(latest)
  })
  it.each(['field-loss', 'unknown-put', 'bad-receipt', 'cas-read-failure'] as const)('keeps the local draft and baselines after %s', async kind => {
    state().updateSpecField('title', 'Save'); const before = state()
    if (kind === 'cas-read-failure') vi.mocked(fetchJson).mockRejectedValueOnce(new Error('offline'))
    else {
      vi.mocked(fetchJson).mockResolvedValueOnce(draft() as never)
      if (kind === 'unknown-put') vi.mocked(fetchJson).mockRejectedValueOnce(new Error('offline'))
      if (kind === 'bad-receipt') vi.mocked(fetchJson).mockResolvedValueOnce({ ok: true, saved: false } as never)
      if (kind === 'field-loss') vi.mocked(fetchJson).mockResolvedValueOnce(draft({ ...document(), title: 'Save', custom: undefined }, savedToken) as never)
    }
    await expect(saveWorkflowCanvasDocument(workflowId)).rejects.toThrow(/数据库/)
    expect(state()).toBe(before)
    expect(casCalls().map(([, init]) => init?.method)).toEqual(kind === 'cas-read-failure' ? ['GET'] : ['GET', 'PUT'])
  })
  it('retains first-draft edits without attempting an absent-only write', async () => {
    stored = null; state().loadSpec(document(), null, 9); state().updateSpecField('title', 'Save')
    mocks.read.mockRejectedValueOnce(missing()).mockRejectedValueOnce(new Error('offline'))
    const before = state(); await expect(saveWorkflowCanvasDocument(workflowId)).rejects.toThrow('缺少可信')
    expect(state()).toBe(before); expect(fetchJson).not.toHaveBeenCalled()
  })
  it('blocks an explicit identity write before any DB or Pack/Git request', async () => {
    const onStart = vi.fn(), onResponse = vi.fn()
    await expect(saveVerifiedWorkflowDocument(workflowId, document(), document(), () => true,
      { packId: 'renamed', identityWrite: { newPackId: 'renamed', onStart, onResponse } })).rejects.toThrow('暂不支持')
    expect(onStart).not.toHaveBeenCalled(); expect(onResponse).not.toHaveBeenCalled(); expect(fetchJson).not.toHaveBeenCalled()
  })
})

it.each(['request', 'baseline'] as const)('rejects mismatched %s identity before DB calls', async kind => {
  const wrong = { ...document(), id: 'other' }
  await expect(saveVerifiedWorkflowDocument(workflowId, kind === 'baseline' ? wrong : document(), kind === 'request' ? wrong : document(), () => true)).rejects.toThrow('身份不匹配')
  expect(mocks.read).not.toHaveBeenCalled(); expect(fetchJson).not.toHaveBeenCalled()
})
it('checks an explicit expected pack identity before writing and does not mislabel it as a Git fault', async () => {
  await expect(saveVerifiedWorkflowDocument(workflowId, document(), document(), () => true, { packId: 'different', expectedRawPackId: 'pack' })).rejects.toThrow('身份与预期不一致')
  expect(casCalls().map(([, init]) => init?.method)).toEqual(['GET'])
})
it.each(['wrong workflow', 'invalid pack', 'null'] as const)('rejects unverifiable DB acknowledgement %s', async kind => {
  const receipt = kind === 'null' ? null : kind === 'wrong workflow'
    ? { ...draft(document(), savedToken), workflowId: 'other' }
    : { ...draft(document(), savedToken), packId: '' }
  vi.mocked(fetchJson).mockResolvedValueOnce(draft(document()) as never).mockResolvedValueOnce(receipt as never)
  await expect(saveVerifiedWorkflowDocument(workflowId, document(), document(), () => true, { expectedRawPackId: 'pack' })).rejects.toThrow('响应')
  expect(casCalls().map(([, init]) => init?.method)).toEqual(['GET', 'PUT'])
})
it('freezes caller-owned request and baseline while the preflight is in flight', async () => {
  const baseline = document(), request = { ...document(), title: 'Requested' }, reading = deferred<unknown>()
  vi.mocked(fetchJson).mockReturnValueOnce(reading.promise as never)
  const saving = saveVerifiedWorkflowDocument(workflowId, baseline, request, () => true, { expectedRawPackId: 'pack' })
  baseline.title = 'Caller mutated baseline'; request.title = 'Caller mutated request'; reading.resolve(draft(document()))
  expect((await saving).title).toBe('Requested')
})

it('preserves historical null version through load, edit and the verified DB save', async () => {
  stored = { ...document(), version: null }
  state().loadSpec(stored); useEditorStore.setState({ _databasePackId: 'pack' })
  state().updateSpecField('title', 'Edited legacy draft')
  expect(state().actionError).toBeNull()
  expect(await saveWorkflowCanvasDocument(workflowId)).toEqual({ dbSaved: true, commit: null })
  const submitted = (await bodyAt(1)).spec
  expect(submitted).toEqual({ ...document(), version: null, title: 'Edited legacy draft' })
  expect(state().spec!.version).toBeNull(); expect(state()._databaseSpec!.version).toBeNull()
  expect(state().isDirty).toBe(false)
})
it('does not silently accept normalization of a legacy null version on save', async () => {
  stored = { ...document(), version: null }
  state().loadSpec(stored); useEditorStore.setState({ _databasePackId: 'pack' })
  state().updateSpecField('title', 'Edited legacy draft')
  const before = state()
  vi.mocked(fetchJson).mockResolvedValueOnce(draft(stored) as never).mockResolvedValueOnce(draft({ ...document(), title: 'Edited legacy draft', version: '1' }, savedToken) as never)
  await expect(saveWorkflowCanvasDocument(workflowId)).rejects.toThrow('保存内容与请求不同')
  expect(state()).toBe(before); expect(state().spec!.version).toBeNull()
})
