import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import InterventionPanel from '../InterventionPanel'

const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } })
const child = { flowId: 'child', status: 'failed', canIntervene: false, availableInterventions: [],
  interventionReady: false, interventionReadOnly: true, sessionConfigured: true,
  originBotId: null, originSessionKey: null, originSessionId: null, hasCredentials: null }
function mount(flowId: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return render(<QueryClientProvider client={client}><InterventionPanel flowId={flowId} runStatus="failed" nodes={[]} /></QueryClientProvider>)
}
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe('child intervention safety', () => {
  it('shows a read-only child state and never submits redacted session values or chat actions', async () => {
    const fetch = vi.fn(async () => json(child)); vi.stubGlobal('fetch', fetch)
    mount('child')
    expect(await screen.findByText(/子运行的人工介入和会话设置暂不可用/)).toBeInTheDocument()
    expect(screen.getByText(/会话已配置/)).toBeInTheDocument()
    expect(screen.queryByText('会话未配置')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '会话设置' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '保存' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '发送' })).not.toBeInTheDocument()
    expect(fetch).toHaveBeenCalledTimes(1)
    expect(fetch.mock.calls.every((call: unknown[]) => !['PATCH', 'POST'].includes(String((call[1] as RequestInit | undefined)?.method)))).toBe(true)
  })
  it('fails closed instead of presenting blank editable session fields when the policy read fails', async () => {
    const fetch = vi.fn(async () => json({ error: 'forbidden' }, 403)); vi.stubGlobal('fetch', fetch)
    mount('child')
    expect(await screen.findByRole('alert')).toHaveTextContent('操作已禁用')
    expect(screen.queryByRole('button', { name: '会话设置' })).not.toBeInTheDocument()
    expect(fetch).toHaveBeenCalledTimes(1)
  })
  it('preserves ordinary run session editing and its existing update payload', async () => {
    const requests: { url: string; init?: RequestInit }[] = []
    const ordinary = { ...child, flowId: 'ordinary', interventionReadOnly: false, interventionReady: true,
      canIntervene: true, availableInterventions: ['retry'], originBotId: 'bot:alice',
      originSessionKey: 'ordinary-session-key', originSessionId: 'ordinary-session-id', hasCredentials: true }
    vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
      requests.push({ url, init }); return json(init?.method === 'PATCH' ? { ok: true, flowId: 'ordinary' } : ordinary)
    }))
    mount('ordinary')
    expect(await screen.findByText('会话就绪')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '会话设置' }))
    fireEvent.click(screen.getByRole('button', { name: '保存' }))
    await waitFor(() => expect(requests.some(request => request.init?.method === 'PATCH')).toBe(true))
    const update = requests.find(request => request.init?.method === 'PATCH')!
    expect(JSON.parse(String(update.init?.body))).toEqual({ originBotId: 'bot:alice', originSessionKey: 'ordinary-session-key', originSessionId: 'ordinary-session-id' })
  })
})
