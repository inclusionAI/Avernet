import { MutationCache, QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, renderHook, waitFor } from '@testing-library/react'
import type { PropsWithChildren } from 'react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { autoHealSaveIsCurrent, bindAutoHealDeployToSave, AutoHealWriteBlockedError, bindEditorDeploymentWriteEpoch, isAutoHealWriteNotSent, isEditorDeploymentNotSent, useAutoHealSave, useAutoHealDeploy } from '../autoHealWrites'
import { readEditorWorkflowSource } from '../editorWorkflowSource'
import { abandonEditorWorkflowWrite, assertEditorWorkflowReadable, beginEditorWorkflowWrite, editorWorkflowReadIsCurrent, editorWorkflowWriteState, finishEditorWorkflowWrite } from '../../editor/workflowWriteGuard'

let client: QueryClient
const fetchMock = vi.fn<typeof fetch>()
let sequence = 0
const id = () => `autoheal-guard-${++sequence}`
const save = (workflowId: string) => ({ workflowId, spec: { id: workflowId, title: 'Test', version: '1', nodes: [] }, note: 'auto-heal save' })
const deploy = (workflowId: string) => ({ workflowId, packId: 'pack', autoRun: false })
const receipt = (workflowId: string) => ({ ok: true, saved: true, workflowId, packId: 'pack', branch: `wf/${workflowId}`, commit: 'a'.repeat(40) })
const response = (body: unknown) => new Response(JSON.stringify(body))
function wrapper({ children }: PropsWithChildren) { return <QueryClientProvider client={client}>{children}</QueryClientProvider> }
beforeEach(() => { client = new QueryClient({ defaultOptions: { mutations: { retry: false } } }); fetchMock.mockReset(); vi.stubGlobal('fetch', fetchMock) })
afterEach(() => { cleanup(); client.clear(); vi.restoreAllMocks(); vi.unstubAllGlobals() })

it.each(['save', 'deploy'] as const)('rejects Editor pending and unknown at the actual %s mutation boundary', async operation => {
  const workflowId = id(), owner = beginEditorWorkflowWrite(workflowId)
  const { result } = renderHook(() => ({ save: useAutoHealSave(), deploy: useAutoHealDeploy() }), { wrapper })
  const send = () => operation === 'save' ? result.current.save.mutateAsync(save(workflowId)) : result.current.deploy.mutateAsync(deploy(workflowId))
  await act(async () => { await expect(send()).rejects.toThrow(/正在进行/) })
  abandonEditorWorkflowWrite(workflowId, owner)
  await act(async () => { await expect(send()).rejects.toThrow(/人工核对/) })
  expect(fetchMock).not.toHaveBeenCalled()
})

it('installs a marker before sending, blocks duplicates and stale reads, and clears on valid success after unmount', async () => {
  const workflowId = id(), epoch = assertEditorWorkflowReadable(workflowId)
  let resolve!: (value: Response) => void
  fetchMock.mockImplementation(() => {
    expect(editorWorkflowWriteState(workflowId).phase).toBe('pending')
    expect(sessionStorage.getItem(`avernet.editor.pending-workflow-write.v1:${encodeURIComponent(workflowId)}`)).not.toBeNull()
    return new Promise(r => { resolve = r })
  })
  const hook = renderHook(() => ({ save: useAutoHealSave(), deploy: useAutoHealDeploy() }), { wrapper })
  let first!: Promise<unknown>
  act(() => { first = hook.result.current.save.mutateAsync(save(workflowId)) })
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1))
  expect(editorWorkflowReadIsCurrent(workflowId, epoch)).toBe(false)
  expect(() => assertEditorWorkflowReadable(workflowId)).toThrow(/正在进行/)
  await act(async () => {
    await expect(hook.result.current.save.mutateAsync(save(workflowId))).rejects.toThrow(/正在进行/)
    await expect(hook.result.current.deploy.mutateAsync(deploy(workflowId))).rejects.toThrow(/正在进行/)
  })
  hook.unmount()
  await act(async () => { resolve(response(receipt(workflowId))); await expect(first).resolves.toEqual(receipt(workflowId)) })
  expect(editorWorkflowWriteState(workflowId).phase).toBe('clear')
  expect(() => assertEditorWorkflowReadable(workflowId)).not.toThrow()
  expect(editorWorkflowReadIsCurrent(workflowId, epoch)).toBe(false)
})

