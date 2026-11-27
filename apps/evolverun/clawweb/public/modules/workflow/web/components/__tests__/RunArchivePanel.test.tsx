import { cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import RunArchivePanel from '../RunArchivePanel'
import { ARCHIVE_FLOW_ID, createArchiveFixture } from './RunArchivePanel.fixture'

const { useRunArchive } = vi.hoisted(() => ({ useRunArchive: vi.fn() }))
vi.mock('@avernet/workflow/web/api/hooks', () => ({ useRunArchive }))

afterEach(cleanup)
beforeEach(() => {
  useRunArchive.mockClear()
  useRunArchive.mockReturnValue({ data: createArchiveFixture(), isLoading: false, isError: false, error: null })
})

const sections = [/运行概览/, /运行过程/, /错误详情/, /AI\s*诊断/, /建议\s*YAML/i]

function renderArchive(data = createArchiveFixture(), runStatus = 'failed') {
  useRunArchive.mockReturnValue({ data, isLoading: false, isError: false, error: null })
  return render(<RunArchivePanel flowId={ARCHIVE_FLOW_ID} runStatus={runStatus} />)
}

describe('RunArchivePanel navigation and honest summary', () => {
  it('switches all five main sections with a distinct heading and a current navigation button', async () => {
    const user = userEvent.setup()
    renderArchive()
    const nav = screen.getByRole('navigation', { name: /运行档案分区/ })
    expect(within(nav).getAllByRole('button')).toHaveLength(5)

    for (const name of sections) {
      const button = within(nav).getByRole('button', { name })
      await user.click(button)
      expect(screen.getByRole('heading', { level: 2, name })).toBeVisible()
      expect(button).toHaveAttribute('aria-current', 'page')
      expect(within(nav).getAllByRole('button').filter(item => item.getAttribute('aria-current') && item.getAttribute('aria-current') !== 'false')).toEqual([button])
    }
    expect(useRunArchive).toHaveBeenCalledWith(ARCHIVE_FLOW_ID)
  })

  it('places the long-ID footer on the next row, not as a third body column', () => {
    const data = createArchiveFixture()
    const { container } = renderArchive(data)
    const nav = screen.getByRole('navigation', { name: /运行档案分区/ })
    const footer = container.querySelector('footer')
    expect(footer).not.toBeNull()
    expect(footer).toHaveTextContent(data.archive.archiveId)
    expect(footer).not.toContainElement(nav)

    // jsdom has no layout engine: verify the structural cause of the original squeeze.
    const body = nav.parentElement!
    const footerSharesBody = footer!.parentElement === body
    if (footerSharesBody) {
      expect(body.className).toMatch(/grid/)
      expect(footer!.className).toMatch(/col-span-(?:full|2)/)
    } else {
      expect(body.children).toHaveLength(2)
      expect(body.parentElement).toContainElement(footer)
      expect(body.compareDocumentPosition(footer!) & Node.DOCUMENT_POSITION_FOLLOWING).not.toBe(0)
    }
    expect(body.className).toMatch(/grid|flex/)
    expect(body.className).not.toMatch(/grid-cols-3/)
    const detail = Array.from(body.children).find(child => child !== nav && child !== footer)
    expect(detail?.className).toMatch(/min-w-0/)
    expect(footer!.className + ' ' + footer!.innerHTML).toMatch(/break-all|break-words|overflow-wrap|overflow-x-auto|truncate/)
  })

  it('does not confuse completed archive collection with a failed run', () => {
    renderArchive()
    const runState = screen.getByText(/^运行状态$/).parentElement!
    const archiveState = screen.getByText(/^档案(?:聚合)?状态$/).parentElement!
    expect(within(runState).getByText(/^(?:执行)?失败$|^failed$/i)).toBeVisible()
    expect(archiveState).toHaveTextContent(/completed|已完成|采集完成|聚合完成/i)
    expect(screen.queryByText(/^(?:本次)?(?:运行|工作流)(?:已)?成功[。！!]?$/)).not.toBeInTheDocument()
  })

  it.each([
    ['cancelled', /取消|中止|cancelled/i],
    ['timeout', /超时|timeout/i],
  ] as const)('keeps %s run status distinct from archive completion', (status, label) => {
    renderArchive(createArchiveFixture({ flowRun: { status } }), status)
    expect(screen.getByText(/^运行状态$/).parentElement).toHaveTextContent(label)
    expect(screen.queryByText(/^(?:本次)?运行(?:已)?成功[。！!]?$/)).not.toBeInTheDocument()
  })

  it.each(['succeeded', 'running', 'waiting', 'pending', 'queued', 'blocked', 'postActionsRunning', 'failed', 'cancelled', 'timeout', 'skipped', 'custom-state', undefined, null])('loads archive for status %s without a frontend status gate', runStatus => {
    renderArchive(createArchiveFixture(), runStatus)
    expect(useRunArchive).toHaveBeenCalledWith(ARCHIVE_FLOW_ID)
    expect(screen.getByRole('navigation', { name: /运行档案分区/ })).toBeVisible()
    expect(screen.queryByText(/当前状态不提供运行档案/)).not.toBeInTheDocument()
  })

  it.each(['succeeded', 'running', undefined] as const)('shows real loading and errors for status %s', runStatus => {
    useRunArchive.mockReturnValue({ data: undefined, isLoading: true, isError: false, refetch: vi.fn() })
    const view = render(<RunArchivePanel flowId={ARCHIVE_FLOW_ID} runStatus={runStatus} />)
    expect(screen.getByRole('status')).toHaveTextContent('正在加载运行档案')
    useRunArchive.mockReturnValue({ data: undefined, isLoading: false, isError: true, error: new Error('Synthetic archive unavailable'), refetch: vi.fn() })
    view.rerender(<RunArchivePanel flowId={ARCHIVE_FLOW_ID} runStatus={runStatus} />)
    expect(screen.getByRole('alert')).toHaveTextContent('Synthetic archive unavailable')
    expect(screen.getByRole('button', { name: '重新加载档案' })).toBeVisible()
  })

  it.each(['pending', 'queued', 'running', 'waiting', 'blocked', 'postActionsRunning'])('explains that %s evidence may keep changing', runStatus => {
    renderArchive(createArchiveFixture(), runStatus)
    expect(screen.getByRole('status')).toHaveTextContent(/运行尚未结束.*采集完成不代表运行结束/)
  })

  it('uses the archived flow status when the parent has not supplied one', () => {
    render(<RunArchivePanel flowId={ARCHIVE_FLOW_ID} />)
    expect(screen.getByRole('navigation', { name: /运行档案分区/ })).toBeVisible()
    expect(screen.getByText(/^运行状态$/).parentElement).toHaveTextContent(/失败/)
  })

  it('refreshes read-only evidence, retains navigation and prevents repeated clicks while fetching', async () => {
    const refetch = vi.fn()
    const user = userEvent.setup()
    useRunArchive.mockReturnValue({ data: createArchiveFixture(), isLoading: false, isFetching: false, isError: false, refetch })
    const view = render(<RunArchivePanel flowId={ARCHIVE_FLOW_ID} runStatus="running" />)
    await user.click(screen.getByRole('button', { name: '运行过程', exact: true }))
    await user.click(screen.getByRole('button', { name: '刷新运行档案' }))
    expect(refetch).toHaveBeenCalledTimes(1)
    useRunArchive.mockReturnValue({ data: createArchiveFixture(), isLoading: false, isFetching: true, isError: false, refetch })
    view.rerender(<RunArchivePanel flowId={ARCHIVE_FLOW_ID} runStatus="running" />)
    expect(screen.getByRole('button', { name: '刷新运行档案' })).toBeDisabled()
    expect(screen.getByRole('heading', { level: 2, name: '运行过程' })).toBeVisible()
    useRunArchive.mockReturnValue({ data: createArchiveFixture(), isLoading: false, isFetching: false, isError: true, refetch })
    view.rerender(<RunArchivePanel flowId={ARCHIVE_FLOW_ID} runStatus="running" />)
    expect(screen.getByRole('alert')).toHaveTextContent('保留上次已加载数据')
    expect(screen.getByRole('heading', { level: 2, name: '运行过程' })).toBeVisible()
  })

  it('treats zero collected failed nodes as missing evidence, and offers a usable next step', async () => {
    const user = userEvent.setup()
    const data = createArchiveFixture({ nodeExecutions: [], failureSummary: { failedNodeCount: 0, failedNodes: [], rootCauseHints: [], errorTimeline: [] } })
    renderArchive(data)
    expect(within(screen.getByRole('region', { name: /^运行概览$/ })).getByText(/未.*(?:采集|记录).*失败节点|暂无.*失败节点/)).toBeVisible()
    expect(screen.queryByText(/^(?:本次)?(?:运行|工作流)(?:已)?成功[。！!]?$/)).not.toBeInTheDocument()
    expect(within(screen.getByText(/^运行状态$/).parentElement!).getByText(/^(?:执行)?失败$|^failed$/i)).toBeVisible()
    await user.click(within(screen.getByRole('navigation', { name: /运行档案分区/ })).getByRole('button', { name: /错误详情/ }))
    expect(screen.getByRole('heading', { name: /未.*(?:采集|记录).*失败节点|暂无.*失败节点/ })).toBeVisible()
    await user.click(screen.getAllByRole('button', { name: /查看.*(?:运行过程|节点|执行|事件)/ })[0])
    expect(screen.getByRole('heading', { level: 2, name: /运行过程/ })).toBeVisible()
  })

  it('shows partial collection and archive errors without hiding already collected data', async () => {
    const user = userEvent.setup()
    const data = createArchiveFixture()
    data.archive.status = 'partial'
    data.archive.errors = ['Synthetic trace collection unavailable', 'Synthetic log collection incomplete']
    renderArchive(data)
    expect(screen.getAllByText(/partial|部分|不完整/i).length).toBeGreaterThan(0)
    for (const error of data.archive.errors) expect(screen.getByText(error, { exact: false })).toBeVisible()
    await user.click(within(screen.getByRole('navigation', { name: /运行档案分区/ })).getByRole('button', { name: /AI\s*诊断/ }))
    expect(screen.getByText('Synthetic diagnosis reasoning')).toBeVisible()
  })

  it.each([
    [/AI\s*诊断/, /暂无.*诊断|未.*诊断|没有.*诊断/],
    [/建议\s*YAML/i, /暂无.*YAML|未.*YAML|没有.*YAML/i],
  ])('gives absent %s an explicit explanation and a local next step', async (name, empty) => {
    const user = userEvent.setup()
    renderArchive(createArchiveFixture({ analysis: null, suggestedYaml: null }))
    await user.click(within(screen.getByRole('navigation', { name: /运行档案分区/ })).getByRole('button', { name }))
    expect(screen.getByRole('heading', { name: empty })).toBeVisible()
    expect(screen.queryByRole('button', { name: /生成诊断|开始诊断|重新分析/ })).not.toBeInTheDocument()
    await user.click(screen.getAllByRole('button', { name: /查看.*(?:运行过程|节点|执行|事件|错误)/ })[0])
    expect(screen.getByRole('heading', { level: 2, name: /运行过程|错误详情/ })).toBeVisible()
  })
})
