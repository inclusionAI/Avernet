import { StrictMode, useState } from 'react'
import { cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { NodeExecution } from '@avernet/clawweb-shared/web/types'
import NodeExecutionList from '../NodeExecutionList'

function node(overrides: Partial<NodeExecution> = {}): NodeExecution {
  return {
    flow_id: 'parent', node_id: 'target', node_title: '目标节点', executor_type: 'action',
    triggered_by: null, phase: null, branch_id: null, session_key: null, session_id: null,
    embedded_session_key: null, status: 'succeeded', started_at: null, completed_at: null,
    duration_ms: null, input_json: null, output_json: null, error_text: null,
    token_usage_json: null, system_context_json: null, progress_message: null,
    resolved_prompt: null, attempt: 1, ...overrides,
  }
}

const scrollIntoView = vi.fn<(options?: ScrollIntoViewOptions) => void>()
const originalScroll = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'scrollIntoView')
const matchMedia = vi.fn()

beforeEach(() => {
  scrollIntoView.mockClear()
  Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', { configurable: true, value: scrollIntoView })
  matchMedia.mockReset().mockReturnValue({ matches: false })
  vi.stubGlobal('matchMedia', matchMedia)
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  if (originalScroll) Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', originalScroll)
  else Reflect.deleteProperty(HTMLElement.prototype, 'scrollIntoView')
})