it.each(['network', 'cancel', 'bad receipt', 'wrong pack', 'http'])('keeps %s outcome unknown and prevents reads and further sends', async kind => {
  const workflowId = id()
  fetchMock.mockImplementationOnce(async () => {
    if (kind === 'network') throw new TypeError('connection lost')
    if (kind === 'cancel') throw new DOMException('cancelled', 'AbortError')
    if (kind === 'http') return new Response('unavailable', { status: 503 })
    return response(kind === 'wrong pack' ? { ...receipt(workflowId), packId: 'wrong-pack' } : { ...receipt(workflowId), workflowId: 'other' })
  })
  const { result } = renderHook(() => ({ save: useAutoHealSave(), deploy: useAutoHealDeploy() }), { wrapper })
  await act(async () => {
    await expect(result.current.save.mutateAsync(kind === 'wrong pack' ? { ...save(workflowId), packId: 'pack' } : save(workflowId))).rejects.toThrow(kind === 'bad receipt' || kind === 'wrong pack' ? /保存结果无效.*结果未知/ : /.*/)
  })
  expect(editorWorkflowWriteState(workflowId).phase).toBe('unknown')
  expect(() => assertEditorWorkflowReadable(workflowId)).toThrow(/人工核对/)
  await expect(readEditorWorkflowSource(workflowId)).rejects.toThrow(/人工核对/)
  await act(async () => { await expect(result.current.deploy.mutateAsync(deploy(workflowId))).rejects.toThrow(/人工核对/) })
  expect(fetchMock).toHaveBeenCalledTimes(1)
})

it('fails closed without sending if marker creation fails', async () => {
  const workflowId = id()
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('quota') })
  const { result } = renderHook(useAutoHealSave, { wrapper })
  await act(async () => { await expect(result.current.mutateAsync(save(workflowId))).rejects.toThrow(/未发送/) })
  expect(fetchMock).not.toHaveBeenCalled()
  expect(editorWorkflowWriteState(workflowId).phase).toBe('unknown')
})

it('keeps the guard unknown if a successful acknowledgement cannot clear its marker', async () => {
  const workflowId = id()
  fetchMock.mockResolvedValueOnce(response(receipt(workflowId)))
  vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(() => { throw new Error('storage failure') })
  const { result } = renderHook(useAutoHealSave, { wrapper })
  await act(async () => { await expect(result.current.mutateAsync(save(workflowId))).rejects.toThrow(/标记/) })
  expect(fetchMock).toHaveBeenCalledTimes(1)
  expect(editorWorkflowWriteState(workflowId).phase).toBe('unknown')
})

it('does not block different workflows or the separate editor-deploy route', async () => {
  const blocked = id(), other = id(), owner = beginEditorWorkflowWrite(blocked)
  fetchMock.mockImplementation(async () => response({ ok: true, deployed: true }))
  const { result } = renderHook(() => ({ auto: useAutoHealDeploy(), editor: useAutoHealDeploy({ editorGuard: true }) }), { wrapper })
  await act(async () => {
    await result.current.auto.mutateAsync(deploy(other))
    const input = deploy(other)
    bindEditorDeploymentWriteEpoch(input, assertEditorWorkflowReadable(other))
    await result.current.editor.mutateAsync(input)
  })
  expect(fetchMock).toHaveBeenCalledTimes(2)
  finishEditorWorkflowWrite(blocked, owner)
})


