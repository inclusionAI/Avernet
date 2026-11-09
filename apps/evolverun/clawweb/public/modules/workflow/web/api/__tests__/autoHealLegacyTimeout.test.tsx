import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, renderHook } from '@testing-library/react'
import type { PropsWithChildren } from 'react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { useAutoHealSave, useAutoHealDeploy, isAutoHealWriteNotSent } from '../autoHealWrites'
import { AUTO_HEAL_LEGACY_TIMEOUT_MS } from '../autoHealLegacyTransport'
import { editorWorkflowWriteState } from '../../editor/workflowWriteGuard'

let id: string, sequence = 0, client: QueryClient
const mock = vi.fn<typeof fetch>()
const deferred = <T,>() => { let resolve!: (value: T) => void; const promise = new Promise<T>(r => { resolve = r }); return { promise, resolve } }
function wrapper({ children }: PropsWithChildren) { return <QueryClientProvider client={client}>{children}</QueryClientProvider> }
beforeEach(() => { id = `legacy-timeout-${++sequence}`; sessionStorage.clear(); mock.mockReset(); vi.stubGlobal('fetch', mock); client = new QueryClient({ defaultOptions: { mutations: { retry: 3, retryDelay: 0 } } }) })
afterEach(() => { vi.useRealTimers(); cleanup(); client.clear(); vi.unstubAllGlobals() })

it.each(['fetch', 'success-body', 'error-body'] as const)('save timeout during %s retains unknown lock even after a late response', async stage => {
  const pending = deferred<Response>()
  let streamController: ReadableStreamDefaultController<Uint8Array> | undefined
  if (stage === 'fetch') mock.mockImplementation(() => pending.promise)
  else {
    const stream = new ReadableStream<Uint8Array>({ start(controller) { streamController = controller } })
    mock.mockImplementation(async () => new Response(stream, { status: stage === 'success-body' ? 200 : 409 }))
  }
  const { result } = renderHook(useAutoHealSave, { wrapper })
  const input = { workflowId: id, spec: { id, title: 'fixed', nodes: [] } }
  vi.useFakeTimers()
  let attempt!: Promise<unknown>
  await act(async () => { attempt = result.current.mutateAsync(input); void attempt.catch(() => {}); await Promise.resolve(); await Promise.resolve() })
  expect(mock).toHaveBeenCalledTimes(1)
  await act(async () => { await vi.advanceTimersByTimeAsync(AUTO_HEAL_LEGACY_TIMEOUT_MS + 1) })
  await expect(attempt).rejects.toThrow('结果未知')
  expect(editorWorkflowWriteState(id).phase).toBe('unknown')
  expect(sessionStorage.getItem(`avernet.editor.pending-workflow-write.v1:${id}`)).not.toBeNull()
  expect(mock).toHaveBeenCalledTimes(1)
  await act(async () => {
    if (stage === 'fetch') pending.resolve(new Response(JSON.stringify({ ok: true, saved: true, workflowId: id, packId: 'pack' })))
    else { streamController!.enqueue(new TextEncoder().encode(stage === 'success-body' ? JSON.stringify({ ok: true, saved: true, workflowId: id, packId: 'pack' }) : '{"code":"DRAFT_CONFLICT"}')); streamController!.close() }
    await Promise.resolve(); await Promise.resolve()
  })
  expect(editorWorkflowWriteState(id).phase).toBe('unknown')
  expect(sessionStorage.getItem(`avernet.editor.pending-workflow-write.v1:${id}`)).not.toBeNull()
})

it.each(['fetch', 'success-body', 'error-body'] as const)('deploy timeout during %s cannot be classified as unsent or a retryable conflict', async stage => {
  const pending = deferred<Response>()
  let streamController: ReadableStreamDefaultController<Uint8Array> | undefined
  if (stage === 'fetch') mock.mockImplementation(() => pending.promise)
  else {
    const stream = new ReadableStream<Uint8Array>({ start(controller) { streamController = controller } })
    mock.mockImplementation(async () => new Response(stream, { status: stage === 'success-body' ? 200 : 409 }))
  }
  const { result } = renderHook(useAutoHealDeploy, { wrapper })
  const input = { workflowId: id, packId: 'pack', requestId: 'request_12345' }
  vi.useFakeTimers()
  let attempt!: Promise<unknown>, failure: unknown
  await act(async () => { attempt = result.current.mutateAsync(input); void attempt.catch(error => { failure = error }); await Promise.resolve(); await Promise.resolve() })
  await act(async () => { await vi.advanceTimersByTimeAsync(AUTO_HEAL_LEGACY_TIMEOUT_MS + 1) })
  await expect(attempt).rejects.toThrow('结果未知')
  expect(isAutoHealWriteNotSent(failure, input)).toBe(false)
  expect(mock).toHaveBeenCalledTimes(1)
  await act(async () => {
    if (stage === 'fetch') pending.resolve(new Response('{}'))
    else { streamController!.enqueue(new TextEncoder().encode('{"code":"DEPLOY_TREE_CONFLICT","requestId":"request_12345"}')); streamController!.close() }
    await Promise.resolve(); await Promise.resolve()
  })
  expect(mock).toHaveBeenCalledTimes(1)
})
