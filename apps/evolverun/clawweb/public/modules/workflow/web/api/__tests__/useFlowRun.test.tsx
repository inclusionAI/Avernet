import React from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useFlowRun } from '@avernet/clawweb-shared/web/api/hooks'
import type { FlowRunDetail, NodeStatus } from '@avernet/clawweb-shared/web/types'

const api = vi.hoisted(() => ({
  runs: {
    get: vi.fn(),
  },
}))

vi.mock('@avernet/clawweb-shared/web/api/client', () => ({ api }))

function detail(
  status: NodeStatus,
  nodes: { node_id: string; status: NodeStatus }[] = [],
): FlowRunDetail {
  return {
    run: {
      flow_id: 'flow-1',
      workflow_id: 'workflow-1',
      status,
    },
    nodes,
  } as unknown as FlowRunDetail
}

async function tick(ms = 0) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms)
  })
}

beforeEach(() => {
  vi.useFakeTimers()
  vi.resetAllMocks()
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

describe('useFlowRun live updates', () => {
  it('polls active runs and adopts the latest node snapshot', async () => {
    api.runs.get
      .mockResolvedValueOnce(detail('running'))
      .mockResolvedValueOnce(
        detail('succeeded', [{ node_id: 'node-a', status: 'succeeded' }]),
      )
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    const wrapper = ({ children }: { children: React.ReactNode }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    )

    const { result, unmount } = renderHook(() => useFlowRun('flow-1'), {
      wrapper,
    })
    await tick()
    expect(result.current.data?.run.status).toBe('running')

    await tick(5000)
    await tick()
    expect(api.runs.get).toHaveBeenCalledTimes(2)
    expect(queryClient.getQueryData(['run', 'flow-1'])).toMatchObject({
      run: { status: 'succeeded' },
      nodes: [{ node_id: 'node-a', status: 'succeeded' }],
    })
    unmount()
  })

  it('stops polling when a run reaches a terminal status', async () => {
    api.runs.get
      .mockResolvedValueOnce(detail('running'))
      .mockResolvedValue(detail('failed'))
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    const wrapper = ({ children }: { children: React.ReactNode }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    )

    const { unmount } = renderHook(() => useFlowRun('flow-1'), { wrapper })
    await tick()
    await tick(5000)
    expect(api.runs.get).toHaveBeenCalledTimes(2)

    await tick(15000)
    expect(api.runs.get).toHaveBeenCalledTimes(2)
    unmount()
  })
})
