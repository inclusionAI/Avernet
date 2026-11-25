import { act, renderHook } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, expect, it, vi } from 'vitest'
import type { ReactNode } from 'react'
import { createWorkflowPack } from '../../editor/newWorkflowSave'
import { useCreateWorkflow } from '../useCreateWorkflow'
vi.mock('../../editor/newWorkflowSave', () => ({ createWorkflowPack: vi.fn() }))
afterEach(() => vi.resetAllMocks())
function setup() {
  const client = new QueryClient({ defaultOptions: { mutations: { retry: 3, retryDelay: 0 } } })
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>
  return { client, ...renderHook(useCreateWorkflow, { wrapper }) }
}
it('passes facade into createOnly and invalidates affected caches', async () => {
  const spec = { id: 'modal-create', title: 'New', version: '1', nodes: [] }, facade = { command: 'hello' }
  vi.mocked(createWorkflowPack).mockResolvedValueOnce({ requestedSpec: { ...spec, facade } } as never)
  const { result, client, unmount } = setup(), invalidate = vi.spyOn(client, 'invalidateQueries')
  await act(async () => { await result.current.mutateAsync({ workflowId: spec.id, spec, facade }) })
  expect(createWorkflowPack).toHaveBeenCalledWith({ ...spec, facade })
  expect(invalidate).toHaveBeenCalledWith({ queryKey: ['db-workflows'] })
  unmount(); client.clear()
})
it('does not automatically retry an unknown result even if the client defaults do', async () => {
  const spec = { id: 'modal-unknown', title: 'New', version: '1', nodes: [] }
  vi.mocked(createWorkflowPack).mockRejectedValue(new Error('unknown'))
  const { result, client, unmount } = setup()
  await act(async () => { await expect(result.current.mutateAsync({ workflowId: spec.id, spec })).rejects.toThrow('unknown') })
  expect(createWorkflowPack).toHaveBeenCalledTimes(1)
  unmount(); client.clear()
})
it('rejects identity mismatch and never sends', async () => {
  const { result, client, unmount } = setup()
  await act(async () => { await expect(result.current.mutateAsync({ workflowId: 'other', spec: { id: 'id', title: 'New', version: '1', nodes: [] } })).rejects.toThrow('身份不匹配') })
  expect(createWorkflowPack).not.toHaveBeenCalled(); unmount(); client.clear()
})
