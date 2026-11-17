import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import AIStudioTrainWatchCard from '../AIStudioTrainWatchCard'
import { trainWatch } from '../../api/__tests__/aistudio-train.fixture'
import { toWatchDto } from '../../../server/contracts/aistudio-train'
import { watchFixture } from '../../../server/contracts/__tests__/aistudio-train.fixture'

let client: QueryClient
const fetchMock = vi.fn<typeof fetch>()
function show() {
  return render(<QueryClientProvider client={client}><AIStudioTrainWatchCard flowId="flow-1" nodeId="train" attempt={2} nodeStatus="waiting" /></QueryClientProvider>)
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
    expect(screen.getByRole('link', { name: '打开 AIStudio 任务' })).toHaveAttribute('rel', 'noopener noreferrer')
  })
  it.each([null, 'javascript:alert(1)', 'http://example.com/task', '//example.com/task', 'https://user:pass@example.com/task'])('does not invent/allow unsafe task links (%s)', async detailUrl => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ watch: { ...trainWatch, detailUrl } })))
    show()
    await screen.findByText('record-42')
    expect(screen.queryByRole('link')).not.toBeInTheDocument()
  })
  it('retains waiting and announces pending mailbox response without success', async () => {
    show()
    await screen.findByText('record-42')
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ ok: true, resolved: false, watch: { ...trainWatch, version: 2, manualPollPending: true, manualPollAllowed: false } }), { status: 202 }))
    fireEvent.click(screen.getByRole('button', { name: '手工查询' }))
    expect(await screen.findByText(/已提交查询，等待后台处理/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /等待查询结果/ })).toBeDisabled()
    expect(screen.getByText('节点状态：waiting')).toBeInTheDocument()
  })
  it('offers selectable recordId when clipboard fails', async () => {
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: vi.fn().mockRejectedValue(new Error('denied')) } })
    show()
    await screen.findByText('record-42')
    fireEvent.click(screen.getByRole('button', { name: '复制 recordId' }))
    await waitFor(() => expect(screen.getByRole('textbox', { name: '手动复制 recordId' })).toHaveValue('record-42'))
  })
})

describe('AIStudio manual query failure and accessibility', () => {
  it.each([
    [404, '未找到'], [409, '不允许'], [429, '冷却'], [502, '上游查询失败'], [503, '暂不可用'],
  ])('explains HTTP %s without failing the node or hiding cached watch', async (status, text) => {
    show()
    await screen.findByText('record-42')
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ error: { message: 'backend detail' } }), { status: Number(status) }))
    fireEvent.click(screen.getByRole('button', { name: '手工查询' }))
    expect(await screen.findByText(new RegExp(String(text)))).toHaveTextContent('backend detail')
    expect(screen.getByText('节点状态：waiting')).toBeInTheDocument()
    expect(screen.getByText('record-42')).toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })
  it('honors server retryAt on 429 and re-enables only after the deadline', async () => {
    const retryAt = new Date(Date.now() + 60000).toISOString()
    show()
    await screen.findByText('record-42')
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ error: 'cooldown', manualPollRetryAt: retryAt }), { status: 429 }))
    fireEvent.click(screen.getByRole('button', { name: '手工查询' }))
    expect(await screen.findByRole('button', { name: /冷却中/ })).toBeDisabled()
    expect(screen.getByText('手工查询可重试时间')).toBeInTheDocument()
  })
  it('shows server-owned next poll countdown and timeout without mutating the schedule', async () => {
    vi.useFakeTimers()
    try {
      vi.setSystemTime(new Date('2026-11-16T00:09:58Z'))
      client.setQueryData(['aistudio-train-watch', 'flow-1', 'train', 2], { watch: { ...trainWatch, timeoutAt: '2026-11-16T00:09:59Z', lastError: 'upstream query failed', errorCount: 2, lastPollSource: 'manual' } })
      show()
      expect(screen.getByText('2 秒后')).toBeInTheDocument()
      expect(screen.getByText('手工查询', { selector: 'dd' })).toBeInTheDocument()
      await act(async () => { await vi.advanceTimersByTimeAsync(2000) })
      expect(screen.getByText('已到计划时间，等待服务端查询')).toBeInTheDocument()
      expect(screen.getByText('已到超时时间，等待服务端确认')).toBeInTheDocument()
      expect(screen.getByText(/最近查询错误：upstream query failed/)).toBeInTheDocument()
      expect(client.getQueryData(['aistudio-train-watch', 'flow-1', 'train', 2])).toMatchObject({ watch: { nextPollAt: trainWatch.nextPollAt, phase: 'waiting' } })
    } finally { vi.useRealTimers() }
  })
  it('disables a forbidden manual query and handles absent status/url without guesses', async () => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ watch: { ...trainWatch, manualPollAllowed: false, lastStatus: null, detailUrl: null, lastPollSource: null } })))
    show()
    await screen.findByText('record-42')
    expect(screen.getByText('AIS 状态：尚未查询')).toBeInTheDocument()
    expect(screen.getByText('服务端当前不允许手工查询。')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '手工查询' })).toBeDisabled()
    expect(screen.queryByRole('link')).not.toBeInTheDocument()
  })
  it('allows explicit GET retry after an initial missing-watch response', async () => {
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ error: 'watch missing' }), { status: 404 }))
    show()
    expect(await screen.findByText(/未找到/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '重试观测' }))
    expect(await screen.findByText('record-42')).toBeInTheDocument()
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
})


