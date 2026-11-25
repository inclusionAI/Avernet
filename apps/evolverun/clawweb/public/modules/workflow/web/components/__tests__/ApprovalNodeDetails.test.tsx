import { act, cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ApprovalCardSummary } from '@avernet/clawweb-shared/web/api/client'
import ApprovalNodeDetails from '../ApprovalNodeDetails'
import { formatTime } from '../../utils/time'

const FLOW_ID = 'flow-approval'
const NODE_ID = 'review'
const CREATED_AT = 1_795_500_000
const RESOLVED_AT = CREATED_AT + 600

type ApprovalDetail = ApprovalCardSummary & {
  comment?: string | null
  note?: string | null
  detail?: Record<string, unknown>
}

function card(overrides: Partial<ApprovalCardSummary> = {}): ApprovalCardSummary {
  return {
    id: 101,
    flowId: FLOW_ID,
    nodeId: NODE_ID,
    workflowId: 'approval-workflow',
    workflowTitle: '发布审批',
    approvalType: 'HUMAN_CONFIRM',
    message: '请审核发布内容',
    cardFields: [],
    approverIds: ['reviewer-1'],
    approverNames: ['张三'],
    approvalPolicy: 'any',
    approvedBy: [],
    rejectedBy: ['reviewer-1'],
    status: 'rejected',
    deliveryMode: 'card-web',
    createdAt: CREATED_AT,
    resolvedAt: RESOLVED_AT,
    isApprover: false,
    ...overrides,
  }
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function deferredResponse() {
  let resolve!: (response: Response) => void
  const promise = new Promise<Response>((done) => { resolve = done })
  return { promise, resolve }
}

let listCards: ApprovalCardSummary[]
let listReply: (flowId: string) => Promise<Response>
let detailReplies: Map<number, () => Promise<Response>>
let fetchMock: ReturnType<typeof vi.fn<typeof fetch>>
const clients: QueryClient[] = []

function serve(...records: ApprovalCardSummary[]) {
  listCards = records
  for (const record of records) {
    detailReplies.set(record.id, async () => json(record))
  }
}

function detailReply(id: number, detail: Partial<ApprovalDetail>) {
  const summary = listCards.find((record) => record.id === id)
  if (!summary) throw new Error(`Missing summary fixture for approval ${id}`)
  detailReplies.set(id, async () => json({ ...summary, ...detail }))
}

function renderDetails(flowId = FLOW_ID, nodeId = NODE_ID) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: Infinity, refetchOnWindowFocus: false } },
  })
  clients.push(client)
  const view = (flow: string, node: string) => (
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <ApprovalNodeDetails flowId={flow} nodeId={node} />
      </MemoryRouter>
    </QueryClientProvider>
  )
  const rendered = render(view(flowId, nodeId))
  return { ...rendered, rerenderDetails: (flow: string, node: string) => rendered.rerender(view(flow, node)) }
}

function record(id = 101): HTMLElement {
  return screen.getByRole('article', { name: new RegExp(`#${id}\\b`) })
}

function field(id: number, label: string): HTMLElement {
  const term = within(record(id)).getByText(label, { selector: 'dt' })
  const value = term.nextElementSibling
  if (!(value instanceof HTMLElement) || value.tagName !== 'DD') {
    throw new Error(`Missing value for approval field ${label}`)
  }
  return value
}

async function expectDetailLoaded(id = 101) {
  await waitFor(() => {
    expect(fetchMock.mock.calls.some(([input]) => String(input) === `/api/approval/${id}`)).toBe(true)
    expect(within(field(id, '审批意见')).queryByRole('status')).not.toBeInTheDocument()
    expect(within(field(id, '审批意见')).queryByRole('alert')).not.toBeInTheDocument()
  })
}

function detailRequestPaths(): string[] {
  return fetchMock.mock.calls
    .map(([input]) => String(input))
    .filter((url) => /^\/api\/approval\/\d+$/.test(url))
}

