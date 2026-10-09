import { useState } from 'react'
import { act, render, screen, within, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, afterEach, expect, it, vi } from 'vitest'
import EvolutionTab from '../../EvolutionTab'
import IssueRepairSuggestions from '../IssueRepairSuggestions'
import type { RepairCandidatesResponse, RepairInboxItem } from '../../../../../server/contracts/repair-workbench'
const accessState = vi.hoisted(() => ({ ready: true }))

vi.mock('../../../../api/hooks', () => ({
  useWorkflowAccess: () => ({ data: accessState.ready ? { canEdit: true } : undefined, isPending: !accessState.ready }),
  useEvolveSuggestions: () => ({ data: { suggestions: [] }, isLoading: false }),
  useSuggestionApplyTasks: () => ({ data: { tasks: [] } }),
  useRecordSuggestionAction: () => ({ mutate: vi.fn() }),
  useRunEvolutionAnalysis: () => ({ data: undefined, isLoading: false }),
  useEligibleBotsForSuggestion: () => ({ data: { bots: [] }, isLoading: false }),
  useApplySuggestionsBatch: () => ({ mutateAsync: vi.fn(), isPending: false }),
}))
const item = (id: string): RepairInboxItem => ({
  itemId: id, workflowId: 'wf', groupKey: 'g', proposalKey: id, contentRevision: 1, previousItemId: null,
  proposal: { summary: id }, instruction: '', sources: [], context: { signature: 'issue-0' },
  episodeKey: 'initial', state: 'pending', stateVersion: 0, activeTaskId: null, activeRevision: null,
  disposition: null, updatedAtMs: 0, sourceAvailable: true,
})
const result: RepairCandidatesResponse = {
  schemaVersion: 'workflow-repair/v2', workflowId: 'wf', inputDigest: 'd'.repeat(64), items: [item('retry'), item('timeout')],
  tasks: [], capabilities: { generation: true, diff: false, publication: false, reason: null }, canEdit: true,
  includeHistorical: false, activeLookbackDays: 30, repairSignatureKeys: [],
  counts: { all: 2, pending: 2, processing: 0, awaiting_verification: 0, closed: 0, no_action: 0 },
  page: { page: 1, pageSize: 20, total: 2, totalPages: 1 }, limits: { maxItems: 100, maxRequestBytes: 65536 },
}
const requests: URL[] = []
const previewScopes: string[][] = []
let omitPreviews = false
const choices = () => within(screen.queryByRole('dialog', { name: '问题详情' }) ?? document.body)
const group = (i: number) => ({ workflowId: 'wf', signature: `issue-${i}`, inputDigest: String(i),
  flowIds: [`run-${i}`], aggregationStatus: 'not_generated', aggregationId: null, summary: null, stale: false,
  sources: [{ sourceId: String(i), flowId: `run-${i}`, flowIds: [`run-${i}`], analysisId: 'an', diagnosisId: String(i),
    nodeId: `node-${i}`, failureSignature: `issue-${i}`, failureMode: i === 20 ? 'error' : 'timeout', reasoning: 'slow', completedAtMs: i + 1, evidenceEventIds: [] }],
})
beforeEach(() => {
  sessionStorage.clear(); requests.length = 0; previewScopes.length = 0; omitPreviews = false
  accessState.ready = true
  result.capabilities.generation = true
  result.capabilities.reason = null
  result.inputDigest = 'd'.repeat(64)
  result.tasks = []
  result.items = [item('retry'), item('timeout')]
  vi.stubGlobal('fetch', vi.fn(async (raw: string, init?: RequestInit) => {
    const url = new URL(raw, 'http://localhost'); requests.push(url)
    let body: unknown
    if (url.pathname.endsWith('/issue-groups')) {
      const page = Number(url.searchParams.get('page') ?? 1)
      const pageSize = Number(url.searchParams.get('pageSize') ?? 20)
      const all = Array.from({ length: 21 }, (_, i) => group(i))
      const filtered = all.filter(g => (!url.searchParams.has('nodeId') || g.sources[0].nodeId === url.searchParams.get('nodeId'))
        && (!url.searchParams.has('failureMode') || g.sources[0].failureMode === url.searchParams.get('failureMode'))
        && (!url.searchParams.has('signature') || g.signature === url.searchParams.get('signature')))
      body = { groups: filtered.slice((page - 1) * pageSize, page * pageSize),
        facets: { nodes: all.map(g => g.sources[0].nodeId), modes: ['timeout', 'error'] },
        page: { page, pageSize, total: filtered.length, totalPages: Math.ceil(filtered.length / pageSize) } }
    } else if (url.pathname.includes('/candidates')) {
      const query = url.pathname.endsWith('/query') ? JSON.parse(init?.body as string) : Object.fromEntries(url.searchParams)
      if (url.pathname.endsWith('/query')) {
        expect(init?.method).toBe('POST')
        previewScopes.push(query.previewSignatures)
      }
      const page = Number(query.page ?? 1)
      const pageSize = Number(query.pageSize ?? 20)
      body = { ...result, items: result.items.slice((page - 1) * pageSize, page * pageSize),
        issuePreviews: (query.previewSignatures ?? []).map((signature: string) => {
          const items = result.items.filter(item => item.context?.signature === signature)
          return { signature, total: items.length, items: omitPreviews ? [] : items.slice(0, 3) }
        }),
        page: { page, pageSize, total: result.items.length, totalPages: Math.ceil(result.items.length / pageSize) } }
    }
    else if (url.pathname.includes('/items/')) body = item(url.pathname.split('/').at(-1)!)
    else throw new Error(`Unexpected request: ${url.pathname}`)
    return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })
  }))
})
afterEach(() => vi.unstubAllGlobals())

