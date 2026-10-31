import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import SubworkflowLinks, { SubworkflowProvider, UnattributedSubworkflowLinks } from '../SubworkflowLinks'

const fetchMock = vi.hoisted(() => vi.fn())
const auth = vi.hoisted(() => ({ epoch: 1 as number | null }))
vi.mock('../../hooks/useObservedAuthEpoch', () => ({ useObservedAuthEpoch: () => auth.epoch }))
vi.mock('@avernet/clawweb-shared/web/api/client', () => ({ fetchJson: fetchMock }))
const response = (items: unknown[]) => ({ items, completeness: 'best_effort' })

function setup(flowId = 'parent', nodeId = 'node-a') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const view = render(<QueryClientProvider client={client}><SubworkflowProvider flowId={flowId}><SubworkflowLinks nodeId={nodeId} ancestors={[flowId]} renderRun={id => <p>完整运行 {id}</p>} /></SubworkflowProvider></QueryClientProvider>)
  return { ...view, client }
}

afterEach(() => { fetchMock.mockReset(); auth.epoch = 1 })

describe('authorized subworkflow links', () => {
  it('uses only returned node links, labels attempts without guessing, and opens an explicit child route in a drawer', async () => {
    fetchMock.mockResolvedValue(response([
      { parentNodeId: 'node-a', childFlowId: 'child-1' },
      { parentNodeId: 'node-a', childFlowId: 'child-2', parentAttempt: 2 },
      { parentNodeId: 'node-a', childFlowId: 'child-2', parentAttempt: 2 },
      { parentNodeId: 'node-a', childFlowId: 'child-3', parentAttempt: 0 },
      { parentNodeId: 'node-b', childFlowId: 'other-node' },
      { parentNodeId: null, childFlowId: 'unknown-node' },
    ]))
    setup()
    const link = await screen.findByRole('link', { name: 'child-1' })
    expect(fetchMock).toHaveBeenCalledWith('/api/runs/parent/subworkflows', { signal: expect.any(AbortSignal) })
    expect(link).toHaveAttribute('href', '/runs/child-1')
    expect(link.closest('li')).toHaveTextContent('尝试次数未知')
    expect(screen.getByRole('link', { name: 'child-2' }).closest('li')).toHaveTextContent('第 2 次尝试')
    expect(screen.getByRole('link', { name: 'child-3' }).closest('li')).toHaveTextContent('尝试次数未知')
    expect(screen.queryByText('other-node')).not.toBeInTheDocument()
    expect(screen.queryByText('unknown-node')).not.toBeInTheDocument()
    link.focus()
    fireEvent.click(link)
    const dialog = screen.getByRole('dialog', { name: '子运行详情' })
    expect(within(dialog).getByText('完整运行 child-1')).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: '关闭子运行详情' })).toHaveFocus()
    fireEvent.keyDown(dialog, { key: 'Escape' })
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(link).toHaveFocus()
  })

  it('hides stale identifiers and closes the drawer on authorization or refresh failure', async () => {
    fetchMock.mockResolvedValueOnce(response([{ parentNodeId: 'node-a', childFlowId: 'sensitive' }]))
    const { client } = setup()
    fireEvent.click(await screen.findByRole('link', { name: 'sensitive' }))
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    fetchMock.mockRejectedValueOnce(new Error('not found'))
    await client.invalidateQueries({ queryKey: ['subworkflow-node-links'] })
    await screen.findByRole('alert')
    expect(screen.queryByText('sensitive')).not.toBeInTheDocument()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('does not reuse previously authorized identifiers after remount or run change', async () => {
    fetchMock.mockResolvedValueOnce(response([{ parentNodeId: 'node-a', childFlowId: 'secret' }]))
      .mockRejectedValueOnce(new Error('forbidden'))
    const view = setup()
    expect(await screen.findByText('secret')).toBeInTheDocument()
    view.unmount()
    setup('other')
    await screen.findByRole('alert')
    expect(screen.queryByText('secret')).not.toBeInTheDocument()
  })

  it('fails closed for malformed responses', async () => {
    fetchMock.mockResolvedValue({ items: [{ parentNodeId: 'node-a', childFlowId: 'secret' }] })
    setup()
    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument())
    expect(screen.queryByText('secret')).not.toBeInTheDocument()
  })
})

