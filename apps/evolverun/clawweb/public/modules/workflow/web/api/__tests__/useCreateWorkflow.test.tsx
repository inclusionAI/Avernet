import { act, renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, expect, it, vi } from 'vitest'
import type { ReactNode } from 'react'
import { stringify } from 'yaml'
import { fetchJson } from '@avernet/clawweb-shared/web/api/client'
import { useCreateWorkflow } from '../useCreateWorkflow'

vi.mock('@avernet/clawweb-shared/web/api/client', () => ({ fetchJson: vi.fn() }))
afterEach(() => vi.resetAllMocks())
it('the workspace creation hook uses atomic createOnly, preserves facade, and never calls the legacy workflow save', async () => {
  const spec = { id: 'workspace-new', title: 'New', version: '1', nodes: [] }
  const facade = { command: 'new-command', remark: 'Details' }
  const commit = 'a'.repeat(40)
  vi.mocked(fetchJson).mockImplementation(async (url, init) => {
    if (url === '/api/auto-heal/save') {
      const body = await new Promise<string>(resolve => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result)); reader.readAsText(init!.body as Blob) })
      const request = JSON.parse(body)
      expect(request).toMatchObject({ createOnly: true, expectedSavedHead: null, spec: { ...spec, facade } })
      expect(request.requestId).toMatch(/^[\da-f-]{36}$/)
      return { branch: `saved/${spec.id}`, commit, requestId: request.requestId, version: 1, packId: spec.id }
    }
    expect(url).toBe(`/api/workflows/${spec.id}/pack-version?version=draft`)
    return { workflowId: spec.id, packId: spec.id, baseRef: `origin/saved/${spec.id}`, baseCommit: commit, draftHead: commit, canWrite: true,
      versionFiles: [{ path: 'workflow.pack.yaml', contentBase64: btoa(stringify({ id: spec.id, workflows: [spec.id] })), mode: '100644' },
        { path: `workflows/${spec.id}.yaml`, contentBase64: btoa(stringify({ ...spec, facade })), mode: '100644' }] }
  })
  const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } })
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>
  const { result } = renderHook(() => useCreateWorkflow(), { wrapper })
  await act(async () => { await result.current.mutateAsync({ workflowId: spec.id, spec, facade, botOwnerId: 'creator' }) })
  expect(fetchJson).toHaveBeenCalledTimes(2)
  await waitFor(() => expect(result.current.isSuccess).toBe(true))
  client.clear()
})
it('resends the modal original createOnly body and UUID after an undelivered POST and absent operation', async () => {
  const { reconcileNewWorkflow } = await import('../../editor/newWorkflowSave')
  const { sessionFor } = await import('../../editor/packVersionSession')
  const spec = { id: 'modal-not-delivered', title: 'Original', version: '1', nodes: [] }
  const commit = 'a'.repeat(40)
  const text = (blob: Blob): Promise<string> => new Promise(resolve => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result)); reader.readAsText(blob) })
  vi.mocked(fetchJson).mockRejectedValueOnce(new TypeError('not delivered'))
  const client = new QueryClient({ defaultOptions: { mutations: { retry: 2, retryDelay: 0 } } })
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>
  const { result, unmount } = renderHook(() => useCreateWorkflow(), { wrapper })
  await act(async () => { await expect(result.current.mutateAsync({ workflowId: spec.id, spec })).rejects.toThrow('尚未确认') })
  expect(fetchJson).toHaveBeenCalledTimes(1)
  const requestId = sessionFor(spec.id).state.creation!.input.requestId
  vi.mocked(fetchJson).mockRejectedValueOnce({ status: 404, body: '{"code":"OPERATION_NOT_FOUND"}' })
  await expect(reconcileNewWorkflow(spec.id)).rejects.toThrow()
  vi.mocked(fetchJson).mockResolvedValueOnce({ branch: `saved/${spec.id}`, commit, requestId, packId: spec.id, version: 1 })
    .mockResolvedValueOnce({ workflowId: spec.id, packId: spec.id, baseRef: `origin/saved/${spec.id}`, baseCommit: commit, draftHead: commit, canWrite: true,
      versionFiles: [{ path: 'workflow.pack.yaml', contentBase64: btoa(stringify({ id: spec.id, workflows: [spec.id] })), mode: '100644' },
        { path: `workflows/${spec.id}.yaml`, contentBase64: btoa(stringify(spec)), mode: '100644' }] })
  await act(async () => { await result.current.mutateAsync({ workflowId: spec.id, spec }) })
  const creates = vi.mocked(fetchJson).mock.calls.filter(([url]) => url === '/api/auto-heal/save')
  expect(creates).toHaveLength(2)
  expect(await text(creates[0][1]!.body as Blob)).toBe(await text(creates[1][1]!.body as Blob))
  expect(JSON.parse(await text(creates[1][1]!.body as Blob))).toMatchObject({ createOnly: true, requestId })
  unmount(); client.clear()
})
it('does not mark a changed modal form saved when its original immutable request completes on retry', async () => {
  const { sessionFor } = await import('../../editor/packVersionSession')
  const spec = { id: 'modal-later-edits', title: 'Original', version: '1', nodes: [] }, commit = 'a'.repeat(40)
  vi.mocked(fetchJson).mockRejectedValueOnce(new TypeError('lost'))
  const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } })
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>
  const { result, unmount } = renderHook(useCreateWorkflow, { wrapper })
  await act(async () => { await expect(result.current.mutateAsync({ workflowId: spec.id, spec })).rejects.toThrow() })
  const requestId = sessionFor(spec.id).state.creation!.input.requestId
  vi.mocked(fetchJson).mockResolvedValueOnce({ branch: `saved/${spec.id}`, commit, requestId, packId: spec.id, version: 1 })
    .mockResolvedValueOnce({ workflowId: spec.id, packId: spec.id, baseRef: `origin/saved/${spec.id}`, baseCommit: commit, draftHead: commit,
      versionFiles: [{ path: 'workflow.pack.yaml', contentBase64: btoa(stringify({ id: spec.id, workflows: [spec.id] })), mode: '100644' },
        { path: `workflows/${spec.id}.yaml`, contentBase64: btoa(stringify(spec)), mode: '100644' }] })
  await act(async () => { await expect(result.current.mutateAsync({ workflowId: spec.id, spec: { ...spec, title: 'Later edit' } })).rejects.toThrow('当前表单内容已变化') })
  expect(sessionFor(spec.id).state.creation!.input.spec.title).toBe('Original')
  unmount(); client.clear()
})