it('reuses a recently visited issue page without refetching or hiding its suggestions', async () => {
  const user = userEvent.setup()
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <MemoryRouter><EvolutionTab workflowId="wf" /></MemoryRouter>
  </QueryClientProvider>)
  await user.click(await screen.findByRole('checkbox', { name: '选择 retry' }))
  const pager = screen.getByRole('region', { name: '问题分页' })
  await user.click(within(pager).getByRole('button', { name: '下一页' }))
  await screen.findByText('node-20', { selector: 'span' })
  await waitFor(() => expect(previewScopes).toHaveLength(2))
  await user.click(within(screen.getByRole('region', { name: '问题分页' })).getByRole('button', { name: '上一页' }))
  expect(await screen.findByRole('checkbox', { name: '选择 retry' })).toBeChecked()
  expect(previewScopes).toHaveLength(2)
})

it('reserves a named suggestion loading area before the first batch response', async () => {
  const fetchOther = globalThis.fetch
  let release!: () => void
  const gate = new Promise<void>(resolve => { release = resolve })
  vi.stubGlobal('fetch', vi.fn(async (input: string, init?: RequestInit) => {
    if (input.endsWith('/candidates/query')) await gate
    return fetchOther(input, init)
  }))
  render(<QueryClientProvider client={new QueryClient()}><MemoryRouter><EvolutionTab workflowId="wf" /></MemoryRouter></QueryClientProvider>)
  await screen.findByText('node-0', { selector: 'span' })
  expect(screen.getAllByRole('status', { name: '修复建议加载中' })).toHaveLength(20)
  await act(async () => { release() })
  expect(await screen.findByRole('checkbox', { name: '选择 retry' })).toBeVisible()
  expect(screen.queryByRole('status', { name: '修复建议加载中' })).not.toBeInTheDocument()
})

it('discards another page cache when a refreshed snapshot reports changed sources', async () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const user = userEvent.setup()
  render(<QueryClientProvider client={client}><MemoryRouter><EvolutionTab workflowId="wf" /></MemoryRouter></QueryClientProvider>)
  await user.click(await screen.findByRole('checkbox', { name: '选择 retry' }))
  await user.click(within(screen.getByRole('region', { name: '问题分页' })).getByRole('button', { name: '下一页' }))
  await waitFor(() => expect(previewScopes).toHaveLength(2))
  result.inputDigest = 'e'.repeat(64)
  result.items = [item('updated')]
  await act(async () => { await client.invalidateQueries({ queryKey: ['repair-issue-previews', 'wf'], predicate: query => query.getObserversCount() > 0 }) })
  expect(screen.getByText(/建议来源已更新，原选择已清空/)).toBeVisible()
  await user.click(within(screen.getByRole('region', { name: '问题分页' })).getByRole('button', { name: '上一页' }))
  expect(await screen.findByRole('checkbox', { name: '选择 updated' })).not.toBeChecked()
  expect(screen.queryByRole('checkbox', { name: '选择 retry' })).not.toBeInTheDocument()
  expect(previewScopes).toHaveLength(4)
})

