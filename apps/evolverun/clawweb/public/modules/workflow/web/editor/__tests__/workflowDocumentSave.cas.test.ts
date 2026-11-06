import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { stringify } from 'yaml'
import type { WorkflowSpec } from '@avernet/clawweb-shared/web/types'
import { fetchJson } from '@avernet/clawweb-shared/web/api/client'
import { saveVerifiedWorkflowDocument, saveWorkflowCanvasDocument } from '../workflowDocumentSave'
import { useEditorStore } from '../store'

vi.mock('@avernet/clawweb-shared/web/api/client', () => ({ fetchJson: vi.fn(), api: { workflows: {} } }))
let workflowId = ''
let workflowSequence = 0
let base: WorkflowSpec
const token = 'a'.repeat(64), nextToken = 'b'.repeat(64)
const envelope = (spec: unknown = base): { workflowId: string; packId: string | null; specJson: string; draftToken: string } => ({ workflowId, packId: 'pack', specJson: JSON.stringify(spec), draftToken: token })
const state = () => useEditorStore.getState()
const blobText = (blob: Blob) => new Promise<string>(resolve => { const r = new FileReader(); r.onload = () => resolve(String(r.result)); r.readAsText(blob) })
let row: ReturnType<typeof envelope>
afterEach(() => vi.unstubAllGlobals())
beforeEach(() => {
  sessionStorage.clear()
  workflowId = `document-cas-fixture-${++workflowSequence}`
  base = { id: workflowId, title: 'Original', version: '1', nodes: [] }
  // Exercise real Response handling while retaining the existing call spies.
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => new Response(JSON.stringify(await fetchJson(url, init)))))
  vi.resetAllMocks(); state().reset({ force: true }); state().loadSpec(base); useEditorStore.setState({ _databasePackId: 'pack' }); row = envelope()
  vi.mocked(fetchJson).mockImplementation(async (url, init) => {
    expect(url).toBe(`/api/workflows/${workflowId}/editor-draft`)
    if (init?.method === 'GET') return row as never
    const body = JSON.parse(await blobText(init!.body as Blob))
    return { ...row, specJson: JSON.stringify(body.spec), draftToken: nextToken } as never
  })
})
describe('existing database draft CAS', () => {
  it('reads once and writes with the matching token, CSRF and no-store', async () => {
    state().updateSpecField('title', 'Edited')
    expect(await saveWorkflowCanvasDocument(workflowId)).toEqual({ dbSaved: true, commit: null })
    const calls = vi.mocked(fetchJson).mock.calls
    expect(calls).toHaveLength(2)
    for (const [, init] of calls) { expect(init?.headers).toMatchObject({ 'X-Pack-Version-CSRF': '1' }); expect(init?.cache).toBe('no-store') }
    expect(calls[0][1]?.method).toBe('GET'); expect(calls[1][1]?.method).toBe('PUT')
    expect(JSON.parse(await blobText(calls[1][1]!.body as Blob))).toEqual({ expectedDraftToken: token, spec: { ...base, title: 'Edited' } })
    expect(state().isDirty).toBe(false)
  })
  it('rejects an already changed baseline without writing', async () => {
    row = envelope({ ...base, title: 'Remote' }); state().updateSpecField('title', 'Local')
    await expect(saveWorkflowCanvasDocument(workflowId)).rejects.toThrow('数据库草稿')
    expect(fetchJson).toHaveBeenCalledTimes(1); expect(state().isDirty).toBe(true)
  })
  it.each([403, 404, 405, 503])('fails closed when GET returns %s', async status => {
    vi.mocked(fetchJson).mockRejectedValueOnce(Object.assign(new Error('unavailable'), { status }))
    state().updateSpecField('title', 'Local')
    await expect(saveWorkflowCanvasDocument(workflowId)).rejects.toThrow(); expect(fetchJson).toHaveBeenCalledTimes(1)
    expect(state().isDirty).toBe(true)
  })
  it('rejects a mismatched expected pack before PUT', async () => {
    await expect(saveVerifiedWorkflowDocument(workflowId, base, { ...base, title: 'Local' }, () => true, { packId: 'other', expectedRawPackId: 'pack' })).rejects.toThrow('身份')
    expect(fetchJson).toHaveBeenCalledTimes(1)
  })
  it('uses workflowId as effective Pack identity for a null row without binding it', async () => {
    row = { ...row, packId: null }
    const requested = { ...base, title: 'Local' }
    expect(await saveVerifiedWorkflowDocument(workflowId, base, requested, () => true, { packId: workflowId, expectedRawPackId: null })).toEqual(requested)
    expect(vi.mocked(fetchJson).mock.calls.map(([, init]) => init?.method)).toEqual(['GET', 'PUT'])
    expect(JSON.parse(await blobText(vi.mocked(fetchJson).mock.calls[1][1]!.body as Blob))).toEqual({ expectedDraftToken: token, spec: requested })
  })
  it('rejects a different Pack for a null row before PUT', async () => {
    row = { ...row, packId: null }
    await expect(saveVerifiedWorkflowDocument(workflowId, base, { ...base, title: 'Local' }, () => true, { packId: 'other', expectedRawPackId: null })).rejects.toThrow('身份')
    expect(fetchJson).toHaveBeenCalledTimes(1)
  })
  it('rejects a receipt that silently binds a null Pack row even to workflowId', async () => {
    row = { ...row, packId: null }
    vi.mocked(fetchJson).mockResolvedValueOnce(row as never).mockImplementationOnce(async (_url, init) => {
      const spec = JSON.parse(await blobText(init!.body as Blob)).spec
      return { ...row, packId: workflowId, specJson: JSON.stringify(spec), draftToken: nextToken } as never
    })
    await expect(saveVerifiedWorkflowDocument(workflowId, base, { ...base, title: 'Local' }, () => true, { packId: workflowId, expectedRawPackId: null })).rejects.toThrow('身份')
    expect(fetchJson).toHaveBeenCalledTimes(2)
  })
  it('blocks pack renaming before PUT without starting identity reconciliation', async () => {
    const onStart = vi.fn(), onResponse = vi.fn()
    state().updateSpecField('title', 'Local'); const before = state()
    await expect(saveVerifiedWorkflowDocument(workflowId, base, state().spec!, () => true,
      { packId: 'renamed', identityWrite: { newPackId: 'renamed', onStart, onResponse } })).rejects.toThrow('暂不支持')
    expect(onStart).not.toHaveBeenCalled(); expect(onResponse).not.toHaveBeenCalled()
    expect(fetchJson).not.toHaveBeenCalled(); expect(state()).toBe(before)
  })
  it('also blocks identity writes for a first draft before reaching AutoHeal', async () => {
    const onStart = vi.fn(), onResponse = vi.fn()
    await expect(saveVerifiedWorkflowDocument(workflowId, null, base, () => true, { expectedActiveDeployNumber: 9,
      identityWrite: { newPackId: 'renamed', onStart, onResponse } })).rejects.toThrow('暂不支持')
    expect(onStart).not.toHaveBeenCalled(); expect(onResponse).not.toHaveBeenCalled(); expect(fetchJson).not.toHaveBeenCalled()
  })
  it('rejects a session change while GET is pending', async () => {
    let resolve!: (value: unknown) => void
    vi.mocked(fetchJson).mockReturnValueOnce(new Promise(r => { resolve = r }) as never)
    state().updateSpecField('title', 'Local'); const saving = saveWorkflowCanvasDocument(workflowId)
    state().reset({ force: true }); state().loadSpec({ ...base, title: 'New session' }); const latest = state()
    resolve(row); await expect(saving).rejects.toThrow('会话已变化')
    expect(fetchJson).toHaveBeenCalledTimes(1); expect(state()).toBe(latest)
  })
  it.each(['workflowId', 'packId', 'specJson', 'draftToken'] as const)('fails closed on invalid GET %s', async field => {
    row = { ...row, [field]: field === 'workflowId' ? 'other' : field === 'specJson' ? '{' : field === 'draftToken' ? 'A'.repeat(64) : 17 } as typeof row
    state().updateSpecField('title', 'Local')
    await expect(saveWorkflowCanvasDocument(workflowId)).rejects.toThrow(); expect(fetchJson).toHaveBeenCalledTimes(1)
  })
  it('does not retry a PUT with unknown outcome', async () => {
    state().updateSpecField('title', 'Local'); const before = state()
    vi.mocked(fetchJson).mockResolvedValueOnce(row as never).mockRejectedValueOnce(new Error('connection lost'))
    await expect(saveWorkflowCanvasDocument(workflowId)).rejects.toThrow('不要盲目重试')
    expect(fetchJson).toHaveBeenCalledTimes(2); expect(state()).toBe(before)
  })
  it('retains draft on CAS conflict without retry', async () => {
    state().updateSpecField('title', 'Local')
    vi.mocked(fetchJson).mockResolvedValueOnce(row as never)
    vi.stubGlobal('fetch', vi.fn(async () => new Response('{\"code\":\"DRAFT_CONFLICT\"}', { status: 409 })))
    const before = state(); await expect(saveWorkflowCanvasDocument(workflowId)).rejects.toThrow(/冲突|变化/)
    expect(fetchJson).toHaveBeenCalledTimes(1); expect(state()).toBe(before)
  })
  it.each(['workflowId', 'packId', 'specJson', 'draftToken'] as const)('rejects a forged %s receipt and keeps draft', async field => {
    state().updateSpecField('title', 'Local')
    vi.mocked(fetchJson).mockResolvedValueOnce(row as never).mockImplementationOnce(async (_url, init) => {
      const spec = JSON.parse(await blobText(init!.body as Blob)).spec
      return { ...row, specJson: JSON.stringify(spec), draftToken: nextToken, [field]: field === 'specJson' ? JSON.stringify(base) : field === 'draftToken' ? 'INVALID' : 'other' } as never
    })
    const before = state(); await expect(saveWorkflowCanvasDocument(workflowId)).rejects.toThrow()
    expect(state()).toBe(before); expect(fetchJson).toHaveBeenCalledTimes(2)
  })
  it('rejects an unchanged token when the persisted JSON changed', async () => {
    state().updateSpecField('title', 'Local')
    vi.mocked(fetchJson).mockResolvedValueOnce(row as never).mockImplementationOnce(async (_url, init) => {
      const spec = JSON.parse(await blobText(init!.body as Blob)).spec
      return { ...row, specJson: JSON.stringify(spec) } as never
    })
    const before = state(); await expect(saveWorkflowCanvasDocument(workflowId)).rejects.toThrow()
    expect(state()).toBe(before)
  })
  it('does not apply a receipt to a new session', async () => {
    let resolve!: (value: unknown) => void; let started!: () => void
    const waiting = new Promise<unknown>(r => { resolve = r }), begun = new Promise<void>(r => { started = r })
    vi.mocked(fetchJson).mockResolvedValueOnce(row as never).mockImplementationOnce(async (_url, init) => { const spec = JSON.parse(await blobText(init!.body as Blob)).spec; started(); await waiting; return { ...row, specJson: JSON.stringify(spec), draftToken: nextToken } as never })
    state().updateSpecField('title', 'Local'); const saving = saveWorkflowCanvasDocument(workflowId); await begun
    state().reset({ force: true }); state().loadSpec({ ...base, title: 'New session' }); const after = state()
    resolve(null); await saving; expect(state()).toBe(after)
  })
  it.each(['wrapper', 'user-content'] as const)('preserves baseline semantics for %s', async kind => {
    const doc = kind === 'user-content' ? { ...base, content: 'authored text' } : base
    state().loadSpec(doc); useEditorStore.setState({ _databasePackId: 'pack' }); state().updateSpecField('title', 'Local')
    row = envelope(kind === 'wrapper' ? { content: stringify(base) } : doc)
    await saveWorkflowCanvasDocument(workflowId)
    const body = JSON.parse(await blobText(vi.mocked(fetchJson).mock.calls[1][1]!.body as Blob))
    expect(body.spec).toEqual({ ...doc, title: 'Local' })
  })
  it('fails closed when a full projection is indistinguishable from authored content', async () => {
    row = envelope({ ...base, content: stringify(base) }); state().updateSpecField('title', 'Local')
    const before = state()
    await expect(saveWorkflowCanvasDocument(workflowId)).rejects.toThrow('数据库草稿自读取后已变化')
    expect(fetchJson).toHaveBeenCalledTimes(1); expect(state()).toBe(before)
  })
  it('preserves a real content field even when it looks exactly like a projection', async () => {
    const doc = { ...base, content: stringify(base) }
    state().loadSpec(doc); useEditorStore.setState({ _databasePackId: 'pack' }); state().updateSpecField('title', 'Local'); row = envelope(doc)
    await saveWorkflowCanvasDocument(workflowId)
    expect(JSON.parse(await blobText(vi.mocked(fetchJson).mock.calls[1][1]!.body as Blob)).spec.content).toBe(doc.content)
  })
  it('rejects ambiguous incomplete wrappers with extra fields', async () => {
    row = envelope({ content: stringify(base), custom: 'do not discard' })
    await expect(saveVerifiedWorkflowDocument(workflowId, base, { ...base, title: 'Local' }, () => true, { expectedRawPackId: 'pack' })).rejects.toThrow()
    expect(fetchJson).toHaveBeenCalledTimes(1)
  })
})

it.each([['0.1', '0.10000000000000001'], ['0', '-0']])('does not accept a lossy numeric receipt %s → %s as verified', async (submitted, returned) => {
  const amount = Number(submitted)
  const request = { ...base, amount }
  row = envelope(request)
  state().reset({ force: true }); state().loadSpec(request); useEditorStore.setState({ _databasePackId: 'pack' }); state().updateSpecField('title', 'Edited')
  vi.mocked(fetchJson).mockResolvedValueOnce(row as never).mockImplementationOnce(async (_url, init) => {
    const spec = JSON.parse(await blobText(init!.body as Blob)).spec
    return { ...row, specJson: JSON.stringify(spec).replace(`"amount":${submitted}`, `"amount":${returned}`), draftToken: nextToken } as never
  })
  await expect(saveWorkflowCanvasDocument(workflowId)).rejects.toMatchObject({ name: 'WorkflowDocumentAcknowledgedError' })
  expect(state().isDirty).toBe(true)
})