describe('server cooldown is authoritative', () => {
  it('honors new 429 retryAt even when the cached watch has an older retryAt', async () => {
    client.setQueryData(['aistudio-train-watch', 'flow-1', 'train', 2], { watch: { ...trainWatch, manualPollRetryAt: new Date(Date.now() - 60000).toISOString() } })
    show()
    const retryAt = new Date(Date.now() + 60000).toISOString()
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ error: 'cooldown', manualPollRetryAt: retryAt }), { status: 429 }))
    fireEvent.click(screen.getByRole('button', { name: '手工查询' }))
    expect(await screen.findByRole('button', { name: /冷却中/ })).toBeDisabled()
  })
  it('re-enables on the same persisted version after a real server cooldown expires', async () => {
    vi.useFakeTimers()
    try {
      const now = Date.parse('2026-11-16T00:09:00Z') / 1000
      vi.setSystemTime(now * 1000)
      const key = ['aistudio-train-watch', 'flow-1', 'train', 2]
      const record = watchFixture({ version: 4, attempt: 2, lastManualRequestedAt: now - 30,
        firstPollAt: now + 600, nextPollAt: now + 600, timeoutAt: now + 3600 })
      const before = toWatchDto(record, now)
      client.setQueryData(key, { watch: before })
      fetchMock.mockImplementation(() => Promise.resolve(new Response(JSON.stringify({ watch: toWatchDto(record, Math.floor(Date.now() / 1000)) }))))
      show()
      expect(screen.getByRole('button', { name: '冷却中 30 秒' })).toBeDisabled()
      await act(async () => { await vi.advanceTimersByTimeAsync(29999) })
      expect(screen.getByRole('button', { name: /冷却中/ })).toBeDisabled()
      await act(async () => { await vi.advanceTimersByTimeAsync(2) })
      expect(screen.getByRole('button', { name: '手工查询' })).toBeEnabled()
      expect(screen.queryByText('手工查询可重试时间')).not.toBeInTheDocument()
      expect(client.getQueryData(key)).toMatchObject({ watch: { version: 4, nextPollAt: before.nextPollAt, manualPollAllowed: true, manualPollRetryAt: null } })
    } finally { vi.useRealTimers() }
  })
  it('disables manual polling on a same-version server timeout projection', async () => {
    vi.useFakeTimers()
    try {
      const now = Date.parse('2026-11-16T00:09:00Z') / 1000
      vi.setSystemTime(now * 1000)
      const key = ['aistudio-train-watch', 'flow-1', 'train', 2]
      const record = watchFixture({ version: 4, attempt: 2, firstPollAt: now + 600, nextPollAt: now + 600, timeoutAt: now + 5 })
      client.setQueryData(key, { watch: toWatchDto(record, now) })
      fetchMock.mockImplementation(() => Promise.resolve(new Response(JSON.stringify({ watch: toWatchDto(record, Math.floor(Date.now() / 1000)) }))))
      show()
      expect(screen.getByRole('button', { name: '手工查询' })).toBeEnabled()
      await act(async () => { await vi.advanceTimersByTimeAsync(5001) })
      expect(screen.getByRole('button', { name: '手工查询' })).toBeDisabled()
      expect(screen.getByText('服务端当前不允许手工查询。')).toBeInTheDocument()
      expect(screen.getByText('已到超时时间，等待服务端确认')).toBeInTheDocument()
      expect(client.getQueryData(key)).toEqual({ watch: toWatchDto(record, now + 5) })
    } finally { vi.useRealTimers() }
  })
})

describe('unconfigured verified identity', () => {
  it('explains initial 503 as feature/auth/owner readiness, never training failure', async () => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ error: 'verified identity is unavailable' }), { status: 503 }))
    show()
    expect(await screen.findByText(/认证.*owner.*尚未就绪/)).toHaveTextContent('不表示训练任务失败')
    expect(screen.getByText('节点状态：waiting')).toBeInTheDocument()
    expect(screen.getByText('AIS 状态：尚未查询')).toBeInTheDocument()
    expect(screen.queryByText('AIS 状态：FAILED')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '重试观测' })).toBeEnabled()
    expect(screen.getByRole('button', { name: '手工查询' })).toBeDisabled()
  })
})
