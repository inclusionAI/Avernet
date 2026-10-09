import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useIssueGroupDetail, type IssueGroupView } from '../../issue-groups'
import { readOnlyJson } from '../../../../api/read-only-json'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'

vi.mock('../../../../api/read-only-json', () => ({ readOnlyJson: vi.fn() }))
const group: IssueGroupView = { presentation: 'summary', workflowId: 'wf', signature: 'timeout:fetch', inputDigest: 'digest',
  flowIds: [], sources: [], summarySources: [], summary: null, aggregationStatus: 'queued', aggregationId: 'job', stale: false }
beforeEach(() => vi.mocked(readOnlyJson).mockReset())
const wrapper = () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>
}

describe('lazy issue detail', () => {
  it('refreshes completed aggregation even when the source digest has not changed', async () => {
    vi.mocked(readOnlyJson).mockResolvedValue({ groups: [{ ...group, presentation: undefined }] })
    const { result, rerender } = renderHook(({ value }) => useIssueGroupDetail(value), { wrapper: wrapper(), initialProps: { value: group } })
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(readOnlyJson).toHaveBeenCalledWith('/api/evolve/issue-groups?workflowId=wf&signature=timeout%3Afetch', expect.any(AbortSignal))
    const completed = { ...group, presentation: undefined, aggregationStatus: 'completed', summary: { summary: 'New summary', causes: [], unknowns: [] } }
    vi.mocked(readOnlyJson).mockResolvedValue({ groups: [completed] })
    rerender({ value: { ...group, aggregationStatus: 'completed' } })
    await waitFor(() => expect(result.current.group?.summary?.summary).toBe('New summary'))
    expect(readOnlyJson).toHaveBeenCalledTimes(2)
  })
  it('reports failure and retries without presenting the compact row as complete evidence', async () => {
    vi.mocked(readOnlyJson).mockRejectedValueOnce(new Error('read failed'))
    const { result } = renderHook(() => useIssueGroupDetail(group), { wrapper: wrapper() })
    await waitFor(() => expect(result.current.error).toBe('read failed'))
    expect(result.current.group).toBeUndefined()
    vi.mocked(readOnlyJson).mockResolvedValue({ groups: [{ ...group, presentation: undefined }] })
    act(() => result.current.retry())
    await waitFor(() => expect(result.current.group?.signature).toBe(group.signature))
    expect(result.current.error).toBe('')
  })
  it('reuses full diagnosis data when the same issue is reopened without a source or job change', async () => {
    vi.mocked(readOnlyJson).mockResolvedValue({ groups: [{ ...group, presentation: undefined }] })
    const shared = wrapper()
    const first = renderHook(() => useIssueGroupDetail(group), { wrapper: shared })
    await waitFor(() => expect(first.result.current.loading).toBe(false))
    first.unmount()
    const next = renderHook(() => useIssueGroupDetail(group), { wrapper: shared })
    expect(next.result.current.group?.signature).toBe(group.signature)
    expect(next.result.current.loading).toBe(false)
    expect(readOnlyJson).toHaveBeenCalledTimes(1)
  })
})