it('blocks an unbound editor deployment and authenticates the exact locally rejected input', async () => {
  const input = deploy(id())
  const { result } = renderHook(() => useAutoHealDeploy({ editorGuard: true }), { wrapper })
  let failure: unknown
  await act(async () => { try { await result.current.mutateAsync(input) } catch (error) { failure = error } })
  expect(failure).toBeInstanceOf(AutoHealWriteBlockedError)
  expect(isEditorDeploymentNotSent(failure, input)).toBe(true)
  expect(isEditorDeploymentNotSent(failure, { ...input })).toBe(false)
  expect(isEditorDeploymentNotSent(new AutoHealWriteBlockedError(new Error('spoof')), input)).toBe(false)
  expect(fetchMock).not.toHaveBeenCalled()
})

it('rechecks the bound editor epoch after a real asynchronous MutationCache.onMutate delay', async () => {
  const workflowId = id(), input = deploy(workflowId)
  let release!: () => void
  const delayed = new Promise<void>(resolve => { release = resolve })
  const onMutate = vi.fn(() => delayed)
  client = new QueryClient({ mutationCache: new MutationCache({ onMutate }), defaultOptions: { mutations: { retry: false } } })
  bindEditorDeploymentWriteEpoch(input, assertEditorWorkflowReadable(workflowId))
  const { result } = renderHook(() => useAutoHealDeploy({ editorGuard: true }), { wrapper })
  let failure: unknown
  let pending!: Promise<void>
  act(() => { pending = result.current.mutateAsync(input).then(() => {}, error => { failure = error }) })
  await waitFor(() => expect(onMutate).toHaveBeenCalledOnce())
  const owner = beginEditorWorkflowWrite(workflowId)
  finishEditorWorkflowWrite(workflowId, owner)
  await act(async () => { release(); await pending })
  expect(failure).toBeInstanceOf(AutoHealWriteBlockedError)
  expect(isEditorDeploymentNotSent(failure, input)).toBe(true)
  expect(fetchMock).not.toHaveBeenCalled()
})

it('never classifies a fetch-thrown lookalike as an unsent editor deployment', async () => {
  const input = deploy(id())
  bindEditorDeploymentWriteEpoch(input, assertEditorWorkflowReadable(input.workflowId))
  const spoof = new AutoHealWriteBlockedError(new Error('network failure'))
  fetchMock.mockRejectedValueOnce(spoof)
  const { result } = renderHook(() => useAutoHealDeploy({ editorGuard: true }), { wrapper })
  await act(async () => { await expect(result.current.mutateAsync(input)).rejects.toBe(spoof) })
  expect(isEditorDeploymentNotSent(spoof, input)).toBe(false)
  expect(fetchMock).toHaveBeenCalledTimes(1)
})

it('does not call mutation success or invalidate queries on an invalid save receipt', async () => {
  const workflowId = id(), onSuccess = vi.fn()
  const invalidate = vi.spyOn(client, 'invalidateQueries')
  fetchMock.mockResolvedValueOnce(response({ ...receipt(workflowId), saved: false }))
  const { result } = renderHook(useAutoHealSave, { wrapper })
  await act(async () => { await expect(result.current.mutateAsync(save(workflowId), { onSuccess })).rejects.toThrow(/结果未知/) })
  expect(onSuccess).not.toHaveBeenCalled()
  expect(invalidate).not.toHaveBeenCalled()
  expect(editorWorkflowWriteState(workflowId).phase).toBe('unknown')
})

