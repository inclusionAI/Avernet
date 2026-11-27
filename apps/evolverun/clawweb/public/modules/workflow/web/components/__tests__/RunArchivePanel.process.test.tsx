import { cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import RunArchivePanel from '../RunArchivePanel'
import { ARCHIVE_FLOW_ID, ARCHIVE_NODE_STATUSES, ARCHIVE_TIMESTAMP_MS, ARCHIVE_TIMESTAMP_SECONDS, createArchiveFixture } from './RunArchivePanel.fixture'

const { useRunArchive } = vi.hoisted(() => ({ useRunArchive: vi.fn() }))
vi.mock('@avernet/workflow/web/api/hooks', () => ({ useRunArchive }))
afterEach(cleanup)

async function openProcess(view: RegExp, data = createArchiveFixture()) {
  const user = userEvent.setup()
  useRunArchive.mockReturnValue({ data, isLoading: false, isError: false, error: null })
  const result = render(<RunArchivePanel flowId={ARCHIVE_FLOW_ID} runStatus="failed" />)
  await user.click(screen.getByRole('button', { name: /^运行过程$/ }))
  await user.click(screen.getByRole('button', { name: view }))
  return { ...result, user }
}

function definition(label: RegExp) {
  const term = screen.getByText(label)
  return term.parentElement!.querySelector('dd') ?? term.nextElementSibling!
}

async function selectMatching(user: ReturnType<typeof userEvent.setup>, select: HTMLElement, name: RegExp) {
  const option = within(select).getByRole('option', { name })
  await user.selectOptions(select, option)
}

function searchField(name: RegExp) {
  return screen.queryByRole('searchbox', { name }) ?? screen.getByRole('textbox', { name })
}

const statusLabels: Record<string, RegExp> = {
  succeeded: /^(?:执行)?成功$|^succeeded$/i,
  failed: /^(?:执行)?失败$|^failed$/i,
  skipped: /^(?:已)?跳过$|^skipped$/i,
  running: /^运行中$|^执行中$|^running$/i,
  cancelled: /^(?:已)?(?:取消|中止)$|^cancelled$/i,
  pending: /^待执行$|^等待(?:中)?$|^pending$/i,
  timeout: /^(?:已)?超时$|^timeout$/i,
  unknown: /^未知$|^unknown$/i,
}

describe('RunArchivePanel process information', () => {
  it.each([
    ['missing', null],
    ['invalid', { started_at: 'not-a-date', completed_at: 'invalid-time', duration_ms: 'not-a-duration', total_duration_ms: 'not-a-duration' }],
  ])('handles %s process information without NaN or Invalid Date', async (_name, flowRun) => {
    const { container } = await openProcess(/流程信息/, createArchiveFixture({ flowRun }))
    expect(container).not.toHaveTextContent(/NaN|Invalid Date|undefined/)
    expect(screen.getByRole('heading', { level: 2, name: /运行过程/ })).toBeVisible()
    expect(definition(/^(?:流程)?节点总数$/)).toHaveTextContent(/未记录|未知|不可用|—|-/)
    expect(container).not.toHaveTextContent(/未执行.*跳过|无记录.*跳过/)
  })

  it.each([ARCHIVE_TIMESTAMP_SECONDS, ARCHIVE_TIMESTAMP_MS])('formats numeric timestamp %s and preserves a zero duration', async (timestamp) => {
    const data = createArchiveFixture()
    data.flowRun = { ...data.flowRun, started_at: timestamp, completed_at: timestamp, duration_ms: 0, total_duration_ms: 0 }
    const { container } = await openProcess(/流程信息/, data)
    expect(container).not.toHaveTextContent(/NaN|Invalid Date/)
    const local = new Date(ARCHIVE_TIMESTAMP_MS)
    const clock = new RegExp(`0?${local.getHours()}:${String(local.getMinutes()).padStart(2, '0')}:${String(local.getSeconds()).padStart(2, '0')}`)
    expect(definition(/开始时间/)).toHaveTextContent(clock)
    expect(definition(/结束时间/)).toHaveTextContent(clock)
    expect(definition(/耗时/)).toHaveTextContent(/0\s*(?:ms|毫秒)/i)
    expect(definition(/^(?:流程)?节点总数$/)).toHaveTextContent(/^12$/)
  })

  it('accepts ISO and SQL-style timestamps without leaking invalid date text', async () => {
    const data = createArchiveFixture()
    data.flowRun = { ...data.flowRun, started_at: '2026-11-26T08:10:11Z', completed_at: '2026-11-26 08:10:12' }
    const { container } = await openProcess(/流程信息/, data)
    expect(container).not.toHaveTextContent(/NaN|Invalid Date/)
    expect(definition(/开始时间/)).toHaveTextContent(/\d{1,2}:\d{2}:\d{2}/)
    expect(definition(/结束时间/)).toHaveTextContent(/\d{1,2}:\d{2}:\d{2}/)
  })
})

describe('RunArchivePanel node execution evidence', () => {
  it('accurately distinguishes every collected status instead of treating all non-failures as success', async () => {
    await openProcess(/节点执行/)
    for (const status of ARCHIVE_NODE_STATUSES) {
      const row = screen.getByRole('row', { name: new RegExp(`node-${status}`) })
      expect(within(row).getByText(statusLabels[status])).toBeVisible()
      if (status !== 'succeeded') expect(within(row).queryByText(/^成功(?:[（(]|$)|^succeeded(?:[（(]|$)/i)).not.toBeInTheDocument()
    }
  })

  it('filters success to succeeded only and explicitly reports unmatched node searches', async () => {
    const { user } = await openProcess(/节点执行/)
    await selectMatching(user, screen.getByRole('combobox', { name: /节点状态/ }), /^成功(?:[（(]|$)|^succeeded(?:[（(]|$)/i)
    expect(screen.getByRole('row', { name: /node-succeeded/ })).toBeVisible()
    for (const status of ARCHIVE_NODE_STATUSES.filter(value => value !== 'succeeded')) {
      expect(screen.queryByRole('row', { name: new RegExp(`node-${status}`) })).not.toBeInTheDocument()
    }
    await user.type(searchField(/搜索节点/), 'no-matching-node')
    expect(screen.queryByRole('row', { name: /node-succeeded/ })).not.toBeInTheDocument()
    expect(screen.getByRole('heading', { name: /无.*匹配|没有.*匹配|未找到|无.*结果/ })).toBeVisible()
    await user.clear(searchField(/搜索节点/))
    expect(screen.getByRole('row', { name: /node-succeeded/ })).toBeVisible()
  })

  it.each([
    ['prepend', 'record ID'], ['reorder', 'record ID'],
    ['prepend', 'fallback identity'], ['reorder', 'fallback identity'],
  ] as const)('does not move expanded details after a same-flow %s refresh with %s', async (change, identity) => {
    const data = createArchiveFixture()
    if (identity === 'fallback identity') {
      for (const node of data.nodeExecutions) delete node.id
    }
    const { user, rerender } = await openProcess(/节点执行/, data)
    await user.click(screen.getByRole('button', { name: /查看节点 node-succeeded/ }))
    expect(screen.getByLabelText(/^节点 node-succeeded Output$/)).toHaveTextContent('output-for-succeeded')

    const inserted = {
      ...data.nodeExecutions[0], id: 1001, node_id: 'node-inserted', node_title: 'Newly collected execution',
      output_json: JSON.stringify({ result: 'output-for-inserted' }),
    }
    if (identity === 'fallback identity') delete inserted.id
    const reordered = change === 'prepend'
      ? [inserted, ...data.nodeExecutions]
      : [data.nodeExecutions[1], data.nodeExecutions[0], ...data.nodeExecutions.slice(2)]
    const refreshed = createArchiveFixture({
      ...data,
      // A real refetch parses fresh row objects; object references alone are not identity.
      nodeExecutions: reordered.map(node => ({ ...node })),
    })
    useRunArchive.mockReturnValue({ data: refreshed, isLoading: false, isError: false, error: null })
    rerender(<RunArchivePanel flowId={ARCHIVE_FLOW_ID} runStatus="failed" />)

    const original = screen.getByRole('button', { name: /查看节点 node-succeeded/ })
    const newFirst = screen.getByRole('button', { name: change === 'prepend' ? /查看节点 node-inserted/ : /查看节点 node-failed/ })
    expect(newFirst).toHaveAttribute('aria-expanded', 'false')
    const expanded = screen.queryAllByRole('button', { name: /查看节点/, expanded: true })
    // Retaining A or safely collapsing everything is valid; silently choosing B is not.
    expect(expanded.length).toBeLessThanOrEqual(1)
    for (const button of expanded) expect(button).toBe(original)
    if (original.getAttribute('aria-expanded') === 'true') {
      expect(screen.getByLabelText(/^节点 node-succeeded Output$/)).toBeVisible()
      expect(screen.getByLabelText(/^节点 node-succeeded Output$/)).toHaveTextContent('output-for-succeeded')
    } else {
      expect(original).toHaveAttribute('aria-expanded', 'false')
      expect(screen.queryByLabelText(/^节点 node-succeeded Output$/)).not.toBeInTheDocument()
    }
  })

  it('safely collapses ambiguous ID-less records rather than transferring detail selection on refetch', async () => {
    const data = createArchiveFixture()
    const original: (typeof data.nodeExecutions)[number] = { ...data.nodeExecutions[0], node_title: 'Original synthetic execution' }
    delete original.id
    const other = { ...original, node_title: 'Other synthetic execution', output_json: JSON.stringify({ result: 'output-for-other' }) }
    data.nodeExecutions = [original, other]
    const { user, rerender } = await openProcess(/节点执行/, data)
    const originalRow = screen.getByText('Original synthetic execution').closest('tr')!
    await user.click(within(originalRow).getByRole('button', { name: /查看节点/ }))
    expect(screen.getByLabelText(/^节点 node-succeeded Output$/)).toHaveTextContent('output-for-succeeded')
    const otherRow = screen.getByText('Other synthetic execution').closest('tr')!
    expect(within(otherRow).getByRole('button', { name: /查看节点/ })).toHaveAttribute('aria-expanded', 'false')

    // Both rows are valid projections but lack a unique ID/composite. Fresh objects
    // cannot identify the previous selection safely, so neither should inherit it.
    const refreshed = createArchiveFixture({ ...data, nodeExecutions: [{ ...other }, { ...original }] })
    useRunArchive.mockReturnValue({ data: refreshed, isLoading: false, isError: false, error: null })
    rerender(<RunArchivePanel flowId={ARCHIVE_FLOW_ID} runStatus="failed" />)
    for (const button of screen.getAllByRole('button', { name: /查看节点/ })) {
      expect(button).toHaveAttribute('aria-expanded', 'false')
    }
    expect(screen.queryAllByRole('button', { name: /查看节点/, expanded: true })).toHaveLength(0)
    expect(screen.queryByLabelText(/^节点 node-succeeded Output$/)).not.toBeInTheDocument()
  })

  it('expands and collapses node details through a native keyboard-operable button', async () => {
    const { user } = await openProcess(/节点执行/)
    const button = screen.getByRole('button', { name: /查看节点 node-succeeded/ })
    expect(button.tagName).toBe('BUTTON')
    expect(button).toHaveAttribute('aria-expanded', 'false')
    button.focus()
    await user.keyboard('{Enter}')
    expect(button).toHaveAttribute('aria-expanded', 'true')
    const controlledId = button.getAttribute('aria-controls')
    expect(controlledId).toBeTruthy()
    expect(document.getElementById(controlledId!)).toBeVisible()
    expect(screen.getByLabelText(/^节点 node-succeeded Output$/)).toBeVisible()
    expect(screen.getByLabelText(/^节点 node-succeeded Output$/)).toHaveTextContent('output-for-succeeded')
    await user.keyboard(' ')
    expect(button).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByLabelText(/^节点 node-succeeded Output$/)).not.toBeInTheDocument()
  })
})

describe('RunArchivePanel event and archived log views', () => {
  it('shows an explicit event empty state', async () => {
    await openProcess(/事件流/, createArchiveFixture({ flowEvents: [] }))
    expect(screen.getByRole('heading', { name: /暂无.*事件|未.*(?:采集|记录).*事件|没有.*事件/ })).toBeVisible()
  })

  it('filters events and explicitly reports a search without matches', async () => {
    const { user } = await openProcess(/事件流/)
    await selectMatching(user, screen.getByRole('combobox', { name: /事件类型/ }), /失败|错误|error/i)
    expect(screen.getByText('node_failed')).toBeVisible()
    expect(screen.queryByText('node_started')).not.toBeInTheDocument()
    expect(screen.queryByText('node_completed')).not.toBeInTheDocument()
    await user.type(searchField(/搜索事件/), 'no-matching-event')
    expect(screen.getByRole('heading', { name: /无.*匹配|没有.*匹配|未找到|无.*结果/ })).toBeVisible()
    expect(screen.queryByText('node_failed')).not.toBeInTheDocument()
  })

  it('retains the archive-local runtime logs, filters levels, and searches the collected messages', async () => {
    const { user } = await openProcess(/运行日志/)
    expect(screen.getByText(/Synthetic startup marker/)).toBeVisible()
    expect(screen.getByText(/Synthetic retry marker/)).toBeVisible()
    expect(screen.getByText(/Synthetic error marker/)).toBeVisible()
    await selectMatching(user, screen.getByRole('combobox', { name: /日志等级/ }), /^ERROR$/i)
    expect(screen.getByText(/Synthetic error marker/)).toBeVisible()
    expect(screen.queryByText(/Synthetic startup marker/)).not.toBeInTheDocument()
    expect(screen.queryByText(/Synthetic retry marker/)).not.toBeInTheDocument()
    await selectMatching(user, screen.getByRole('combobox', { name: /日志等级/ }), /^WARN\+$/i)
    expect(screen.getByText(/Synthetic retry marker/)).toBeVisible()
    expect(screen.getByText(/Synthetic error marker/)).toBeVisible()
    await user.type(searchField(/搜索运行日志/), 'retry marker')
    expect(screen.getByText(/Synthetic retry marker/)).toBeVisible()
    expect(screen.queryByText(/Synthetic error marker/)).not.toBeInTheDocument()
    await user.clear(searchField(/搜索运行日志/))
    await user.type(searchField(/搜索运行日志/), 'no-matching-log')
    expect(screen.getByRole('heading', { name: /无.*匹配|没有.*匹配|未找到|无.*结果/ })).toBeVisible()
  })

  it('shows an explicit archived log empty state', async () => {
    await openProcess(/运行日志/, createArchiveFixture({ runLogs: [] }))
    expect(screen.getByRole('heading', { name: /暂无.*日志|未.*(?:采集|记录).*日志|没有.*日志/ })).toBeVisible()
  })

  it('never crashes on an invalid collected log timestamp', async () => {
    const data = createArchiveFixture()
    data.runLogs[0].timestamp = Number.NaN
    const { container } = await openProcess(/运行日志/, data)
    expect(screen.getByText(/Synthetic startup marker/)).toBeVisible()
    expect(container).not.toHaveTextContent(/NaN|Invalid Date/)
  })
})