describe('request isolation', () => {
  it('does not show a previous parent response after switching runs while the new request is pending', async () => {
    let resolveOther!: (value: unknown) => void
    fetchMock.mockImplementation((url: string) => url.includes('/old/')
      ? Promise.resolve(response([{ parentNodeId: 'node-a', childFlowId: 'old-child' }]))
      : new Promise(resolve => { resolveOther = resolve }))
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const view = render(<QueryClientProvider client={client}><SubworkflowProvider flowId="old"><SubworkflowLinks nodeId="node-a" ancestors={['old']} renderRun={() => null} /></SubworkflowProvider></QueryClientProvider>)
    await screen.findByText('old-child')
    view.rerender(<QueryClientProvider client={client}><SubworkflowProvider flowId="new"><SubworkflowLinks nodeId="node-a" ancestors={['new']} renderRun={() => null} /></SubworkflowProvider></QueryClientProvider>)
    expect(screen.queryByText('old-child')).not.toBeInTheDocument()
    resolveOther(response([{ parentNodeId: 'node-a', childFlowId: 'new-child' }]))
    await screen.findByText('new-child')
    expect(screen.queryByText('old-child')).not.toBeInTheDocument()
  })
})


describe('unattributed authorized links', () => {
  it('shows null and missing node links once, with attempt labels and drawer, but not links belonging to a displayed node', async () => {
    fetchMock.mockResolvedValue(response([
      { parentNodeId: null, childFlowId: 'orphan', parentAttempt: null },
      { parentNodeId: 'missing', childFlowId: 'historical', parentAttempt: 2 },
      { parentNodeId: 'node-a', childFlowId: 'assigned' },
    ]))
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={client}><SubworkflowProvider flowId="parent">
      <SubworkflowLinks nodeId="node-a" ancestors={['parent']} renderRun={id => <p>详情 {id}</p>} />
      <UnattributedSubworkflowLinks nodeIds={['node-a']} ancestors={['parent']} renderRun={id => <p>详情 {id}</p>} />
    </SubworkflowProvider></QueryClientProvider>)
    const orphan = await screen.findByRole('link', { name: 'orphan' })
    expect(orphan.closest('li')).toHaveTextContent('尝试次数未知')
    expect(screen.getByRole('link', { name: 'historical' }).closest('li')).toHaveTextContent('第 2 次尝试')
    expect(screen.getAllByRole('link', { name: 'assigned' })).toHaveLength(1)
    expect(fetchMock).toHaveBeenCalledTimes(1)
    fireEvent.click(orphan)
    expect(within(screen.getByRole('dialog')).getByText('详情 orphan')).toBeInTheDocument()
  })

  it('closes the drawer and cancels old requests when the auth epoch changes', async () => {
    let resolveOld!: (value: unknown) => void
    let oldSignal!: AbortSignal
    fetchMock.mockImplementationOnce((_url, init) => { oldSignal = init.signal; return Promise.resolve(response([{ parentNodeId: 'node-a', childFlowId: 'old' }])) })
      .mockImplementationOnce((_url, init) => { oldSignal = init.signal; return new Promise(resolve => { resolveOld = resolve }) })
      .mockResolvedValue(response([{ parentNodeId: 'node-a', childFlowId: 'new' }]))
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    let childSignal!: AbortSignal
    function ChildDetail() {
      useQuery({ queryKey: ['child-detail'], queryFn: ({ signal }) => { childSignal = signal; return new Promise(() => {}) } })
      return <p>子详情</p>
    }
    const content = () => <QueryClientProvider client={client}><SubworkflowProvider flowId="parent"><SubworkflowLinks nodeId="node-a" ancestors={['parent']} renderRun={() => <ChildDetail />} /></SubworkflowProvider></QueryClientProvider>
    const view = render(content())
    fireEvent.click(await screen.findByText('old'))
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    await waitFor(() => expect(childSignal).toBeDefined())
    void client.invalidateQueries({ queryKey: ['subworkflow-node-links'] })
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))
    auth.epoch = 2
    view.rerender(content())
    expect(oldSignal.aborted).toBe(true)
    expect(childSignal.aborted).toBe(true)
    expect(screen.queryByText('old')).not.toBeInTheDocument()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    resolveOld(response([{ parentNodeId: 'node-a', childFlowId: 'late-secret' }]))
    expect(await screen.findByText('new')).toBeInTheDocument()
    expect(screen.queryByText('late-secret')).not.toBeInTheDocument()
  })
})