it.each(['save', 'deploy'] as const)('rejects queued AutoHeal %s if a write starts and finishes during MutationCache.onMutate', async operation => {
  const workflowId = id(), input = operation === 'save' ? save(workflowId) : deploy(workflowId)
  let release!: () => void
  const delayed = new Promise<void>(resolve => { release = resolve })
  const onMutate = vi.fn(() => delayed)
  client = new QueryClient({ mutationCache: new MutationCache({ onMutate }) })
  const { result } = renderHook(() => ({ save: useAutoHealSave(), deploy: useAutoHealDeploy() }), { wrapper })
  let failure: unknown, pending!: Promise<void>
  act(() => {
    const promise = operation === 'save' ? result.current.save.mutateAsync(input as ReturnType<typeof save>) : result.current.deploy.mutateAsync(input as ReturnType<typeof deploy>)
    pending = promise.then(() => {}, error => { failure = error })
  })
  await waitFor(() => expect(onMutate).toHaveBeenCalledOnce())
  const owner = beginEditorWorkflowWrite(workflowId)
  finishEditorWorkflowWrite(workflowId, owner)
  await act(async () => { release(); await pending })
  expect(failure).toBeInstanceOf(AutoHealWriteBlockedError)
  expect(isAutoHealWriteNotSent(failure, input)).toBe(true)
  expect(fetchMock).not.toHaveBeenCalled()
  expect(editorWorkflowWriteState(workflowId).phase).toBe('clear')
})

it('keeps independent submission epochs when the same input object is queued twice', async () => {
  const workflowId = id(), input = deploy(workflowId)
  const releases: Array<() => void> = []
  const onMutate = vi.fn(() => new Promise<void>(resolve => releases.push(resolve)))
  client = new QueryClient({ mutationCache: new MutationCache({ onMutate }) })
  fetchMock.mockImplementation(async () => response({ ok: true, deployed: true }))
  const { result } = renderHook(useAutoHealDeploy, { wrapper })
  let first!: Promise<unknown>, second!: Promise<unknown>
  act(() => { first = result.current.mutateAsync(input).catch(error => error) })
  await waitFor(() => expect(releases).toHaveLength(1))
  const owner = beginEditorWorkflowWrite(workflowId); finishEditorWorkflowWrite(workflowId, owner)
  act(() => { second = result.current.mutateAsync(input) })
  await waitFor(() => expect(releases).toHaveLength(2))
  await act(async () => { releases[1](); await second; releases[0](); expect(await first).toBeInstanceOf(AutoHealWriteBlockedError) })
  expect(fetchMock).toHaveBeenCalledTimes(1)
})

it('clears its save marker only on a native 409 DRAFT_CONFLICT response', async () => {
  const workflowId = id(), input = save(workflowId)
  fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ error: 'Save could not be completed', code: 'DRAFT_CONFLICT' }), { status: 409 }))
  const { result } = renderHook(useAutoHealSave, { wrapper })
  await act(async () => { await expect(result.current.mutateAsync(input)).rejects.toMatchObject({ status: 409, body: expect.stringContaining('DRAFT_CONFLICT') }) })
  expect(editorWorkflowWriteState(workflowId).phase).toBe('clear')
  expect(sessionStorage.getItem(`avernet.editor.pending-workflow-write.v1:${encodeURIComponent(workflowId)}`)).toBeNull()
  fetchMock.mockResolvedValueOnce(response(receipt(workflowId)))
  await act(async () => { await expect(result.current.mutateAsync(input)).resolves.toEqual(receipt(workflowId)) })
})

it.each(['throw', 'fake response', 'body read failure', 'wrong status', 'other conflict', 'invalid body', 'oversized', 'create only'])('keeps %s save conflict unknown', async kind => {
  const workflowId = id(), input = { ...save(workflowId), ...(kind === 'create only' ? { createOnly: true } : {}) }
  const conflict = JSON.stringify({ code: 'DRAFT_CONFLICT' })
  fetchMock.mockImplementationOnce(async () => {
    if (kind === 'throw') throw Object.assign(new Error('spoof'), { status: 409, body: conflict })
    if (kind === 'fake response') return { ok: false, status: 409, statusText: '', text: async () => conflict } as Response
    if (kind === 'body read failure') return new Response(new ReadableStream({ start(controller) { controller.error(new Error('lost')) } }), { status: 409, statusText: conflict })
    return new Response(kind === 'other conflict' ? JSON.stringify({ code: 'WORKFLOW_ALREADY_EXISTS' }) : kind === 'invalid body' ? '{' : kind === 'oversized' ? JSON.stringify({ code: 'DRAFT_CONFLICT', padding: 'x'.repeat(9000) }) : conflict, { status: kind === 'wrong status' ? 503 : 409 })
  })
  const { result } = renderHook(useAutoHealSave, { wrapper })
  await act(async () => { await expect(result.current.mutateAsync(input)).rejects.toThrow() })
  expect(editorWorkflowWriteState(workflowId).phase).toBe('unknown')
  expect(() => assertEditorWorkflowReadable(workflowId)).toThrow(/人工核对/)
})

