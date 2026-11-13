import { renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({ fetchJson: vi.fn() }))
vi.mock('@avernet/clawweb-shared/web/api/client', () => ({ fetchJson: mocks.fetchJson }))

import { useRecoveredFlowEvents } from '../use-recovered-flow-events'

describe('useRecoveredFlowEvents', () => {
  beforeEach(() => {
    mocks.fetchJson.mockReset().mockResolvedValue([])
  })

  it('requests the server maximum event window with a URL-safe flow id', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    )
    const { result } = renderHook(() => useRecoveredFlowEvents('flow /escaped'), { wrapper })

    await waitFor(() => expect(result.current.data).toEqual([]))
    expect(mocks.fetchJson).toHaveBeenCalledTimes(1)
    expect(mocks.fetchJson).toHaveBeenCalledWith('/api/runs/flow%20%2Fescaped/events?limit=1000')
  })
})
