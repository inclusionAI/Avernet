import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { focusManager, QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fetchJson } from '@avernet/clawweb-shared/web/api/client'
import SubworkflowExecutionTree from '../SubworkflowExecutionTree'

vi.mock('@avernet/clawweb-shared/web/api/client', () => ({ fetchJson: vi.fn() }))
const fetchMock = vi.mocked(fetchJson)
const response = (items: unknown[] = []) => ({ items, completeness: 'best_effort' })
const child = (id: string, extra: Record<string, unknown> = {}) => ({ childFlowId: id, childWorkflowId: 'wf', parentNodeId: 'parent', status: 'running', nodes: [], ...extra })
function renderTree(flowId = 'root') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const view = render(<QueryClientProvider client={client}><SubworkflowExecutionTree flowId={flowId} /></QueryClientProvider>)
  return { ...view, client }
}
beforeEach(() => { fetchMock.mockReset() })
afterEach(() => { cleanup(); focusManager.setFocused(undefined); vi.useRealTimers() })

describe('SubworkflowExecutionTree', () => {
  it('only requests the safe endpoint, deduplicates and loads nested levels lazily', async () => {
    fetchMock.mockImplementation(async url => {
      if (url === '/api/runs/root%2Fid/subworkflows') return response([child('a/b', { status: 'failed', nodes: [{ nodeId: 'n', nodeTitle: 'Step', status: 'succeeded', attempt: 2, output: 'secret-node' }] }), child('a/b'), child('waiting-child', { status: 'waiting' }), child('unknown', { status: null, parentNodeId: null, childWorkflowId: null, nodes: null })]) as never
      return response([child('grandchild')]) as never
    })
    renderTree('root/id')
    expect(await screen.findAllByRole('button', { name: '展开子运行 a/b' })).toHaveLength(1)
    expect(screen.getByText('状态：失败')).toBeInTheDocument()
    expect(screen.getByText('状态：等待中')).toBeInTheDocument()
    expect(screen.getByText('状态：未知')).toBeInTheDocument()
    expect(screen.getByText(/工作流：未知 · 父节点：未知/)).toBeInTheDocument()
    expect(screen.getByText(/可能不完整/)).toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(fetchMock).toHaveBeenCalledWith('/api/runs/root%2Fid/subworkflows', expect.objectContaining({ signal: expect.any(AbortSignal) }))
    expect(screen.queryByRole('link')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '展开子运行 a/b' }))
    expect(screen.getByText('Step（n）：成功 · 第 2 次')).toBeInTheDocument()
    expect(screen.queryByText(/secret-node/)).not.toBeInTheDocument()
    expect(await screen.findByRole('button', { name: '展开子运行 grandchild' })).toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledWith('/api/runs/a%2Fb/subworkflows', expect.anything())
    fireEvent.click(screen.getByRole('button', { name: '展开子运行 unknown' }))
    expect(screen.getByText('节点记录未知。')).toBeInTheDocument()
  })

  it('protects against cycles and excessive depth without requesting guarded flows', async () => {
    fetchMock.mockImplementation(async url => {
      const id = String(url).split('/')[3]
      return response([child(id === '0' ? '1' : '0')]) as never
    })
    const tree = renderTree('0')
    fireEvent.click(await screen.findByRole('button', { name: '展开子运行 1' }))
    fireEvent.click(await screen.findByRole('button', { name: '展开子运行 0' }))
    expect(screen.getByText(/检测到循环引用/)).toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledTimes(2)
    tree.unmount()
    fetchMock.mockClear()
    fetchMock.mockImplementation(async url => response([child(String(Number(String(url).split('/')[3]) + 1))]) as never)
    renderTree('0')
    for (let i = 1; i <= 8; i++) fireEvent.click(await screen.findByRole('button', { name: `展开子运行 ${i}` }))
    expect(screen.getByText(/达到显示深度限制/)).toBeInTheDocument()
    expect(fetchMock).not.toHaveBeenCalledWith('/api/runs/8/subworkflows', expect.anything())
  })

  it('shows loading, safe errors, missing data, manual retry and refresh', async () => {
    let resolve!: (value: never) => void
    fetchMock.mockImplementationOnce(() => new Promise(r => { resolve = r }))
    renderTree()
    expect(screen.getByRole('status')).toHaveTextContent('正在加载')
    await act(async () => resolve(response([{ childFlowId: '' }]) as never))
    expect(await screen.findByText(/记录无法识别/)).toBeInTheDocument()
    expect(screen.getByText(/没有可显示的子运行记录/)).toBeInTheDocument()
    fetchMock.mockRejectedValueOnce(new Error('sensitive details'))
    fireEvent.click(screen.getByRole('button', { name: '刷新 root 的子运行' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('结果未知')
    expect(screen.queryByText(/sensitive details/)).not.toBeInTheDocument()
    fetchMock.mockResolvedValueOnce(response([child('after-retry')]) as never)
    fireEvent.click(screen.getByRole('button', { name: '重试 root' }))
    expect(await screen.findByRole('button', { name: '展开子运行 after-retry' })).toBeInTheDocument()
  })

  it('rejects malformed responses and isolates errors to the expanded branch', async () => {
    fetchMock.mockImplementation(async url => url === '/api/runs/root/subworkflows' ? response([child('a')]) as never : { items: [] } as never)
    renderTree()
    fireEvent.click(await screen.findByRole('button', { name: '展开子运行 a' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('结果未知')
    expect(screen.getByText('状态：运行中')).toBeInTheDocument()
  })

  it('polls expanded terminal children, pauses when hidden, and stops after collapse', async () => {
    vi.useFakeTimers()
    fetchMock.mockImplementation(async url => url === '/api/runs/root/subworkflows' ? response([child('a', { status: 'succeeded' })]) as never : response() as never)
    renderTree()
    await act(async () => { await vi.advanceTimersByTimeAsync(20) })
    fireEvent.click(screen.getByRole('button', { name: '展开子运行 a' }))
    await act(async () => { await vi.advanceTimersByTimeAsync(20) })
    const calls = (id: string) => fetchMock.mock.calls.filter(([url]) => url === `/api/runs/${id}/subworkflows`).length
    expect(calls('a')).toBe(1)
    await act(async () => { await vi.advanceTimersByTimeAsync(15_100) })
    expect(calls('root')).toBeGreaterThan(1)
    expect(calls('a')).toBeGreaterThan(1)
    act(() => focusManager.setFocused(false))
    const hiddenRoot = calls('root')
    const hiddenChild = calls('a')
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000) })
    expect(calls('root')).toBe(hiddenRoot)
    expect(calls('a')).toBe(hiddenChild)
    fireEvent.click(screen.getByRole('button', { name: '收起子运行 a' }))
    act(() => focusManager.setFocused(true))
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000) })
    expect(calls('a')).toBe(hiddenChild)
    expect(calls('root')).toBeGreaterThan(hiddenRoot)
  })

  it('aborts an in-flight nested request when its branch is collapsed', async () => {
    let childSignal: AbortSignal | undefined
    fetchMock.mockImplementation((url, init) => {
      if (url === '/api/runs/root/subworkflows') return Promise.resolve(response([child('a')]) as never)
      childSignal = init?.signal as AbortSignal
      return new Promise(() => {})
    })
    renderTree()
    fireEvent.click(await screen.findByRole('button', { name: '展开子运行 a' }))
    await waitFor(() => expect(childSignal).toBeDefined())
    fireEvent.click(screen.getByRole('button', { name: '收起子运行 a' }))
    expect(childSignal?.aborted).toBe(true)
  })

  it('resets expansion when the root changes and handles missing ids', async () => {
    fetchMock.mockResolvedValue(response([child('a')]) as never)
    const { rerender, client } = renderTree()
    fireEvent.click(await screen.findByRole('button', { name: '展开子运行 a' }))
    rerender(<QueryClientProvider client={client}><SubworkflowExecutionTree flowId="other" /></QueryClientProvider>)
    expect(await screen.findByRole('button', { name: '展开子运行 a' })).toHaveAttribute('aria-expanded', 'false')
    rerender(<QueryClientProvider client={client}><SubworkflowExecutionTree flowId="" /></QueryClientProvider>)
    expect(screen.getByText('运行标识未知。')).toBeInTheDocument()
  })
})