it('keeps a conflict unknown when clearing the marker fails', async () => {
  const workflowId = id()
  fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ code: 'DRAFT_CONFLICT' }), { status: 409 }))
  vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(() => { throw new Error('storage failure') })
  const { result } = renderHook(useAutoHealSave, { wrapper })
  await act(async () => { await expect(result.current.mutateAsync(save(workflowId))).rejects.toThrow(/标记/) })
  expect(editorWorkflowWriteState(workflowId).phase).toBe('unknown')
})

it('preserves public mutation callback input identity and original request JSON', async () => {
  const workflowId = id(), input = save(workflowId), onSuccess = vi.fn()
  fetchMock.mockResolvedValueOnce(response(receipt(workflowId)))
  const { result } = renderHook(useAutoHealSave, { wrapper })
  await act(async () => { await result.current.mutateAsync(input, { onSuccess }) })
  expect(onSuccess).toHaveBeenCalledOnce()
  expect(onSuccess.mock.calls[0][1]).toBe(input)
  const body = fetchMock.mock.calls[0][1]!.body as Blob
  const text = await new Promise<string>(resolve => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result)); reader.readAsText(body) })
  expect(JSON.parse(text)).toEqual(input)
})

it('saves normally when randomUUID is unavailable but getRandomValues works', async () => {
  const workflowId = id()
  vi.stubGlobal('crypto', { getRandomValues: crypto.getRandomValues.bind(crypto) })
  fetchMock.mockResolvedValueOnce(response(receipt(workflowId)))
  const { result } = renderHook(useAutoHealSave, { wrapper })
  await act(async () => { await expect(result.current.mutateAsync(save(workflowId))).resolves.toEqual(receipt(workflowId)) })
  expect(fetchMock).toHaveBeenCalledOnce()
  expect(editorWorkflowWriteState(workflowId).phase).toBe('clear')
})

it.each(['forged prototype', 'overridden status', 'overridden text'])('does not unlock on %s save response', async kind => {
  const workflowId = id(), conflict = JSON.stringify({ code: 'DRAFT_CONFLICT' })
  let fake: Response
  if (kind === 'forged prototype') {
    fake = Object.create(Response.prototype) as Response
    Object.defineProperties(fake, { ok: { value: false }, status: { value: 409 }, text: { value: async () => conflict } })
  } else if (kind === 'overridden status') {
    fake = new Response(conflict, { status: 503 })
    Object.defineProperty(fake, 'status', { value: 409 })
  } else {
    fake = new Response(JSON.stringify({ code: 'WORKFLOW_ALREADY_EXISTS' }), { status: 409 })
    Object.defineProperty(fake, 'text', { value: async () => conflict })
  }
  fetchMock.mockResolvedValueOnce(fake)
  const { result } = renderHook(useAutoHealSave, { wrapper })
  await act(async () => { await expect(result.current.mutateAsync(save(workflowId))).rejects.toThrow() })
  expect(editorWorkflowWriteState(workflowId).phase).toBe('unknown')
})

