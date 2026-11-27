import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useNavigate, useParams } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import RunSummaryHeader from '../RunSummaryHeader'

const mutations = vi.hoisted(() => ({
  delete: { mutate: vi.fn(), isPending: false },
  rerun: { mutate: vi.fn(), isPending: false },
  abort: { mutate: vi.fn(), isPending: false },
}))

vi.mock('@avernet/clawweb-shared/web/api/hooks', () => ({
  useDeleteFlowRun: () => mutations.delete,
  useRerunFlowRun: () => mutations.rerun,
  useAbortFlowRun: () => mutations.abort,
}))

type Props = Parameters<typeof RunSummaryHeader>[0]

const run: Props['run'] = {
  flow_id: 'current-run', workflow_id: 'example', workflow_title: '示例工作流', status: 'waiting',
  triggered_by: null, node_count: 4, succeeded_count: 1, failed_count: 1,
  total_duration_ms: 300, total_token_usage: 120, started_at: '2026-11-26T00:00:00Z', completed_at: null,
  user_id: 'user', origin_bot_id: 'bot', plugin_version: '1.0', engine: 'example-engine',
  workflow_version: 2, workflow_deploy_number: 1,
  input_json: '{"message":"启动时的需求","params":{"target":"retry"}}',
  params_json: '{"target":"original"}', parent_flow_id: 'parent-run', root_flow_id: null,
  device_id: 'device', bot_type: 'example-bot', stage: 'example-stage',
}

function renderHeader(props: Partial<Props> = {}) {
  return render(<MemoryRouter initialEntries={['/runs/current-run?from=workspace']}>
    <RunSummaryHeader run={run} nodeCount={4} {...props} />
  </MemoryRouter>)
}

beforeEach(() => {
  vi.clearAllMocks()
  for (const mutation of Object.values(mutations)) mutation.isPending = false
})

