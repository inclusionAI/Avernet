import { useState } from 'react'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, afterEach, expect, it, vi } from 'vitest'
import EvolutionTab from '../../EvolutionTab'
import IssueRepairSuggestions from '../IssueRepairSuggestions'
import type { RepairCandidatesResponse, RepairInboxItem } from '../../../../../server/contracts/repair-workbench'

vi.mock('../../../../api/hooks', () => ({
  useWorkflowAccess: () => ({ data: { canEdit: true } }),
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
const group = (i: number) => ({ workflowId: 'wf', signature: `issue-${i}`, inputDigest: String(i),
  flowIds: [`run-${i}`], aggregationStatus: 'not_generated', aggregationId: null, summary: null, stale: false,
  sources: [{ sourceId: String(i), flowId: `run-${i}`, flowIds: [`run-${i}`], analysisId: 'an', diagnosisId: String(i),
    nodeId: `node-${i}`, failureSignature: `issue-${i}`, failureMode: i === 20 ? 'error' : 'timeout', reasoning: 'slow', completedAtMs: i + 1, evidenceEventIds: [] }],
})
beforeEach(() => {
  sessionStorage.clear(); requests.length = 0
  result.items = [item('retry'), item('timeout')]
  vi.stubGlobal('fetch', vi.fn(async (raw: string) => {
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
    } else if (url.pathname.endsWith('/candidates')) {
      const page = Number(url.searchParams.get('page') ?? 1)
      const pageSize = Number(url.searchParams.get('pageSize') ?? 20)
      body = { ...result, items: result.items.slice((page - 1) * pageSize, page * pageSize),
        page: { page, pageSize, total: result.items.length, totalPages: Math.ceil(result.items.length / pageSize) } }
    }
    else if (url.pathname.includes('/items/')) body = item(url.pathname.split('/').at(-1)!)
    else throw new Error(`Unexpected request: ${url.pathname}`)
    return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })
  }))
})
afterEach(() => vi.unstubAllGlobals())

it('turns the issue page, changes the actual problem rows, and resets it on filtering without reading candidates', async () => {
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
  expect(requests.some(url => url.pathname.endsWith('/candidates'))).toBe(false)
  await user.selectOptions(screen.getByRole('combobox', { name: '问题模式' }), 'error')
  expect(await screen.findByText('没有符合当前筛选条件的问题')).toBeVisible()
  await user.selectOptions(screen.getByRole('combobox', { name: '问题模式' }), 'all')
  expect(await screen.findByText('node-3', { selector: 'span' })).toBeVisible()
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
  render(<Harness />)
  await user.click(await screen.findByRole('checkbox', { name: '选择 retry' }))
  await user.click(screen.getByRole('checkbox', { name: '选择 timeout' }))
  expect(screen.getByRole('checkbox', { name: '选择 retry' })).toBeChecked()
  expect(screen.getByRole('checkbox', { name: '选择 timeout' })).toBeChecked()
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
  await user.click((await screen.findAllByRole('button', { name: '问题详情' }))[0])
  await user.click(screen.getByRole('button', { name: '修复建议' }))
  await user.click(await screen.findByRole('checkbox', { name: '选择 retry' }))
  await user.click(screen.getByRole('checkbox', { name: '选择 timeout' }))
  await user.click(within(screen.getByRole('dialog', { name: '问题详情' })).getByRole('button', { name: '关闭' }))
  await user.click(within(screen.getByRole('region', { name: '问题分页' })).getByRole('button', { name: '下一页' }))
  await user.click(screen.getByRole('button', { name: '生成 Pack 草稿（2）' }))
  const confirmation = screen.getByRole('dialog', { name: '生成 Pack 草稿' })
  expect(within(confirmation).getByText('retry')).toBeVisible()
  expect(within(confirmation).getByText('timeout')).toBeVisible()
  expect(within(confirmation).getByRole('button', { name: '确认生成' })).toBeEnabled()
})

it('requires a choice between conflicting modifications before draft generation', async () => {
  result.items = [600, 90].map(value => ({ ...item(String(value)),
    proposal: { summary: String(value), operations: [{ nodeId: 'fetch', path: '/executor/timeoutMs', value }] } }))
  const user = userEvent.setup()
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <MemoryRouter><EvolutionTab workflowId="wf" /></MemoryRouter>
  </QueryClientProvider>)
  await user.click((await screen.findAllByRole('button', { name: '问题详情' }))[0])
  await user.click(screen.getByRole('button', { name: '修复建议' }))
  await user.click(await screen.findByRole('checkbox', { name: '选择 600' }))
  await user.click(screen.getByRole('checkbox', { name: '选择 90' }))
  await user.click(within(screen.getByRole('dialog', { name: '问题详情' })).getByRole('button', { name: '关闭' }))
  await user.click(screen.getByRole('button', { name: '生成 Pack 草稿（2）' }))
  const confirmation = screen.getByRole('dialog', { name: '生成 Pack 草稿' })
  expect(within(confirmation).getByRole('alert')).toHaveTextContent('同一配置')
  expect(within(confirmation).getByRole('button', { name: '确认生成' })).toBeDisabled()
  await user.click(within(confirmation).getByRole('button', { name: '移除 90' }))
  expect(within(confirmation).getByRole('button', { name: '确认生成' })).toBeEnabled()
})

it('opens a selected item directly even when neither its issue nor its suggestion is on the current page', async () => {
  const user = userEvent.setup()
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <MemoryRouter><EvolutionTab workflowId="wf" /></MemoryRouter>
  </QueryClientProvider>)
  await user.click((await screen.findAllByRole('button', { name: '问题详情' }))[0])
  await user.click(screen.getByRole('button', { name: '修复建议' }))
  await user.click(await screen.findByRole('checkbox', { name: '选择 retry' }))
  await user.click(within(screen.getByRole('dialog', { name: '问题详情' })).getByRole('button', { name: '关闭' }))
  await user.click(within(screen.getByRole('region', { name: '问题分页' })).getByRole('button', { name: '下一页' }))
  await screen.findByText('node-20', { selector: 'span' })
  result.items = [item('timeout')]
  await user.click(screen.getByRole('button', { name: '查看已选' }))
  await user.click(within(screen.getByRole('dialog', { name: '已选修复建议' })).getByRole('button', { name: 'retry' }))
  expect(await screen.findByRole('button', { name: '返回此问题的建议列表' })).toBeVisible()
  expect(screen.getByRole('checkbox', { name: '选择 retry' })).toBeChecked()
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
  render(<Harness />)
  await user.click(await screen.findByRole('checkbox', { name: '选择 suggestion-0' }))
  await user.click(screen.getByRole('button', { name: '下一页建议' }))
  expect(await screen.findByRole('checkbox', { name: '选择 suggestion-20' })).not.toBeChecked()
  expect(screen.queryByRole('checkbox', { name: '选择 suggestion-0' })).not.toBeInTheDocument()
  await user.click(screen.getByRole('checkbox', { name: '选择 suggestion-20' }))
  await user.click(screen.getByRole('button', { name: '上一页建议' }))
  expect(await screen.findByRole('checkbox', { name: '选择 suggestion-0' })).toBeChecked()
})