beforeEach(() => {
  listCards = []
  detailReplies = new Map()
  listReply = async () => json({ items: listCards })
  fetchMock = vi.fn<typeof fetch>(async (input) => {
    const url = String(input)
    const listMatch = url.match(/^\/api\/approval\/by-flow\/([^?]+)$/)
    if (listMatch) return listReply(decodeURIComponent(listMatch[1]))
    const detailMatch = url.match(/^\/api\/approval\/(\d+)$/)
    const reply = detailMatch && detailReplies.get(Number(detailMatch[1]))
    if (reply) return reply()
    throw new Error(`Unexpected approval request: ${url}`)
  })
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => {
  cleanup()
  for (const client of clients.splice(0)) client.clear()
  vi.unstubAllGlobals()
})

describe('ApprovalNodeDetails', () => {
  it('loads a full rejected record rather than treating summary metadata as the opinion', async () => {
    serve(card())
    const comment = '拒绝发布：缺少验收证据\n请补齐后重新提交。'
    detailReply(101, { comment })

    renderDetails()

    await expectDetailLoaded()
    for (const label of ['审批人', '审批时间', '审批意见', '审批状态', '审批链接']) {
      expect(within(record()).getByText(label, { selector: 'dt' })).toBeInTheDocument()
    }
    expect(field(101, '审批人')).toHaveTextContent('张三')
    expect(field(101, '审批时间')).toHaveTextContent(formatTime(RESOLVED_AT))
    expect(field(101, '审批状态')).toHaveTextContent('已拒绝')
    expect(field(101, '审批意见').textContent).toBe(comment)
    expect(detailRequestPaths()).toEqual(['/api/approval/101'])
  })

  it.each([
    { label: 'named and whitespace-only entries', names: ['  李四  ', ' \t ', ''], expected: ['李四', 'id-2', 'id-3'] },
    { label: 'missing names', names: [], expected: ['id-1', 'id-2', 'id-3'] },
  ])('uses per-person ID fallback for $label', async ({ names, expected }) => {
    serve(card({ approverIds: ['id-1', 'id-2', 'id-3'], approverNames: names }))
    renderDetails()

    await expectDetailLoaded()
    for (const name of expected) expect(field(101, '审批人')).toHaveTextContent(name)
  })

  it.each([
    { label: 'multiline note over a raw structured comment', note: '第一行审核意见\n第二行补充依据', comment: '{"note":"旧意见","detail":{"review":{}}}', detail: { review: {} }, expected: '第一行审核意见\n第二行补充依据' },
    { label: 'plain comment when note is whitespace', note: ' \n\t ', comment: '普通意见第一行\n普通意见第二行', detail: undefined, expected: '普通意见第一行\n普通意见第二行' },
  ])('preserves $label', async ({ note, comment, detail, expected }) => {
    serve(card())
    detailReply(101, { note, comment, detail })
    renderDetails()

    await expectDetailLoaded()
    expect(field(101, '审批意见').textContent).toBe(expected)
    if (note.trim()) expect(field(101, '审批意见')).not.toHaveTextContent(comment)
  })

  it('does not expose a raw structured comment when the API omits its blank note', async () => {
    serve(card())
    const detail = { review: { choice: { actionLabel: '保留', value: '原值' } } }
    const comment = JSON.stringify({ note: '', detail })
    // This is the actual API shape: raw comment + parsed detail, but no truthy note.
    detailReply(101, { comment, detail })
    renderDetails()

    await expectDetailLoaded()
    const opinion = field(101, '审批意见')
    expect(opinion.textContent?.trim()).not.toBe('')
    expect(opinion).not.toHaveTextContent(comment)
    expect(opinion.textContent).not.toMatch(/"(?:note|detail|actionLabel)"\s*:/)
    expect(field(101, '审批状态')).toHaveTextContent('已拒绝')
  })

  it('shows all statuses for the exact node, newest createdAt then highest ID first', async () => {
    const approved = card({ id: 101, status: 'approved', approvedBy: ['reviewer-1'], rejectedBy: [], createdAt: CREATED_AT })
    const rejected = card({ id: 102, createdAt: CREATED_AT + 10 })
    const pending = card({ id: 103, status: 'pending', rejectedBy: [], createdAt: CREATED_AT + 10, resolvedAt: null })
    serve(
      approved,
      card({ id: 201, nodeId: `${NODE_ID}-child`, createdAt: CREATED_AT + 100 }),
      rejected,
      card({ id: 202, nodeId: `pre-${NODE_ID}`, createdAt: CREATED_AT + 200 }),
      pending,
      card({ id: 203, flowId: 'another-flow', createdAt: CREATED_AT + 300 }),
    )
    detailReply(101, { comment: '旧审批已通过' })
    detailReply(102, { comment: '新审批已拒绝' })
    renderDetails()

    await Promise.all([101, 102, 103].map(expectDetailLoaded))
    expect(screen.getAllByRole('article').map((article) => within(article).getByRole('link').getAttribute('href')))
      .toEqual(['/approval/103', '/approval/102', '/approval/101'])
    expect(field(101, '审批状态')).toHaveTextContent('已通过')
    expect(field(102, '审批状态')).toHaveTextContent('已拒绝')
    expect(field(103, '审批状态')).toHaveTextContent('待审批')
    expect(field(101, '审批意见')).toHaveTextContent('旧审批已通过')
    expect(field(102, '审批意见')).toHaveTextContent('新审批已拒绝')
    expect(detailRequestPaths().sort()).toEqual(['/api/approval/101', '/api/approval/102', '/api/approval/103'])
  })

  it('builds same-origin record links and read-only detail requests without embedded credentials', async () => {
    serve(card())
    renderDetails()
    await expectDetailLoaded()

    const link = within(record()).getByRole('link')
    expect(link).toHaveAttribute('href', '/approval/101')
    const url = new URL(link.getAttribute('href')!, window.location.href)
    expect(url.origin).toBe(window.location.origin)
    expect(url.search).toBe('')
    expect(url.username).toBe('')
    expect(url.password).toBe('')
    const [input, init] = fetchMock.mock.calls.find(([request]) => String(request) === '/api/approval/101')!
    expect(String(input)).toBe('/api/approval/101')
    expect(init?.method ?? 'GET').toBe('GET')
    expect(init?.body).toBeUndefined()
    const headers = new Headers(init?.headers)
    expect(headers.get('Accept')).toBe('application/json')
    expect(headers.has('Authorization')).toBe(false)
    expect(headers.has('X-User-Id')).toBe(false)
    for (const [request] of fetchMock.mock.calls) {
      const requestUrl = new URL(String(request), window.location.href)
      for (const param of ['empId', 'authCode', 'token']) {
        expect(requestUrl.searchParams.has(param)).toBe(false)
      }
      expect(requestUrl.pathname).not.toMatch(/\/resolve(?:\/|$)/)
    }
  })

  it.each([RESOLVED_AT, RESOLVED_AT * 1000])('formats card-level resolvedAt %s using formatTime, not creation time', async (resolvedAt) => {
    serve(card({ resolvedAt }))
    renderDetails()
    await expectDetailLoaded()

    expect(field(101, '审批时间')).toHaveTextContent(formatTime(resolvedAt))
    expect(field(101, '审批时间')).not.toHaveTextContent(formatTime(CREATED_AT))
  })

  it('does not invent a resolution time or opinion for an untouched pending card', async () => {
    serve(card({ status: 'pending', rejectedBy: [], resolvedAt: null }))
    detailReply(101, { comment: ' \n ', note: null })
    renderDetails()
    await expectDetailLoaded()

    expect(field(101, '审批状态')).toHaveTextContent('待审批')
    expect(field(101, '审批时间')).not.toHaveTextContent(formatTime(CREATED_AT))
    expect(field(101, '审批时间').textContent).toMatch(/待|—|未/)
    expect(field(101, '审批意见')).toHaveTextContent(/暂无|未填写|无审批意见/)
  })

  it('retains partial decisions and a stored comment while a card is still pending', async () => {
    serve(card({
      status: 'pending',
      approvalPolicy: 'all',
      approverIds: ['first-reviewer', 'second-reviewer'],
      approverNames: ['先审人', '后审人'],
      approvedBy: ['first-reviewer'],
      rejectedBy: [],
      resolvedAt: null,
    }))
    detailReply(101, { comment: '首位审批人已完成核对，等待其他人。' })
    renderDetails()
    await expectDetailLoaded()

    expect(field(101, '审批状态')).toHaveTextContent('待审批')
    expect(field(101, '审批人')).toHaveTextContent('先审人')
    expect(field(101, '审批人')).toHaveTextContent('后审人')
    expect(field(101, '审批人')).toHaveTextContent('已通过')
    expect(field(101, '审批意见')).toHaveTextContent('首位审批人已完成核对，等待其他人。')
    expect(field(101, '审批时间')).not.toHaveTextContent(formatTime(CREATED_AT))
  })

  it('shows list loading without falsely claiming that no records exist', async () => {
    const response = deferredResponse()
    listReply = () => response.promise
    renderDetails()

    expect(await screen.findByRole('status')).toHaveTextContent(/加载/)
    expect(screen.queryByText(/暂无审批记录/)).not.toBeInTheDocument()
    expect(detailRequestPaths()).toEqual([])
    await act(async () => { response.resolve(json({ items: [] })) })
    expect(await screen.findByText(/暂无审批记录/)).toBeInTheDocument()
  })

  it('recovers from a failed list request through the visible retry action', async () => {
    serve(card())
    listReply = async () => json({ message: 'list temporarily unavailable' }, 503)
    renderDetails()

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(/失败/)
    expect(screen.queryByText(/暂无审批记录/)).not.toBeInTheDocument()
    expect(detailRequestPaths()).toEqual([])
    listReply = async () => json({ items: listCards })
    await userEvent.click(within(alert).getByRole('button', { name: /重试/ }))

    await expectDetailLoaded()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(fetchMock.mock.calls.filter(([input]) => String(input).startsWith('/api/approval/by-flow/'))).toHaveLength(2)
  })

  it.each([
    { label: 'empty list', records: [] },
    { label: 'only other-node cards', records: [card({ nodeId: 'other-node' })] },
  ])('shows a scoped empty state for $label without fetching unrelated details', async ({ records }) => {
    serve(...records)
    renderDetails()

    expect(await screen.findByText(/暂无审批记录/)).toBeInTheDocument()
    expect(screen.queryByRole('article')).not.toBeInTheDocument()
    expect(detailRequestPaths()).toEqual([])
  })

  it('distinguishes full-detail loading from a confirmed absent opinion', async () => {
    serve(card())
    const response = deferredResponse()
    detailReplies.set(101, () => response.promise)
    renderDetails()

    expect(await screen.findByRole('status')).toHaveTextContent(/加载/)
    await waitFor(() => expect(detailRequestPaths()).toEqual(['/api/approval/101']))
    expect(within(field(101, '审批意见')).getByRole('status')).toHaveTextContent(/加载/)
    expect(field(101, '审批意见')).not.toHaveTextContent(/暂无|未填写|无审批意见/)
    await act(async () => { response.resolve(json({ ...card(), comment: '稍后返回的真实意见' })) })

    await expectDetailLoaded()
    expect(field(101, '审批意见')).toHaveTextContent('稍后返回的真实意见')
  })

  it('keeps a detail failure visible until retry retrieves the real opinion', async () => {
    serve(card())
    detailReplies.set(101, async () => json({ message: 'detail temporarily unavailable' }, 503))
    renderDetails()

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(/失败/)
    expect(field(101, '审批意见')).not.toHaveTextContent(/暂无|未填写|无审批意见/)
    detailReply(101, { comment: '重试后读到的拒绝原因' })
    await userEvent.click(within(alert).getByRole('button', { name: /重试/ }))

    await expectDetailLoaded()
    expect(field(101, '审批意见')).toHaveTextContent('重试后读到的拒绝原因')
    expect(detailRequestPaths()).toEqual(['/api/approval/101', '/api/approval/101'])
  })

  it('does not retain a previous node opinion when re-rendered before the next detail resolves', async () => {
    serve(card(), card({ id: 102, nodeId: 'second-review' }))
    detailReply(101, { comment: '仅属于第一个节点的意见' })
    const response = deferredResponse()
    detailReplies.set(102, () => response.promise)
    const rendered = renderDetails()
    await expectDetailLoaded(101)
    expect(field(101, '审批意见')).toHaveTextContent('仅属于第一个节点的意见')

    rendered.rerenderDetails(FLOW_ID, 'second-review')
    await waitFor(() => expect(detailRequestPaths()).toContain('/api/approval/102'))
    expect(screen.queryByText('仅属于第一个节点的意见')).not.toBeInTheDocument()
    expect(screen.queryByRole('article', { name: /#101\b/ })).not.toBeInTheDocument()
    await act(async () => { response.resolve(json({ ...card({ id: 102, nodeId: 'second-review' }), comment: '仅属于第二个节点的意见' })) })

    await expectDetailLoaded(102)
    expect(field(102, '审批意见')).toHaveTextContent('仅属于第二个节点的意见')
    expect(screen.queryByText('仅属于第一个节点的意见')).not.toBeInTheDocument()
  })

  it('ignores late old-flow details after switching to another flow with the same node ID', async () => {
    const oldCard = card()
    const newCard = card({ id: 301, flowId: 'new-flow' })
    serve(oldCard, newCard)
    listReply = async (flowId) => json({ items: flowId === FLOW_ID ? [oldCard] : [newCard] })
    const oldResponse = deferredResponse()
    detailReplies.set(101, () => oldResponse.promise)
    detailReplies.set(301, async () => json({ ...newCard, comment: '新流程的审批意见' }))
    const rendered = renderDetails()
    await waitFor(() => expect(detailRequestPaths()).toContain('/api/approval/101'))

    rendered.rerenderDetails('new-flow', NODE_ID)
    await expectDetailLoaded(301)
    await act(async () => { oldResponse.resolve(json({ ...oldCard, comment: '迟到的旧流程意见' })) })

    expect(field(301, '审批意见')).toHaveTextContent('新流程的审批意见')
    expect(screen.queryByText('迟到的旧流程意见')).not.toBeInTheDocument()
    expect(screen.queryByRole('article', { name: /#101\b/ })).not.toBeInTheDocument()
  })
})
