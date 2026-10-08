import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useIssueGroupDetail, type IssueGroupView } from '../../issue-groups'
import { readOnlyJson } from '../../../../api/read-only-json'

vi.mock('../../../../api/read-only-json', () => ({ readOnlyJson: vi.fn() }))
const group: IssueGroupView = { presentation: 'summary', workflowId: 'wf', signature: 'timeout:fetch', inputDigest: 'digest',
  flowIds: [], sources: [], summarySources: [], summary: null, aggregationStatus: 'queued', aggregationId: 'job', stale: false }
beforeEach(() => vi.mocked(readOnlyJson).mockReset())

describe('lazy issue detail', () => {
  it('refreshes completed aggregation even when the source digest has not changed', async () => {
    vi.mocked(readOnlyJson).mockResolvedValue({ groups: [{ ...group, presentation: undefined }] })
    const { result, rerender } = renderHook(({ value }) => useIssueGroupDetail(value), { initialProps: { value: group } })
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
    const { result } = renderHook(() => useIssueGroupDetail(group))
    await waitFor(() => expect(result.current.error).toBe('read failed'))
    expect(result.current.group).toBeUndefined()
    vi.mocked(readOnlyJson).mockResolvedValue({ groups: [{ ...group, presentation: undefined }] })
    act(() => result.current.retry())
    await waitFor(() => expect(result.current.group?.signature).toBe(group.signature))
    expect(result.current.error).toBe('')
  })
})
