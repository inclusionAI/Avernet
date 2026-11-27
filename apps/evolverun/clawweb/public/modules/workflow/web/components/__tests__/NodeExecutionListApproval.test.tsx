import { useState } from 'react'
import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ApprovalCardSummary } from '@avernet/clawweb-shared/web/api/client'
import type { NodeExecution } from '@avernet/clawweb-shared/web/types'
import NodeExecutionList from '../NodeExecutionList'
import { formatTime } from '../../utils/time'

const FLOW_ID = 'flow-inline-approval'
const CREATED_AT = 1_795_500_000
const RESOLVED_AT = CREATED_AT + 900

function node(overrides: Partial<NodeExecution> = {}): NodeExecution {
  return {
    flow_id: FLOW_ID,
    node_id: 'review',
    node_title: '发布审核节点',
    executor_type: 'approval',
    triggered_by: null,
    phase: null,
    branch_id: null,
    session_key: null,
    session_id: null,
    embedded_session_key: null,
    status: 'succeeded',
    started_at: String(CREATED_AT),
    completed_at: String(RESOLVED_AT + 120),
    duration_ms: 1020,
    input_json: null,
    output_json: '{"approved":false}',
    error_text: null,
    token_usage_json: null,
    system_context_json: null,
    progress_message: null,
    resolved_prompt: null,
    attempt: 1,
    ...overrides,
  }
}

function card(overrides: Partial<ApprovalCardSummary> = {}): ApprovalCardSummary {
  return {
    id: 401,
    flowId: FLOW_ID,
    nodeId: 'review',
    workflowId: 'inline-approval-workflow',
    workflowTitle: '审批流程',
    approvalType: 'HUMAN_CONFIRM',
    message: '请审核',
    cardFields: [],
    approverIds: ['reviewer-1'],
    approverNames: ['王五'],
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

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), { headers: { 'Content-Type': 'application/json' } })
}

let cards: ApprovalCardSummary[]
let comments: Map<number, string>
let fetchMock: ReturnType<typeof vi.fn<typeof fetch>>
const clients: QueryClient[] = []

function renderList(nodes: NodeExecution[], initialSelectedNodeId?: string, readOnly = false) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: Infinity, refetchOnWindowFocus: false } },
  })
  clients.push(client)
  const onSelectNode = vi.fn()
  const onApproval = vi.fn()
  function ListHarness() {
    const [selectedNodeId, setSelectedNodeId] = useState(initialSelectedNodeId)
    return (
      <NodeExecutionList
        nodes={nodes}
        selectedNodeId={selectedNodeId}
        onSelectNode={(id) => {
          onSelectNode(id)
          setSelectedNodeId((previous) => previous === id ? undefined : id)
        }}
        onApproval={onApproval}
        approvalNodeIds={new Set(cards.map((approval) => approval.nodeId))}
        readOnly={readOnly}
      />
    )
  }
  return {
    ...render(
      <QueryClientProvider client={client}>
        <MemoryRouter><ListHarness /></MemoryRouter>
      </QueryClientProvider>,
    ),
    onSelectNode,
    onApproval,
  }
}

function nodeRow(title = '发布审核节点'): HTMLTableRowElement {
  const row = screen.getByText(title).closest('tr')
  if (!row) throw new Error(`Node row not found: ${title}`)
  return row
}

function detailRequestPaths(): string[] {
  return fetchMock.mock.calls.map(([input]) => String(input)).filter((url) => /^\/api\/approval\/\d+$/.test(url))
}