describe('RunSummaryHeader launch details integration', () => {
  it('shows independent launch data and lineage alongside grouped metadata, progress and token usage', () => {
    renderHeader({ succeededCount: 2, failedCount: 0 })
    expect(screen.getByRole('region', { name: '输入文本内容' })).toHaveTextContent('启动时的需求')
    expect(screen.getByRole('region', { name: '原始启动参数内容' })).toHaveTextContent('original')
    expect(screen.getByRole('link', { name: '查看父运行 parent-run' })).toHaveAttribute('href', '/runs/parent-run?from=workspace')
    const context = within(screen.getByRole('region', { name: '执行上下文' }))
    expect(context.getAllByRole('term').map(term => term.textContent)).toEqual([
      '运行引擎', '流量标识', '标签', 'device_id', 'BOT_TYPE', 'STAGE',
    ])
    expect(screen.getByText('2 成功 / 0 失败 / 4 总计')).toBeInTheDocument()
    expect(screen.getByText('Token 用量', { selector: 'dt' }).parentElement?.querySelector('dd')).toHaveTextContent('120')
  })

  it('keeps metadata columns shrinkable and long Bot/engine/ID values wrappable', () => {
    const botId = `bot-${'b'.repeat(200)}`
    const engine = `engine-${'e'.repeat(200)}`
    const { container } = renderHeader({ run: { ...run, origin_bot_id: botId, engine } })
    for (const term of screen.getAllByRole('term')) {
      expect(term.parentElement).toHaveClass('min-w-0')
      if (term.textContent !== 'Token 用量') {
        expect(term.parentElement?.querySelector('dd')).toHaveClass('break-words', '[overflow-wrap:anywhere]')
      }
    }
    expect(screen.getByText(botId, { exact: false, selector: 'dd' })).toBeInTheDocument()
    expect(screen.getByText('plugin_version · 1.0')).toBeInTheDocument()
    expect(screen.getByText(engine)).toBeInTheDocument()
    expect(container.querySelector('dl')).toHaveClass('grid-cols-2', 'sm:grid-cols-4')
  })

  it('keeps launch details and markers visible while readOnly hides every action', () => {
    const onAutoHeal = vi.fn()
    const onApproval = vi.fn()
    renderHeader({
      readOnly: true, onAutoHeal, onApproval, pendingApprovalCount: 2,
      run: { ...run, env: 'read-only-env', tag: 'read-only-tag' },
    })
    expect(screen.getByRole('region', { name: '输入文本内容' })).toHaveTextContent('启动时的需求')
    expect(screen.getByRole('region', { name: '原始启动参数内容' })).toHaveTextContent('original')
    expect(screen.getByRole('link', { name: '查看父运行 parent-run' })).toHaveAttribute('href', '/runs/parent-run?from=workspace')
    expect(screen.getByText('read-only-env', { selector: 'dd' })).toBeInTheDocument()
    expect(screen.getByText('read-only-tag', { selector: 'dd' })).toBeInTheDocument()
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
    expect(onAutoHeal).not.toHaveBeenCalled()
    expect(onApproval).not.toHaveBeenCalled()
    for (const mutation of Object.values(mutations)) expect(mutation.mutate).not.toHaveBeenCalled()
  })

  it('retains rerun, repair, approval and confirmed abort actions', async () => {
    const user = userEvent.setup()
    const onAutoHeal = vi.fn()
    const onApproval = vi.fn()
    renderHeader({ onAutoHeal, onApproval, pendingApprovalCount: 2 })
    await user.click(screen.getByRole('button', { name: '🔄 重跑' }))
    expect(mutations.rerun.mutate).toHaveBeenCalledExactlyOnceWith('current-run')
    await user.click(screen.getByRole('button', { name: '🩹 修复' }))
    expect(onAutoHeal).toHaveBeenCalledExactlyOnceWith(run)
    await user.click(screen.getByRole('button', { name: /审批/ }))
    expect(onApproval).toHaveBeenCalledTimes(1)
    await user.click(screen.getByRole('button', { name: '⛔ 中止' }))
    expect(mutations.abort.mutate).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: '取消' }))
    expect(screen.getByRole('button', { name: '⛔ 中止' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '⛔ 中止' }))
    await user.click(screen.getByRole('button', { name: '确认中止' }))
    expect(mutations.abort.mutate).toHaveBeenCalledExactlyOnceWith('current-run')
  })

  it('retains delete cancellation and requires explicit confirmation', async () => {
    const user = userEvent.setup()
    renderHeader()
    await user.click(screen.getByRole('button', { name: '🗑 删除' }))
    expect(mutations.delete.mutate).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: '取消' }))
    expect(mutations.delete.mutate).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: '🗑 删除' }))
    await user.click(screen.getByRole('button', { name: '确认删除' }))
    expect(mutations.delete.mutate).toHaveBeenCalledExactlyOnceWith('current-run')
  })

  it('preserves status and origin-based action visibility', () => {
    renderHeader({ run: { ...run, status: 'succeeded', origin_bot_id: null }, pendingApprovalCount: 2, onApproval: vi.fn() })
    expect(screen.queryByRole('button', { name: '🔄 重跑' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '⛔ 中止' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '🩹 修复' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /审批/ })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '🗑 删除' })).toBeInTheDocument()
  })
})


describe('RunSummaryHeader action contract', () => {
  it.each([
    { status: 'running', abort: true, heal: false, approval: false },
    { status: 'waiting', abort: true, heal: true, approval: true },
    { status: 'blocked', abort: true, heal: true, approval: false },
    { status: 'pending', abort: true, heal: false, approval: false },
    { status: 'queued', abort: true, heal: false, approval: false },
    { status: 'postActionsRunning', abort: true, heal: false, approval: false },
    { status: 'failed', abort: false, heal: true, approval: false },
    { status: 'succeeded', abort: false, heal: false, approval: false },
    { status: 'cancelled', abort: false, heal: false, approval: false },
    { status: 'timeout', abort: false, heal: false, approval: false },
    { status: 'skipped', abort: false, heal: false, approval: false },
  ])('retains the original action gates for $status', ({ status, abort, heal, approval }) => {
    // Runtime legacy statuses can be wider than the shared NodeStatus union.
    renderHeader({ run: { ...run, status: status as Props['run']['status'] }, onApproval: vi.fn(), pendingApprovalCount: 2 })
    const actions = within(screen.getByRole('group', { name: '运行操作' }))
    expect(actions.queryAllByRole('button', { name: '⛔ 中止' })).toHaveLength(abort ? 1 : 0)
    expect(actions.queryAllByRole('button', { name: '🩹 修复' })).toHaveLength(heal ? 1 : 0)
    expect(actions.queryAllByRole('button', { name: /审批/ })).toHaveLength(approval ? 1 : 0)
    expect(actions.getByRole('button', { name: '🔄 重跑' })).toBeInTheDocument()
    expect(actions.getByRole('button', { name: '🗑 删除' })).toBeInTheDocument()
    for (const mutation of Object.values(mutations)) expect(mutation.mutate).not.toHaveBeenCalled()
  })

  it.each([
    { count: 0, callback: true, visible: false },
    { count: 1, callback: false, visible: false },
    { count: 1, callback: true, visible: true },
    { count: 2, callback: true, visible: true },
  ])('requires both a pending approval and its callback ($count, $callback)', ({ count, callback, visible }) => {
    renderHeader({ pendingApprovalCount: count, onApproval: callback ? vi.fn() : undefined })
    const buttons = screen.queryAllByRole('button', { name: /审批/ })
    expect(buttons).toHaveLength(visible ? 1 : 0)
    if (visible) expect(buttons[0].querySelector('span')?.textContent ?? '').toBe(count > 1 ? String(count) : '')
  })

  it('keeps mutation pending protections on rerun and confirmed destructive actions', async () => {
    const user = userEvent.setup()
    const view = renderHeader()
    await user.click(screen.getByRole('button', { name: '⛔ 中止' }))
    await user.click(screen.getByRole('button', { name: '🗑 删除' }))
    for (const mutation of Object.values(mutations)) mutation.isPending = true
    view.rerender(<MemoryRouter><RunSummaryHeader run={run} nodeCount={4} /></MemoryRouter>)
    expect(screen.getByTitle('重跑：重新发送原始命令到 Bot')).toBeDisabled()
    expect(screen.getByRole('button', { name: '中止中…' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '删除中…' })).toBeDisabled()
    for (const mutation of Object.values(mutations)) expect(mutation.mutate).not.toHaveBeenCalled()
  })
})

