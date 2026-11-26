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

  it.each([
    ['succeeded', 'loading', /成功|succeeded/i],
    ['succeeded', 'error', /成功|succeeded/i],
    ['running', 'loading', /运行中|执行中|running/i],
    ['running', 'error', /运行中|执行中|running/i],
    [undefined, 'loading', /未记录|未知|未提供/],
    [undefined, 'error', /未记录|未知|未提供/],
  ] as const)('keeps ineligible status %s neutral even with a %s hook result', (runStatus, hookState, statusLabel) => {
    useRunArchive.mockReturnValue({
      data: undefined, isLoading: hookState === 'loading', isError: hookState === 'error',
      error: hookState === 'error' ? new Error('Synthetic archive not applicable') : null,
    })
    const { container } = render(<RunArchivePanel flowId={ARCHIVE_FLOW_ID} runStatus={runStatus} />)
    expect(useRunArchive).not.toHaveBeenCalled()
    expect(screen.queryByRole('navigation', { name: /运行档案分区/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.queryByText(/(?:加载|正在加载).*运行档案|加载.*(?:失败|错误)|Synthetic archive not applicable/)).not.toBeInTheDocument()
    expect(screen.getByRole('heading', { name: /当前状态.*(?:不提供|不支持|无).*档案|(?:不提供|不支持).*运行档案/ })).toBeVisible()
    expect(container).toHaveTextContent(statusLabel)
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