it('does not restore a cached generation action after another page discovers an active task', async () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const user = userEvent.setup()
  render(<QueryClientProvider client={client}><MemoryRouter><EvolutionTab workflowId="wf" /></MemoryRouter></QueryClientProvider>)
  await user.click(await screen.findByRole('checkbox', { name: '选择 retry' }))
  await user.click(within(screen.getByRole('region', { name: '问题分页' })).getByRole('button', { name: '下一页' }))
  await waitFor(() => expect(previewScopes).toHaveLength(2))
  result.tasks = [{ taskId: 'FIX-current', revision: 1, phase: 'drafting', updatedAtMs: 2, itemCount: 1 }]
  await act(async () => { await client.invalidateQueries({ queryKey: ['repair-issue-previews', 'wf'], predicate: query => query.getObserversCount() > 0 }) })
  await user.click(within(screen.getByRole('region', { name: '问题分页' })).getByRole('button', { name: '上一页' }))
  await screen.findByRole('checkbox', { name: '选择 retry' })
  expect(screen.getByRole('region', { name: '本次修复操作' })).toHaveTextContent('已有修复任务')
  expect(screen.getByRole('button', { name: '生成修复草稿（1）' })).toBeDisabled()
})

it('keeps cached suggestions visible during background validation and removes them on a forbidden response', async () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><MemoryRouter><EvolutionTab workflowId="wf" /></MemoryRouter></QueryClientProvider>)
  await screen.findByRole('checkbox', { name: '选择 retry' })
  const fetchOther = globalThis.fetch
  let release!: () => void, started = false
  const gate = new Promise<void>(resolve => { release = resolve })
  vi.stubGlobal('fetch', vi.fn(async (input: string, init?: RequestInit) => {
    if (!input.endsWith('/candidates/query')) return fetchOther(input, init)
    started = true; await gate
    return new Response(JSON.stringify({ code: 'FORBIDDEN' }), { status: 403 })
  }))
  act(() => { void client.invalidateQueries({ queryKey: ['repair-issue-previews', 'wf'] }) })
  await waitFor(() => expect(started).toBe(true))
  expect(screen.getByRole('checkbox', { name: '选择 retry' })).toBeVisible()
  expect(screen.getByRole('checkbox', { name: '选择 retry' })).toBeDisabled()
  await act(async () => { release() })
  expect(await screen.findByRole('alert')).toHaveTextContent('403')
  expect(screen.queryByRole('checkbox', { name: '选择 retry' })).not.toBeInTheDocument()
})

it('keeps suggestions reachable when the preview byte budget omits all their items', async () => {
  omitPreviews = true
  const user = userEvent.setup()
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <MemoryRouter><EvolutionTab workflowId="wf" /></MemoryRouter>
  </QueryClientProvider>)
  await user.click(await screen.findByRole('button', { name: '查看全部 2 条建议（已展示 0 条）' }))
  const dialog = await screen.findByRole('dialog', { name: '问题详情' })
  const checkbox = await within(dialog).findByRole('checkbox', { name: '选择 retry' })
  expect(checkbox).toBeEnabled()
  await user.click(checkbox)
  expect(within(dialog).getByRole('button', { name: '生成修复草稿（1）' })).toBeEnabled()
})

it('does not scan suggestions twice when initial workflow permission finishes loading', async () => {
  accessState.ready = false
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const ui = () => <QueryClientProvider client={client}><MemoryRouter><EvolutionTab workflowId="wf" /></MemoryRouter></QueryClientProvider>
  const view = render(ui())
  await screen.findByText('node-0', { selector: 'span' })
  expect(requests.filter(url => url.pathname.includes('/candidates'))).toHaveLength(0)
  accessState.ready = true
  view.rerender(ui())
  await screen.findByRole('checkbox', { name: '选择 retry' })
  expect(requests.filter(url => url.pathname.includes('/candidates'))).toHaveLength(1)
})