it('binds deployments only to the original acknowledged save receipt and its exact identity', async () => {
  const workflowId = id()
  fetchMock.mockResolvedValueOnce(response(receipt(workflowId)))
  const { result } = renderHook(() => ({ save: useAutoHealSave(), deploy: useAutoHealDeploy() }), { wrapper })
  let saved!: Awaited<ReturnType<typeof result.current.save.mutateAsync>>
  await act(async () => { saved = await result.current.save.mutateAsync(save(workflowId)) })
  expect(autoHealSaveIsCurrent(saved)).toBe(true)
  expect(autoHealSaveIsCurrent({ ...saved })).toBe(false)
  expect(() => bindAutoHealDeployToSave(deploy(workflowId), { ...saved })).toThrow(/重新核对/)
  expect(() => bindAutoHealDeployToSave(deploy('different'), saved)).toThrow(/重新核对/)
  const input = deploy(workflowId)
  bindAutoHealDeployToSave(input, saved)
  const owner = beginEditorWorkflowWrite(workflowId); finishEditorWorkflowWrite(workflowId, owner)
  expect(autoHealSaveIsCurrent(saved)).toBe(false)
  await act(async () => { await expect(result.current.deploy.mutateAsync(input)).rejects.toThrow(/写入状态.*变化/) })
  expect(fetchMock).toHaveBeenCalledTimes(1)
})

it.each([
  [401, { error: 'Unauthorized', message: 'Authentication required' }, false],
  [403, { error: 'Forbidden', message: 'No edit permission for this workflow' }, false],
  [403, { error: 'Origin not allowed', code: 'ORIGIN_NOT_ALLOWED' }, false],
  [403, { error: 'Request rejected', code: 'CSRF_REJECTED' }, false],
  [400, { error: 'Bad Request', message: 'workflowId and spec are required' }, false],
  [400, { error: 'Request Failed', message: 'Auto-heal request failed', code: 'INVALID_WORKFLOW' }, false],
  [403, { error: 'Request Failed', message: 'Auto-heal request failed', code: 'FORBIDDEN' }, false],
  [401, { error: 'Unauthorized', message: 'Authentication required' }, true],
  [403, { error: 'Origin not allowed', code: 'ORIGIN_NOT_ALLOWED' }, true],
])('releases legacy save guard for a verified pre-write HTTP %s rejection (createOnly=%s)', async (status, body, createOnly) => {
  const workflowId = id()
  fetchMock.mockResolvedValueOnce(new Response(JSON.stringify(body), { status }))
  const { result } = renderHook(useAutoHealSave, { wrapper })
  await act(async () => { await expect(result.current.mutateAsync({ ...save(workflowId), ...(createOnly ? { createOnly: true } : {}) })).rejects.toThrow() })
  expect(editorWorkflowWriteState(workflowId).phase).toBe('clear')
  expect(sessionStorage.getItem(`avernet.editor.pending-workflow-write.v1:${encodeURIComponent(workflowId)}`)).toBeNull()
  expect(fetchMock).toHaveBeenCalledTimes(1)
})

it.each([
  [403, { error: 'Forbidden' }, false],
  [400, { error: 'Request Failed', message: 'Auto-heal request failed', code: 'INVALID_WORKFLOW' }, true],
  [403, { error: 'Request Failed', message: 'Auto-heal request failed', code: 'FORBIDDEN', commit: 'a'.repeat(40) }, false],
  [503, { code: 'CREATE_DB_CONFIRMED_GIT_NOT_STARTED' }, true],
  [409, { code: 'DRAFT_CONFLICT' }, true],
  [400, { code: 'INVALID_REQUEST' }, false],
])('keeps legacy save guard unknown for unproven HTTP %s response (createOnly=%s)', async (status, body, createOnly) => {
  const workflowId = id()
  fetchMock.mockResolvedValueOnce(new Response(JSON.stringify(body), { status }))
  const { result } = renderHook(useAutoHealSave, { wrapper })
  await act(async () => { await expect(result.current.mutateAsync({ ...save(workflowId), ...(createOnly ? { createOnly: true } : {}) })).rejects.toThrow() })
  expect(editorWorkflowWriteState(workflowId).phase).toBe('unknown')
  expect(fetchMock).toHaveBeenCalledTimes(1)
})