beforeEach(() => {
  cards = [card()]
  comments = new Map([[401, '拒绝上线：验收记录不完整\n补充测试证据后再提交。']])
  fetchMock = vi.fn<typeof fetch>(async (input) => {
    const url = String(input)
    if (url === `/api/approval/by-flow/${FLOW_ID}`) return json({ items: cards })
    const detail = url.match(/^\/api\/approval\/(\d+)$/)
    const approval = detail && cards.find((candidate) => candidate.id === Number(detail[1]))
    if (approval) return json({ ...approval, comment: comments.get(approval.id) })
    throw new Error(`Unexpected inline approval request: ${url}`)
  })
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => {
  cleanup()
  for (const client of clients.splice(0)) client.clear()
  vi.unstubAllGlobals()
})

describe('NodeExecutionList inline approval details', () => {
  it.each(['row', 'executor cell'] as const)('opens matching rejected details from the %s even though the node succeeded', async (target) => {
    cards.push(card({ id: 402, nodeId: 'review-next', approverNames: ['不相关审批人'] }))
    comments.set(402, '不能出现在当前节点的意见')
    const rendered = renderList([node()])
    const row = nodeRow()
    expect(within(row).getByText(/成功/)).toBeInTheDocument()
    expect(fetchMock).not.toHaveBeenCalled()

    await userEvent.click(target === 'row' ? row : within(row).getAllByRole('cell')[2])

    expect(await screen.findByText(comments.get(401)!, { exact: false, normalizer: (text) => text })).toBeInTheDocument()
    const details = screen.getByRole('region', { name: '审批详情' })
    expect(within(details).getByText('王五')).toBeInTheDocument()
    expect(within(details).getByText('已拒绝', { exact: true })).toBeInTheDocument()
    expect(within(details).getByText(formatTime(RESOLVED_AT), { exact: true })).toBeInTheDocument()
    expect(within(details).getByRole('link')).toHaveAttribute('href', '/approval/401')
    expect(within(row).getByText(/成功/)).toBeInTheDocument()
    expect(screen.queryByText('不能出现在当前节点的意见')).not.toBeInTheDocument()
    expect(screen.queryByText('不相关审批人')).not.toBeInTheDocument()
    expect(detailRequestPaths()).toEqual(['/api/approval/401'])
    expect(rendered.onSelectNode).toHaveBeenCalledExactlyOnceWith('review')
    expect(rendered.onApproval).not.toHaveBeenCalled()
  })

  it('keeps pending approval details read-only without approval navigation or actions', async () => {
    cards = [card({ status: 'pending', rejectedBy: [], resolvedAt: null, isApprover: true })]
    const comment = '等待复核的审批意见'
    comments.set(401, comment)
    const rendered = renderList([node({ status: 'waiting' })], undefined, true)

    expect(fetchMock).not.toHaveBeenCalled()
    await userEvent.click(nodeRow())

    expect(await screen.findByText(comment)).toBeInTheDocument()
    const details = screen.getByRole('region', { name: '审批详情' })
    for (const [label, value] of [
      ['审批人', '王五'],
      ['审批时间', '待审批'],
      ['审批意见', comment],
      ['审批状态', '待审批'],
      ['审批链接', '只读视图不提供审批入口'],
    ]) {
      const term = within(details).getByText(label, { selector: 'dt' })
      expect(term).toBeInTheDocument()
      expect(term.nextElementSibling).toHaveTextContent(value)
    }
    expect(within(details).queryByRole('link')).not.toBeInTheDocument()
    expect(rendered.container.querySelector('a[href^="/approval/"]')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /审批/ })).not.toBeInTheDocument()
    expect(rendered.onSelectNode).toHaveBeenCalledExactlyOnceWith('review')
    expect(rendered.onApproval).not.toHaveBeenCalled()
    expect(fetchMock.mock.calls.map(([input]) => String(input))).toEqual([
      `/api/approval/by-flow/${FLOW_ID}`,
      '/api/approval/401',
    ])
    for (const [, init] of fetchMock.mock.calls) {
      expect(init?.method ?? 'GET').toBe('GET')
      expect(init?.body).toBeUndefined()
    }
  })

  it('does not mount or fetch approval details while the approval row is collapsed', async () => {
    renderList([node(), node({ node_id: 'ordinary', node_title: '普通动作节点', executor_type: 'action' })])
    expect(screen.queryByRole('region', { name: '审批详情' })).not.toBeInTheDocument()
    expect(fetchMock).not.toHaveBeenCalled()

    await userEvent.click(nodeRow('普通动作节点'))

    expect(screen.getByText('节点详情')).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: '审批详情' })).not.toBeInTheDocument()
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it.each(['action', 'human', 'done', 'baas-call'])('does not fetch approvals for an expanded %s node, even with a same-ID card', async (executor_type) => {
    renderList([node({ executor_type })], 'review')

    expect(screen.getByText('节点详情')).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: '审批详情' })).not.toBeInTheDocument()
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('unmounts loaded approval details when the row is collapsed again', async () => {
    renderList([node()])
    await userEvent.click(nodeRow())
    expect(await screen.findByText(comments.get(401)!, { normalizer: (text) => text })).toBeInTheDocument()
    const previousRequests = fetchMock.mock.calls.length

    await userEvent.click(nodeRow())

    expect(screen.queryByText('节点详情')).not.toBeInTheDocument()
    expect(screen.queryByRole('region', { name: '审批详情' })).not.toBeInTheDocument()
    expect(screen.queryByText(comments.get(401)!, { normalizer: (text) => text })).not.toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledTimes(previousRequests)
  })

  it('keeps opinions isolated while selecting between multiple approval nodes', async () => {
    const secondNode = node({ node_id: 'second-review', node_title: '二次审核节点' })
    cards.push(card({ id: 402, nodeId: secondNode.node_id, status: 'approved', approvedBy: ['reviewer-2'], rejectedBy: [], approverIds: ['reviewer-2'], approverNames: ['赵六'] }))
    comments.set(402, '第二节点同意发布')
    renderList([node(), secondNode])

    await userEvent.click(nodeRow())
    expect(await screen.findByText(comments.get(401)!, { normalizer: (text) => text })).toBeInTheDocument()
    expect(detailRequestPaths()).toEqual(['/api/approval/401'])
    await userEvent.click(nodeRow('二次审核节点'))

    expect(await screen.findByText('第二节点同意发布')).toBeInTheDocument()
    const details = screen.getByRole('region', { name: '审批详情' })
    expect(within(details).getByText('赵六')).toBeInTheDocument()
    expect(within(details).getByText('已通过', { exact: true })).toBeInTheDocument()
    expect(within(details).getByRole('link')).toHaveAttribute('href', '/approval/402')
    expect(screen.queryByText(comments.get(401)!, { normalizer: (text) => text })).not.toBeInTheDocument()
    expect(within(details).queryByText('王五')).not.toBeInTheDocument()
    expect(detailRequestPaths().sort()).toEqual(['/api/approval/401', '/api/approval/402'])
  })

  it('shows every matching card without inventing a card-to-attempt association', async () => {
    cards = [
      card({ id: 401, createdAt: CREATED_AT }),
      card({ id: 403, createdAt: CREATED_AT + 20, status: 'pending', resolvedAt: null, rejectedBy: [] }),
      card({ id: 402, createdAt: CREATED_AT + 20, status: 'approved', approvedBy: ['reviewer-1'], rejectedBy: [] }),
      card({ id: 404, nodeId: 'review-other', createdAt: CREATED_AT + 30 }),
    ]
    comments.set(402, '历史审批通过意见')
    renderList([node({ attempt: 3 })])

    await userEvent.click(nodeRow())
    await waitFor(() => expect(detailRequestPaths()).toHaveLength(3))
    expect(await screen.findByText('历史审批通过意见')).toBeInTheDocument()

    const details = screen.getByRole('region', { name: '审批详情' })
    expect(within(details).getAllByRole('link').map((link) => link.getAttribute('href')))
      .toEqual(['/approval/403', '/approval/402', '/approval/401'])
    expect(within(details).getByText('已拒绝', { exact: true })).toBeInTheDocument()
    expect(within(details).getByText('已通过', { exact: true })).toBeInTheDocument()
    expect(within(details).getAllByText('待审批', { exact: true }).length).toBeGreaterThan(0)
    expect(detailRequestPaths().sort()).toEqual(['/api/approval/401', '/api/approval/402', '/api/approval/403'])
  })
})