it('keeps repair context usable when the issue endpoint fails before returning any data', async () => {
  const fetchOther = globalThis.fetch
  vi.stubGlobal('fetch', vi.fn((input: string, init?: RequestInit) => input.includes('/issue-groups')
    ? Promise.resolve(new Response(JSON.stringify({ error: 'issue summary unavailable' }), { status: 503 })) : fetchOther(input, init)))
  const user = userEvent.setup()
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <MemoryRouter><EvolutionTab workflowId="wf" /></MemoryRouter>
  </QueryClientProvider>)
  await screen.findByRole('alert')
  await user.click(screen.getByRole('button', { name: '全部建议与历史' }))
  const dialog = screen.getByRole('dialog', { name: '全部建议与历史' })
  const checkbox = await within(dialog).findByRole('checkbox', { name: '选择 retry' })
  expect(checkbox).toBeEnabled()
  await user.click(checkbox)
  expect(within(dialog).getByRole('button', { name: '生成修复草稿（1）' })).toBeEnabled()
})

it('turns and filters issue pages while batching suggestion previews for the visible signatures', async () => {
  const user = userEvent.setup()
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <MemoryRouter><EvolutionTab workflowId="wf" /></MemoryRouter>
  </QueryClientProvider>)
  expect(await screen.findByText('node-0', { selector: 'span' })).toBeVisible()
  const pager = await screen.findByRole('region', { name: '问题分页' })
  await user.click(within(pager).getByRole('button', { name: '下一页' }))
  expect(await screen.findByText('node-20', { selector: 'span' })).toBeVisible()
  expect(screen.queryByText('node-0', { selector: 'span' })).not.toBeInTheDocument()
  await user.selectOptions(screen.getByRole('combobox', { name: '问题节点' }), 'node-3')
  expect(await screen.findByText('node-3', { selector: 'span' })).toBeVisible()
  expect(requests.some(url => url.searchParams.get('page') === '1' && url.searchParams.get('nodeId') === 'node-3')).toBe(true)
  expect(previewScopes.some(signatures => signatures.includes('issue-3'))).toBe(true)
  await user.selectOptions(screen.getByRole('combobox', { name: '问题模式' }), 'error')
  expect(await screen.findByText('没有符合当前筛选条件的问题')).toBeVisible()
  await user.selectOptions(screen.getByRole('combobox', { name: '问题模式' }), 'all')
  expect(await screen.findByText('node-3', { selector: 'span' })).toBeVisible()
})

it('selects directly in the issue list and shows unavailable generation beside the fixed next action', async () => {
  result.capabilities.generation = false
  result.capabilities.reason = 'Repair generation provider is unavailable'
  const user = userEvent.setup()
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <MemoryRouter><EvolutionTab workflowId="wf" /></MemoryRouter>
  </QueryClientProvider>)
  await user.click(await choices().findByRole('checkbox', { name: '选择 retry' }))
  const bar = screen.getByRole('region', { name: '本次修复操作' })
  expect(within(bar).getByText(/已选 1 条建议/)).toBeVisible()
  expect(within(bar).getByText(/生成服务尚未接入/)).toBeVisible()
  expect(within(bar).getByRole('button', { name: '生成修复草稿（1）' })).toBeDisabled()
  expect(requests.filter(url => url.pathname.includes('/candidates'))).toHaveLength(1)
  expect(requests.filter(url => url.pathname.includes('/items/'))).toHaveLength(0)
  await user.click(within(screen.getByText('node-0', { selector: 'span' }).closest('article')!).getByRole('button', { name: '问题详情' }))
  await user.click(screen.getByRole('button', { name: '修复建议' }))
  const drawer = screen.getByRole('dialog', { name: '问题详情' })
  expect(within(drawer).getByRole('button', { name: '生成修复草稿（1）' })).toBeDisabled()
  expect(within(drawer).getByText(/生成服务尚未接入/)).toBeVisible()
  expect(screen.queryByRole('button', { name: '确认修复范围' })).not.toBeInTheDocument()
})