describe('RunSummaryHeader launch layout and safe projection', () => {
  it('keeps independent previews, bounded scrolling and read-only expansion for both sources', async () => {
    const user = userEvent.setup()
    const message = '长输入文本'.repeat(300)
    const params = { original: 'long-original-value'.repeat(100) }
    renderHeader({ readOnly: true, run: { ...run, input_json: JSON.stringify({ message }), params_json: JSON.stringify(params) } })
    const input = screen.getByRole('region', { name: '输入文本内容' })
    const original = screen.getByRole('region', { name: '原始启动参数内容' })
    expect(input.parentElement?.parentElement).toBe(original.parentElement?.parentElement)
    expect(input.parentElement?.parentElement).toHaveClass('grid-cols-1', 'sm:grid-cols-2', 'lg:grid-cols-1', 'xl:grid-cols-2')
    expect(screen.queryByRole('group', { name: '运行操作' })).not.toBeInTheDocument()
    const expandInput = screen.getByRole('button', { name: '展开输入文本' })
    expect(expandInput).toHaveAttribute('aria-controls', input.id)
    expandInput.focus()
    await user.keyboard('{Enter}')
    expect(input).toHaveClass('max-h-96', 'overflow-auto')
    expect(original).toHaveClass('max-h-28', 'overflow-auto')
    await user.click(screen.getByRole('button', { name: '展开原始启动参数' }))
    expect(original).toHaveClass('max-h-96')
    await user.click(screen.getByRole('button', { name: '收起输入文本' }))
    expect(input).toHaveClass('max-h-28')
    expect(original).toHaveClass('max-h-96')
    expect(input.textContent).toBe(message)
    expect(JSON.parse(original.textContent!)).toEqual(params)
    for (const mutation of Object.values(mutations)) expect(mutation.mutate).not.toHaveBeenCalled()
  })

  it('uses the original helper for command fallback and rejects state envelopes and retry params', () => {
    const { container } = renderHeader({
      readOnly: true,
      run: {
        ...run, input_json: JSON.stringify({ command: '/workflow start recorded', params: { secret: 'retry-secret' } }),
        params_json: '{"_clawmind_state":{"secret":"state-secret"}}',
        parent_flow_id: undefined, root_flow_id: undefined,
      },
    })
    expect(screen.getByRole('region', { name: '已存储命令内容' })).toHaveTextContent('/workflow start recorded')
    expect(screen.getByText('flow_runs.input_json · command')).toBeInTheDocument()
    expect(screen.getByText('仅有执行状态，未记录原始启动参数')).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: '原始启动参数内容' })).not.toBeInTheDocument()
    expect(screen.getByLabelText('父运行未记录')).toHaveTextContent('—')
    expect(screen.getByLabelText('根运行未记录')).toHaveTextContent('—')
    expect(screen.queryByRole('link')).not.toBeInTheDocument()
    expect(container.textContent).not.toMatch(/retry-secret|state-secret|_clawmind_state/)
  })

  it('retains authorized encoded lineage links, the router basename and only workspace provenance', () => {
    const parent = 'parent/中文 ?#%&='
    const root = 'javascript:alert(1)//example'
    render(<MemoryRouter basename="/task-escort" initialEntries={['/task-escort/runs/current?from=workspace&analysisId=old&unsafe=1']}>
      <RunSummaryHeader run={{ ...run, parent_flow_id: parent, root_flow_id: root }} nodeCount={4} readOnly />
    </MemoryRouter>)
    expect(screen.getByRole('link', { name: `查看父运行 ${parent}` })).toHaveAttribute('href', `/task-escort/runs/${encodeURIComponent(parent)}?from=workspace`)
    expect(screen.getByRole('link', { name: `查看根运行 ${root}` })).toHaveAttribute('href', `/task-escort/runs/${encodeURIComponent(root)}?from=workspace`)
  })
})


