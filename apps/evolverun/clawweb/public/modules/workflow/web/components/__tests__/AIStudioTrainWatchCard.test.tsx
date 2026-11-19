import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import AIStudioTrainWatchCard from '../AIStudioTrainWatchCard'
import { trainWatch } from '../../api/__tests__/aistudio-train.fixture'
import { toWatchDto } from '../../../server/contracts/aistudio-train'
import { watchFixture } from '../../../server/contracts/__tests__/aistudio-train.fixture'

let client: QueryClient
const fetchMock = vi.fn<typeof fetch>()
const key = ['aistudio-train-watch', 'flow-1', 'train', 2]
function show(compact = false) {
  return render(<QueryClientProvider client={client}><AIStudioTrainWatchCard flowId="flow-1" nodeId="train" attempt={2} nodeStatus="waiting" compact={compact} /></QueryClientProvider>)
}
beforeEach(() => {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  fetchMock.mockReset().mockResolvedValue(new Response(JSON.stringify({ watch: trainWatch })))
  vi.stubGlobal('fetch', fetchMock)
})
afterEach(() => { cleanup(); client.clear(); vi.unstubAllGlobals(); vi.restoreAllMocks() })

describe('AIStudioTrainWatchCard', () => {
  it('separates node/watch/AIS status and displays server schedule in Shanghai time', async () => {
    show()
    expect(await screen.findByText('record-42')).toBeInTheDocument()
    expect(screen.getByText('节点状态：waiting')).toBeInTheDocument()
    expect(screen.getByText('观测状态：等待查询')).toBeInTheDocument()
    expect(screen.getByText('AIS 状态：RUNNING')).toBeInTheDocument()
    expect(screen.getAllByText('2026-11-16 08:05:00')).toHaveLength(2)
    expect(screen.getByText('300 秒')).toBeInTheDocument()
  })
  it('shows the AIS error status and a status interpretation diagnostic without inferring node failure', async () => {
    const watch = toWatchDto(watchFixture({
      attempt: 2, lastStatus: 'error', lastError: 'AIStudio status unknown', errorCount: 1,
    }), 200)
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ watch })))
    show()
    expect(await screen.findByText('AIS 状态：error')).toBeInTheDocument()
    expect(screen.getByText('节点状态：waiting')).toBeInTheDocument()
    expect(screen.getByText('连续观测异常')).toBeInTheDocument()
    expect(screen.getByText('最近观测诊断：AIStudio returned an unrecognized status（节点状态以服务端为准）')).toBeInTheDocument()
    expect(screen.queryByText(/查询错误|AIStudio status query failed/)).not.toBeInTheDocument()
  })
  it('places the safe external task link immediately after Copy in the recordId row, not the header', async () => {
    show()
    const record = await screen.findByText('record-42')
    const copy = screen.getByRole('button', { name: '复制 recordId' })
    const link = screen.getByRole('link', { name: '打开 AIStudio 任务' })
    expect(within(record.parentElement!).getByRole('link', { name: '打开 AIStudio 任务' })).toBe(link)
    expect(copy.nextElementSibling).toBe(link)
    expect(screen.getByRole('heading', { name: 'AIStudio 训练观测' })).not.toContainElement(link)
    expect(link).toHaveAttribute('href', trainWatch.detailUrl)
    expect(link).toHaveAttribute('target', '_blank')
    expect(link).toHaveAttribute('rel', 'noopener noreferrer')
  })
  it.each([null, 'javascript:alert(1)', 'http://example.com/task', '//example.com/task', 'https://user:pass@example.com/task'])('does not invent/allow unsafe task links (%s)', async detailUrl => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ watch: { ...trainWatch, detailUrl } })))
    show()
    await screen.findByText('record-42')
    expect(screen.queryByRole('link')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '复制 recordId' })).toBeEnabled()
  })
  it('offers selectable recordId when clipboard fails', async () => {
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: vi.fn().mockRejectedValue(new Error('denied')) } })
    show()
    await screen.findByText('record-42')
    fireEvent.click(screen.getByRole('button', { name: '复制 recordId' }))
    await waitFor(() => expect(screen.getByRole('textbox', { name: '手动复制 recordId' })).toHaveValue('record-42'))
  })
})