it('reuses loaded suggestions on a tab revisit and after closing and reopening the drawer', async () => {
  const user = userEvent.setup()
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <MemoryRouter><EvolutionTab workflowId="wf" /></MemoryRouter>
  </QueryClientProvider>)
  await user.click(within((await screen.findByText('node-0', { selector: 'span' })).closest('article')!).getByRole('button', { name: '问题详情' }))
  await user.click(screen.getByRole('button', { name: '修复建议' }))
  await within(screen.getByRole('dialog', { name: '问题详情' })).findByRole('checkbox', { name: '选择 retry' })
  const count = requests.filter(url => url.pathname.includes('/candidates')).length
  await user.click(screen.getByRole('button', { name: '问题原因' }))
  await user.click(screen.getByRole('button', { name: '修复建议' }))
  expect(within(screen.getByRole('dialog', { name: '问题详情' })).getByRole('checkbox', { name: '选择 retry' })).toBeVisible()
  await user.click(within(screen.getByRole('dialog', { name: '问题详情' })).getByRole('button', { name: '关闭' }))
  await user.click(within(screen.getByText('node-0', { selector: 'span' }).closest('article')!).getByRole('button', { name: '问题详情' }))
  await user.click(screen.getByRole('button', { name: '修复建议' }))
  expect(within(screen.getByRole('dialog', { name: '问题详情' })).getByRole('checkbox', { name: '选择 retry' })).toBeVisible()
  expect(requests.filter(url => url.pathname.includes('/candidates'))).toHaveLength(count)
})

it('presents suggestions together for multi-selection and does not hydrate evidence until requested', async () => {
  const user = userEvent.setup()
  function Harness() {
    const [selected, setSelected] = useState<string[]>([])
    return <IssueRepairSuggestions workflowId="wf" signature="issue-0" includeHistorical={false}
      selected={selected} canEdit limit={100}
      onToggle={id => setSelected(old => old.includes(id) ? old.filter(x => x !== id) : [...old, id])}
      onResult={() => {}} renderItem={value => <p>Evidence for {value.itemId}</p>} />
  }
  render(<QueryClientProvider client={new QueryClient()}><Harness /></QueryClientProvider>)
  await user.click(await choices().findByRole('checkbox', { name: '选择 retry' }))
  await user.click(choices().getByRole('checkbox', { name: '选择 timeout' }))
  expect(choices().getByRole('checkbox', { name: '选择 retry' })).toBeChecked()
  expect(choices().getByRole('checkbox', { name: '选择 timeout' })).toBeChecked()
  expect(screen.queryByRole('combobox', { name: '切换建议' })).not.toBeInTheDocument()
  expect(screen.queryByText('Evidence for retry')).not.toBeInTheDocument()
  await user.click(screen.getAllByRole('button', { name: '查看修改与依据' })[0])
  expect(screen.getByText('Evidence for retry')).toBeVisible()
})

it('retains selections across issue pages and reviews their actual titles before creating a draft', async () => {
  const user = userEvent.setup()
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <MemoryRouter><EvolutionTab workflowId="wf" /></MemoryRouter>
  </QueryClientProvider>)
  await user.click(within((await screen.findByText('node-0', { selector: 'span' })).closest('article')!).getByRole('button', { name: '问题详情' }))
  await user.click(screen.getByRole('button', { name: '修复建议' }))
  await user.click(await choices().findByRole('checkbox', { name: '选择 retry' }))
  await user.click(choices().getByRole('checkbox', { name: '选择 timeout' }))
  await user.click(within(screen.getByRole('dialog', { name: '问题详情' })).getByRole('button', { name: '关闭' }))
  await user.click(within(screen.getByRole('region', { name: '问题分页' })).getByRole('button', { name: '下一页' }))
  await waitFor(() => expect(screen.getByRole('button', { name: '生成修复草稿（2）' })).toBeEnabled())
  await user.click(screen.getByRole('button', { name: '生成修复草稿（2）' }))
  const confirmation = screen.getByRole('dialog', { name: '生成修复草稿' })
  expect(within(confirmation).getByText('retry')).toBeVisible()
  expect(within(confirmation).getByText('timeout')).toBeVisible()
  expect(within(confirmation).getByRole('button', { name: '确认生成草稿' })).toBeEnabled()
})

