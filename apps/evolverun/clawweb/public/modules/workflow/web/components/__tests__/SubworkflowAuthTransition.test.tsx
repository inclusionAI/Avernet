import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import SubworkflowLinks, { SubworkflowProvider } from '../SubworkflowLinks'

const load = vi.hoisted(() => vi.fn())
vi.mock('@avernet/clawweb-shared/web/api/client', () => ({ fetchJson: load }))
vi.mock('@avernet/clawweb-shared/web/hooks/useClientUser', () => ({ isDevAuthMode: () => false }))
afterEach(() => { vi.unstubAllGlobals(); load.mockReset() })
const auth = (userId: string) => ({ ok: true, json: async () => ({ userId }) })
const response = (id: string) => ({ completeness: 'best_effort', items: [{ parentNodeId: 'n', childFlowId: id }] })

describe('subworkflow identity transition', () => {
  it('suspends old links, closes the drawer, cancels both query caches and ignores late results', async () => {
    let resolveAuth!: (value: unknown) => void
    let resolveOld!: (value: unknown) => void
    let parentSignal!: AbortSignal
    let childSignal!: AbortSignal
    vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(auth('user-a')).mockImplementationOnce(() => new Promise(resolve => { resolveAuth = resolve })))
    load.mockResolvedValueOnce(response('old-child')).mockImplementationOnce((_url, init) => {
      parentSignal = init.signal
      return new Promise(resolve => { resolveOld = resolve })
    }).mockResolvedValue(response('new-child'))
    function Child() {
      useQuery({ queryKey: ['child'], queryFn: ({ signal }) => { childSignal = signal; return new Promise(() => {}) } })
      return <p>child detail</p>
    }
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={client}><SubworkflowProvider flowId="parent"><SubworkflowLinks nodeId="n" ancestors={['parent']} renderRun={() => <Child />} /></SubworkflowProvider></QueryClientProvider>)
    fireEvent.click(await screen.findByText('old-child'))
    await waitFor(() => expect(childSignal).toBeDefined())
    void client.invalidateQueries({ queryKey: ['subworkflow-node-links'] })
    await waitFor(() => expect(parentSignal).toBeDefined())
    act(() => window.dispatchEvent(new Event('focus')))
    expect(screen.queryByText('old-child')).not.toBeInTheDocument()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(parentSignal.aborted).toBe(true)
    expect(childSignal.aborted).toBe(true)
    await act(async () => { resolveOld(response('late-secret')); resolveAuth(auth('user-b')); await Promise.resolve() })
    expect(await screen.findByText('new-child')).toBeInTheDocument()
    expect(screen.queryByText('late-secret')).not.toBeInTheDocument()
  })
})