describe.each([false, true])('automatic observation without manual controls (compact=%s)', compact => {
  it.each([
    { name: 'allowed', manualPollAllowed: true },
    { name: 'unavailable', manualPollAllowed: false },
    { name: 'pending', manualPollAllowed: false, manualPollPending: true },
    { name: 'cooldown', manualPollAllowed: false, manualPollRetryAt: new Date(Date.now() + 60000).toISOString() },
  ])('does not expose manual actions or hints when $name', async ({ name: _name, ...state }) => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ watch: { ...trainWatch, ...state } })))
    show(compact)
    await screen.findByRole('link', { name: '打开 AIStudio 任务' })
    expect(screen.queryByRole('button', { name: /手工查询|查询中|等待查询结果|冷却/ })).not.toBeInTheDocument()
    expect(screen.queryByText(/不允许手工查询|手工查询可重试时间|已提交查询|正在请求查询|冷却/)).not.toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(fetchMock.mock.calls.every(([, init]) => !init?.method || init.method === 'GET')).toBe(true)
  })
  it('keeps automatic five-second GET refresh and stops after resolution', async () => {
    vi.useFakeTimers()
    try {
      client.setQueryData(key, { watch: trainWatch })
      fetchMock.mockResolvedValue(new Response(JSON.stringify({ watch: { ...trainWatch, version: 2, phase: 'resolved', nextPollAt: null, manualPollAllowed: false, lastStatus: 'completed' } })))
      show(compact)
      expect(fetchMock).not.toHaveBeenCalled()
      await act(async () => { await vi.advanceTimersByTimeAsync(5001) })
      expect(fetchMock).toHaveBeenCalledTimes(1)
      expect(client.getQueryData(key)).toMatchObject({ watch: { version: 2, phase: 'resolved' } })
      if (!compact) expect(screen.getByText('AIS 状态：completed')).toBeInTheDocument()
      await act(async () => { await vi.advanceTimersByTimeAsync(10000) })
      expect(fetchMock).toHaveBeenCalledTimes(1)
      expect(fetchMock.mock.calls[0][1]?.method).toBeUndefined()
    } finally { vi.useRealTimers() }
  })
  it('allows explicit GET retry after an initial missing-watch response', async () => {
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ error: 'watch missing' }), { status: 404 }))
    show(compact)
    expect(await screen.findByText(/未找到/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '重试观测' }))
    expect(await screen.findByRole('link', { name: '打开 AIStudio 任务' })).toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(fetchMock.mock.calls.every(([, init]) => !init?.method || init.method === 'GET')).toBe(true)
  })
})

describe('watch schedule, errors and accessibility', () => {
  it.each([[502, '上游查询失败'], [503, '暂不可用']])('explains GET HTTP %s without failing the node or hiding cached watch', async (status, text) => {
    client.setQueryData(key, { watch: trainWatch })
    show()
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ error: { message: 'backend detail' } }), { status: Number(status) }))
    await act(async () => { await client.refetchQueries({ queryKey: key }) })
    expect(await screen.findByText(new RegExp(String(text)))).toHaveTextContent('backend detail')
    expect(screen.getByText('节点状态：waiting')).toBeInTheDocument()
    expect(screen.getByText('record-42')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '重试观测' })).toBeEnabled()
  })
  it('shows server-owned next poll countdown, historical manual source and timeout without mutating the schedule', async () => {
    vi.useFakeTimers()
    try {
      vi.setSystemTime(new Date('2026-11-16T00:09:58Z'))
      client.setQueryData(key, { watch: { ...trainWatch, timeoutAt: '2026-11-16T00:09:59Z', lastError: 'upstream query failed', errorCount: 2, lastPollSource: 'manual' } })
      show()
      expect(screen.getByText('2 秒后')).toBeInTheDocument()
      expect(screen.getByText('手工查询', { selector: 'dd' })).toBeInTheDocument()
      expect(screen.queryByRole('button', { name: '手工查询' })).not.toBeInTheDocument()
      await act(async () => { await vi.advanceTimersByTimeAsync(2000) })
      expect(screen.getByText('已到计划时间，等待服务端查询')).toBeInTheDocument()
      expect(screen.getByText('已到超时时间，等待服务端确认')).toBeInTheDocument()
      expect(screen.getByText(/最近观测诊断：upstream query failed/)).toBeInTheDocument()
      expect(client.getQueryData(key)).toMatchObject({ watch: { nextPollAt: trainWatch.nextPollAt, phase: 'waiting' } })
    } finally { vi.useRealTimers() }
  })
  it('handles absent status/url without guesses or manual-unavailable hints', async () => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ watch: { ...trainWatch, manualPollAllowed: false, lastStatus: null, detailUrl: null, lastPollSource: null } })))
    show()
    await screen.findByText('record-42')
    expect(screen.getByText('AIS 状态：尚未查询')).toBeInTheDocument()
    expect(screen.queryByText('服务端当前不允许手工查询。')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '手工查询' })).not.toBeInTheDocument()
    expect(screen.queryByRole('link')).not.toBeInTheDocument()
  })
  it('copies using Clipboard and announces success without stealing focus', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } })
    show()
    await screen.findByText('record-42')
    const button = screen.getByRole('button', { name: '复制 recordId' })
    button.focus()
    fireEvent.click(button)
    expect(await screen.findByText('已复制 recordId')).toBeInTheDocument()
    expect(writeText).toHaveBeenCalledWith('record-42')
    expect(button).toHaveFocus()
    expect(button.className).toContain('focus-visible:ring-2')
  })
  it('provides focused selected fallback even when Clipboard is unavailable', async () => {
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: undefined })
    show()
    await screen.findByText('record-42')
    fireEvent.click(screen.getByRole('button', { name: '复制 recordId' }))
    const input = await screen.findByRole('textbox', { name: '手动复制 recordId' }) as HTMLInputElement
    expect(input).toHaveFocus()
    expect(input.selectionEnd).toBe(trainWatch.recordId.length)
  })
  it('explains initial 503 as feature/auth/owner readiness, never training failure', async () => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ error: 'verified identity is unavailable' }), { status: 503 }))
    show()
    expect(await screen.findByText(/认证.*owner.*尚未就绪/)).toHaveTextContent('不表示训练任务失败')
    expect(screen.getByText('节点状态：waiting')).toBeInTheDocument()
    expect(screen.getByText('AIS 状态：尚未查询')).toBeInTheDocument()
    expect(screen.queryByText('AIS 状态：FAILED')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '重试观测' })).toBeEnabled()
    expect(screen.queryByRole('button', { name: '手工查询' })).not.toBeInTheDocument()
  })
})