it('requires a choice between conflicting modifications before draft generation', async () => {
  result.items = [600, 90].map(value => ({ ...item(String(value)),
    proposal: { summary: String(value), operations: [{ nodeId: 'fetch', path: '/executor/timeoutMs', value }] } }))
  const user = userEvent.setup()
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <MemoryRouter><EvolutionTab workflowId="wf" /></MemoryRouter>
  </QueryClientProvider>)
  await user.click(within((await screen.findByText('node-0', { selector: 'span' })).closest('article')!).getByRole('button', { name: '问题详情' }))
  await user.click(screen.getByRole('button', { name: '修复建议' }))
  await user.click(await choices().findByRole('checkbox', { name: '选择 600' }))
  await user.click(choices().getByRole('checkbox', { name: '选择 90' }))
  await user.click(within(screen.getByRole('dialog', { name: '问题详情' })).getByRole('button', { name: '关闭' }))
  await user.click(screen.getByRole('button', { name: '生成修复草稿（2）' }))
  const confirmation = screen.getByRole('dialog', { name: '生成修复草稿' })
  expect(within(confirmation).getByRole('alert')).toHaveTextContent('同一配置')
  expect(within(confirmation).getByRole('button', { name: '确认生成草稿' })).toBeDisabled()
  await user.click(within(confirmation).getByRole('button', { name: '移除 90' }))
  expect(within(confirmation).getByRole('button', { name: '确认生成草稿' })).toBeEnabled()
})

it('opens a selected item directly even when neither its issue nor its suggestion is on the current page', async () => {
  const user = userEvent.setup()
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <MemoryRouter><EvolutionTab workflowId="wf" /></MemoryRouter>
  </QueryClientProvider>)
  await user.click(within((await screen.findByText('node-0', { selector: 'span' })).closest('article')!).getByRole('button', { name: '问题详情' }))
  await user.click(screen.getByRole('button', { name: '修复建议' }))
  await user.click(await choices().findByRole('checkbox', { name: '选择 retry' }))
  await user.click(within(screen.getByRole('dialog', { name: '问题详情' })).getByRole('button', { name: '关闭' }))
  await user.click(within(screen.getByRole('region', { name: '问题分页' })).getByRole('button', { name: '下一页' }))
  await screen.findByText('node-20', { selector: 'span' })
  result.items = [item('timeout')]
  await user.click(screen.getByRole('button', { name: '查看已选' }))
  await user.click(within(screen.getByRole('dialog', { name: '已选修复建议' })).getByRole('button', { name: 'retry' }))
  expect(await screen.findByRole('button', { name: '返回此问题的建议列表' })).toBeVisible()
  expect(choices().getByRole('checkbox', { name: '选择 retry' })).toBeChecked()
})

it('pages suggestions independently and preserves explicit selections without selecting the next page', async () => {
  result.items = Array.from({ length: 21 }, (_, i) => item(`suggestion-${i}`))
  const user = userEvent.setup()
  function Harness() {
    const [selected, setSelected] = useState<string[]>([])
    return <IssueRepairSuggestions workflowId="wf" signature="issue-0" includeHistorical={false}
      selected={selected} canEdit onToggle={id => setSelected(old => old.includes(id) ? old.filter(x => x !== id) : [...old, id])}
      onResult={() => {}} renderItem={value => <p>{value.itemId}</p>} />
  }
  render(<QueryClientProvider client={new QueryClient()}><Harness /></QueryClientProvider>)
  await user.click(await choices().findByRole('checkbox', { name: '选择 suggestion-0' }))
  await user.click(screen.getByRole('button', { name: '下一页建议' }))
  expect(await choices().findByRole('checkbox', { name: '选择 suggestion-20' })).not.toBeChecked()
  expect(choices().queryByRole('checkbox', { name: '选择 suggestion-0' })).not.toBeInTheDocument()
  await user.click(choices().getByRole('checkbox', { name: '选择 suggestion-20' }))
  await user.click(screen.getByRole('button', { name: '上一页建议' }))
  expect(await choices().findByRole('checkbox', { name: '选择 suggestion-0' })).toBeChecked()
})