describe('NodeExecutionList DAG location', () => {
  it('focuses the real node button, centers its row, and keeps repeated requests expanded', () => {
    const onSelectNode = vi.fn()
    const nodes = [node()]
    const focus = vi.spyOn(HTMLElement.prototype, 'focus')
    const view = (requestId: number) => (
      <StrictMode>
        <NodeExecutionList nodes={nodes} selectedNodeId="target" onSelectNode={onSelectNode}
          locateRequest={{ nodeId: 'target', requestId }} />
      </StrictMode>
    )
    const mounted = render(view(1))
    const button = screen.getByRole('button', { name: '目标节点 target' })
    const row = button.closest('tr')!

    expect(button).toHaveAttribute('type', 'button')
    expect(button).toHaveAttribute('aria-expanded', 'true')
    expect(button).toHaveFocus()
    expect(focus).toHaveBeenCalledExactlyOnceWith({ preventScroll: true })
    expect(scrollIntoView).toHaveBeenCalledExactlyOnceWith({ block: 'center', behavior: 'smooth' })
    expect(scrollIntoView.mock.instances[0]).toBe(row)
    expect(row).toHaveClass('bg-primary/10', 'ring-primary/40')
    const details = document.getElementById(button.getAttribute('aria-controls')!)!
    expect(within(details).getByText('节点详情')).toBeInTheDocument()
    expect(details.querySelector('td')).toHaveAttribute('colspan', '9')
    expect(screen.getAllByRole('columnheader').map(header => header.textContent?.trim())).toEqual([
      '节点', '状态', '执行器', '触发者', '阶段', '耗时', 'Token', '开始时间', '完成时间',
    ])

    mounted.rerender(view(1))
    expect(scrollIntoView).toHaveBeenCalledTimes(1)
    mounted.rerender(view(2))
    expect(scrollIntoView).toHaveBeenCalledTimes(2)
    expect(focus).toHaveBeenCalledTimes(2)
    expect(button).toHaveFocus()
    expect(button).toHaveAttribute('aria-expanded', 'true')
    expect(document.getElementById(button.getAttribute('aria-controls')!)).toBe(details)
    expect(onSelectNode).not.toHaveBeenCalled()
  })

  it('locates successive targets and can return to an earlier node without toggling it closed', () => {
    const nodes = [node(), node({ node_id: 'second', node_title: '第二节点' })]
    const onSelectNode = vi.fn()
    const view = (nodeId: string, requestId: number) => <NodeExecutionList nodes={nodes}
      selectedNodeId={nodeId} onSelectNode={onSelectNode} locateRequest={{ nodeId, requestId }} />
    const mounted = render(view('target', 1))
    const first = screen.getByRole('button', { name: '目标节点 target' })
    const second = screen.getByRole('button', { name: '第二节点 second' })
    mounted.rerender(view('second', 2))
    expect(second).toHaveFocus()
    expect(first).toHaveAttribute('aria-expanded', 'false')
    mounted.rerender(view('target', 3))
    expect(first).toHaveFocus()
    expect(first).toHaveAttribute('aria-expanded', 'true')
    expect(second).toHaveAttribute('aria-expanded', 'false')
    expect(scrollIntoView.mock.instances).toEqual([first.closest('tr'), second.closest('tr'), first.closest('tr')])
    expect(onSelectNode).not.toHaveBeenCalled()
  })

  it('uses instant scrolling when reduced motion is preferred', () => {
    matchMedia.mockReturnValue({ matches: true })
    render(<NodeExecutionList nodes={[node()]} selectedNodeId="target" onSelectNode={vi.fn()}
      locateRequest={{ nodeId: 'target', requestId: 1 }} />)

    expect(matchMedia).toHaveBeenCalledWith('(prefers-reduced-motion: reduce)')
    expect(scrollIntoView).toHaveBeenCalledExactlyOnceWith({ block: 'center', behavior: 'instant' })
    expect(screen.getByRole('button', { name: '目标节点 target' })).toHaveFocus()
  })

  it('allows Enter/Space to expand and collapse without scrolling or double selection', async () => {
    const user = userEvent.setup()
    const onSelectNode = vi.fn()
    function Harness() {
      const [selected, setSelected] = useState<string>()
      return <NodeExecutionList nodes={[node()]} selectedNodeId={selected} onSelectNode={(id) => {
        onSelectNode(id)
        setSelected(previous => previous === id ? undefined : id)
      }} />
    }
    render(<Harness />)
    const button = screen.getByRole('button', { name: '目标节点 target' })
    await user.tab()
    expect(button).toHaveFocus()
    expect(button).toHaveAttribute('aria-expanded', 'false')
    await user.keyboard('{Enter}')
    expect(button).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByText('节点详情')).toBeInTheDocument()
    expect(onSelectNode).toHaveBeenCalledExactlyOnceWith('target')
    await user.keyboard(' ')
    expect(button).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByText('节点详情')).not.toBeInTheDocument()
    expect(onSelectNode).toHaveBeenCalledTimes(2)
    expect(scrollIntoView).not.toHaveBeenCalled()
  })

  it('does not replay a handled DAG request on manual collapse, reopen, or node refresh', async () => {
    const user = userEvent.setup()
    const onSelectNode = vi.fn()
    const request = { nodeId: 'target', requestId: 1 }
    function Harness({ attempt = 1 }: { attempt?: number }) {
      const [selected, setSelected] = useState<string | undefined>('target')
      return <NodeExecutionList nodes={[node({ attempt })]} selectedNodeId={selected} locateRequest={request}
        onSelectNode={(id) => {
          onSelectNode(id)
          setSelected(previous => previous === id ? undefined : id)
        }} />
    }
    const mounted = render(<Harness />)
    const button = screen.getByRole('button', { name: '目标节点 target' })
    expect(scrollIntoView).toHaveBeenCalledTimes(1)
    await user.click(button)
    expect(button).toHaveAttribute('aria-expanded', 'false')
    await user.click(button)
    expect(button).toHaveAttribute('aria-expanded', 'true')
    mounted.rerender(<Harness attempt={2} />)
    expect(button).toHaveAttribute('aria-expanded', 'true')
    expect(onSelectNode).toHaveBeenCalledTimes(2)
    expect(scrollIntoView).toHaveBeenCalledTimes(1)
  })

  it('targets only this instance even when a nested same-ID row appears earlier in the DOM', () => {
    const parentNodes = [node({ node_id: 'group', node_title: '子流程', executor_type: 'subworkflow' }), node()]
    const parentSelect = vi.fn()
    const childSelect = vi.fn()
    render(<section aria-label="父列表">
      <NodeExecutionList nodes={parentNodes} selectedNodeId="target" onSelectNode={parentSelect}
        locateRequest={{ nodeId: 'target', requestId: 1 }}
        renderSubworkflowLinks={() => <section aria-label="子列表">
          <NodeExecutionList nodes={[node({ flow_id: 'child', node_title: '子目标' })]}
            selectedNodeId="target" onSelectNode={childSelect} readOnly />
        </section>} />
    </section>)
    const parentButton = screen.getByRole('button', { name: '目标节点 target' })
    const childButton = within(screen.getByRole('region', { name: '子列表' })).getByRole('button', { name: '子目标 target' })

    expect(parentButton).toHaveFocus()
    expect(childButton).not.toHaveFocus()
    expect(scrollIntoView).toHaveBeenCalledTimes(1)
    expect(scrollIntoView.mock.instances[0]).toBe(parentButton.closest('tr'))
    expect(parentButton.getAttribute('aria-controls')).not.toBe(childButton.getAttribute('aria-controls'))
    expect(parentSelect).not.toHaveBeenCalled()
    expect(childSelect).not.toHaveBeenCalled()
  })

  it('does not locate a child-only node through a request to the parent instance', () => {
    const parentNode = node({ node_id: 'group', executor_type: 'subworkflow' })
    const view = (requestId?: number) => <>
      <button type="button">保留焦点</button>
      <NodeExecutionList nodes={[parentNode]} onSelectNode={vi.fn()}
        locateRequest={requestId === undefined ? undefined : { nodeId: 'target', requestId }}
        renderSubworkflowLinks={() => <NodeExecutionList nodes={[node({ flow_id: 'child' })]} onSelectNode={vi.fn()} />} />
    </>
    const mounted = render(view())
    const retainedFocus = screen.getByRole('button', { name: '保留焦点' })
    retainedFocus.focus()
    mounted.rerender(view(1))

    expect(retainedFocus).toHaveFocus()
    expect(scrollIntoView).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: '目标节点 target' })).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByText('节点详情')).not.toBeInTheDocument()
  })

  it('keeps identical node IDs in sibling instances isolated', () => {
    const nodes = [node()]
    render(<>
      <section aria-label="首个列表"><NodeExecutionList nodes={nodes} selectedNodeId="target" onSelectNode={vi.fn()} /></section>
      <section aria-label="第二列表"><NodeExecutionList nodes={nodes} selectedNodeId="target" onSelectNode={vi.fn()}
        locateRequest={{ nodeId: 'target', requestId: 1 }} /></section>
    </>)
    const first = within(screen.getByRole('region', { name: '首个列表' })).getByRole('button', { name: '目标节点 target' })
    const second = within(screen.getByRole('region', { name: '第二列表' })).getByRole('button', { name: '目标节点 target' })
    expect(second).toHaveFocus()
    expect(first).not.toHaveFocus()
    expect(scrollIntoView.mock.instances).toEqual([second.closest('tr')])
  })

  it.each([false, true])('does not fabricate rows, details, focus, or scrolling for absent records (empty=%s)', (empty) => {
    const nodes = empty ? [] : [node({ node_id: 'other', node_title: '其他节点' })]
    const onSelectNode = vi.fn()
    const view = (requestId?: number) => <>
      <button type="button">保留焦点</button>
      <NodeExecutionList nodes={nodes} selectedNodeId="not-executed" onSelectNode={onSelectNode}
        locateRequest={requestId === undefined ? undefined : { nodeId: 'not-executed', requestId }} />
    </>
    const mounted = render(view())
    const retainedFocus = screen.getByRole('button', { name: '保留焦点' })
    retainedFocus.focus()
    const focus = vi.spyOn(HTMLElement.prototype, 'focus')
    mounted.rerender(view(1))
    mounted.rerender(view(2))

    expect(retainedFocus).toHaveFocus()
    expect(focus).not.toHaveBeenCalled()
    expect(scrollIntoView).not.toHaveBeenCalled()
    expect(onSelectNode).not.toHaveBeenCalled()
    expect(screen.queryByRole('button', { name: /not-executed/ })).not.toBeInTheDocument()
    expect(screen.queryByText('节点详情')).not.toBeInTheDocument()
    expect(screen.queryByText('暂无输入数据')).not.toBeInTheDocument()
    expect(screen.queryAllByRole('row')).toHaveLength(empty ? 0 : 2)
    if (empty) expect(screen.getByText('暂无节点执行记录')).toBeInTheDocument()
  })

  it('allows readOnly location and expansion without restoring analysis, approval, or BaaS polling actions', () => {
    const onSelectNode = vi.fn()
    const onAnalyze = vi.fn()
    const onApproval = vi.fn()
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    const nodes = [node({ executor_type: 'baas-call', status: 'running', session_id: 'session',
      progress_message: '(runId=remote)' })]
    const view = (readOnly: boolean) => <NodeExecutionList nodes={nodes} selectedNodeId="target" onSelectNode={onSelectNode}
      onAnalyze={onAnalyze} onApproval={onApproval} approvalNodeIds={new Set(['target'])} readOnly={readOnly}
      locateRequest={{ nodeId: 'target', requestId: 1 }} />
    const mounted = render(view(true))

    expect(screen.getByRole('button', { name: '目标节点 target' })).toHaveFocus()
    expect(screen.getByText('节点详情')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '节点分析' })).not.toBeInTheDocument()
    expect(screen.queryByTitle('审批')).not.toBeInTheDocument()
    expect(screen.queryByTitle('查询BaaS调用状态')).not.toBeInTheDocument()
    expect(onSelectNode).not.toHaveBeenCalled()
    expect(onAnalyze).not.toHaveBeenCalled()
    expect(onApproval).not.toHaveBeenCalled()
    expect(fetchMock).not.toHaveBeenCalled()
    mounted.rerender(view(false))
    expect(screen.getByRole('button', { name: '节点分析' })).toBeInTheDocument()
    expect(screen.getByTitle('审批')).toBeInTheDocument()
    expect(screen.getByTitle('查询BaaS调用状态')).toBeInTheDocument()
    expect(scrollIntoView).toHaveBeenCalledTimes(1)
    expect(fetchMock).not.toHaveBeenCalled()
  })
})