const cachedRuns: Record<string, Props['run']> = {
  'child-run': { ...run, flow_id: 'child-run', parent_flow_id: 'parent-run', root_flow_id: 'root-run' },
  'parent-run': { ...run, flow_id: 'parent-run', parent_flow_id: null, root_flow_id: 'root-run' },
  'root-run': { ...run, flow_id: 'root-run', parent_flow_id: null, root_flow_id: null },
}

function CachedRunHeader() {
  const { flowId } = useParams<{ flowId: string }>()
  const navigate = useNavigate()
  const cachedRun = cachedRuns[flowId!]
  // Cached route data switches synchronously, without unmounting the Header caller.
  return <>
    <output aria-label="当前运行">{cachedRun.flow_id}</output>
    <button type="button" onClick={() => navigate(-1)}>返回上一运行</button>
    <RunSummaryHeader run={cachedRun} nodeCount={cachedRun.node_count} />
  </>
}

describe('RunSummaryHeader run-scoped destructive confirmations', () => {
  it.each([
    { action: 'delete', initial: '🗑 删除', confirm: '确认删除', relation: '父运行', destination: 'parent-run' },
    { action: 'delete', initial: '🗑 删除', confirm: '确认删除', relation: '根运行', destination: 'root-run' },
    { action: 'abort', initial: '⛔ 中止', confirm: '确认中止', relation: '父运行', destination: 'parent-run' },
    { action: 'abort', initial: '⛔ 中止', confirm: '确认中止', relation: '根运行', destination: 'root-run' },
  ] as const)('requires fresh $action authorization after cached navigation to $destination and back', async ({ action, initial, confirm, relation, destination }) => {
    const user = userEvent.setup()
    render(<MemoryRouter initialEntries={['/runs/child-run?from=workspace']}>
      <Routes><Route path="/runs/:flowId" element={<CachedRunHeader />} /></Routes>
    </MemoryRouter>)

    await user.click(screen.getByRole('button', { name: initial }))
    expect(screen.getByRole('button', { name: confirm })).toBeInTheDocument()
    expect(mutations[action].mutate).not.toHaveBeenCalled()

    await user.click(screen.getByRole('link', { name: `查看${relation} ${destination}` }))
    expect(screen.getByLabelText('当前运行')).toHaveTextContent(destination)
    expect(screen.queryByRole('button', { name: confirm })).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: initial }))
    expect(screen.getByRole('button', { name: confirm })).toBeInTheDocument()
    expect(mutations[action].mutate).not.toHaveBeenCalled()

    // Neither the child nor the destination may resurrect an earlier armed action.
    await user.click(screen.getByRole('button', { name: '返回上一运行' }))
    expect(screen.getByLabelText('当前运行')).toHaveTextContent('child-run')
    expect(screen.queryByRole('button', { name: confirm })).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: initial }))
    expect(mutations[action].mutate).not.toHaveBeenCalled()
    await user.click(screen.getByRole('link', { name: `查看${relation} ${destination}` }))
    expect(screen.queryByRole('button', { name: confirm })).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: initial }))
    expect(mutations[action].mutate).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: confirm }))
    expect(mutations[action].mutate).toHaveBeenCalledExactlyOnceWith(destination)
    expect(mutations[action === 'delete' ? 'abort' : 'delete'].mutate).not.toHaveBeenCalled()
  })
})
