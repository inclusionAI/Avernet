import { cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ReactFlowProps } from '@xyflow/react'
import type { WorkflowNode } from '@avernet/clawweb-shared/web/types'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const graph = vi.hoisted(() => ({
  props: null as ReactFlowProps | null,
  controls: null as { showInteractive: boolean; fitViewOptions: unknown } | null,
  wrapperKeyDown: vi.fn(),
  getViewport: vi.fn(),
  setCenter: vi.fn(),
}))

// Exercise our node UI, not ReactFlow's layout/ResizeObserver machinery in jsdom.
// A bubbling-key listener models the keyboard boundary around custom nodes.
vi.mock('@xyflow/react', () => ({
  Position: { Left: 'left', Right: 'right' },
  MarkerType: { ArrowClosed: 'arrowclosed' },
  useReactFlow: () => ({ getViewport: graph.getViewport, setCenter: graph.setCenter }),
  ReactFlow: (props: ReactFlowProps) => {
    graph.props = props
    return (
      <div className="react-flow" data-testid="react-flow" onKeyDown={graph.wrapperKeyDown}>
        {props.nodes?.map(node => {
          const Component = props.nodeTypes![node.type!]
          return (
            <div key={node.id} data-testid={`flow-node-${node.id}`}>
              <Component
                id={node.id} type={node.type!} data={node.data} selected={node.selected ?? false}
                dragging={false} draggable={false} selectable={false} deletable={false}
                isConnectable={false} positionAbsoluteX={node.position.x} positionAbsoluteY={node.position.y} zIndex={0}
              />
            </div>
          )
        })}
        {props.children}
      </div>
    )
  },
  Handle: ({ type, position }: { type: string; position: string }) => <span data-testid={`${type}-${position}`} />,
  Background: () => <div data-testid="graph-background" />,
  Panel: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  Controls: (props: { showInteractive: boolean; fitViewOptions: unknown }) => {
    graph.controls = props
    return <div data-testid="graph-controls" />
  },
}))

import RunDagView from '../RunDagView'

const node = (id: string, dependsOn: string[] = [], title = `节点 ${id}`): WorkflowNode => ({
  id, title, dependsOn, executor: { type: 'action' },
})
const specNodes = [node('start'), node('branch-a', ['start']), node('branch-b', ['start']), node('finish', ['branch-a', 'branch-b'])]

beforeEach(() => {
  graph.props = null
  graph.controls = null
  graph.wrapperKeyDown.mockClear()
  graph.getViewport.mockReset().mockReturnValue({ x: 0, y: 0, zoom: 0.65 })
  graph.setCenter.mockReset().mockResolvedValue(true)
})
afterEach(() => { cleanup(); vi.restoreAllMocks() })

function rect(x: number, y: number, width: number, height: number): DOMRect {
  return { x, y, width, height, left: x, top: y, right: x + width, bottom: y + height, toJSON: () => ({}) }
}

function mockFocusBounds(button: HTMLElement, buttonRect: DOMRect, keyboardFocus = true) {
  vi.spyOn(screen.getByTestId('react-flow'), 'getBoundingClientRect').mockReturnValue(rect(20, 100, 1000, 380))
  vi.spyOn(button, 'getBoundingClientRect').mockReturnValue(buttonRect)
  const originalMatches = button.matches.bind(button)
  vi.spyOn(button, 'matches').mockImplementation(selector => selector === ':focus-visible' ? keyboardFocus : originalMatches(selector))
}

describe('RunDagView', () => {
  it('lays out branching dependencies left to right with directional edges', () => {
    render(<RunDagView specNodes={specNodes} nodeStatusMap={{}} onNodeClick={vi.fn()} />)
    const nodes = graph.props!.nodes!
    const [start, branchA, branchB, finish] = nodes
    expect(nodes.map(item => item.id)).toEqual(['start', 'branch-a', 'branch-b', 'finish'])
    expect(start.position.x).toBeLessThan(branchA.position.x)
    expect(branchA.position.x).toBe(branchB.position.x)
    expect(branchA.position.y).not.toBe(branchB.position.y)
    expect(branchA.position.x).toBeLessThan(finish.position.x)
    expect(graph.props!.edges!.map(edge => [edge.source, edge.target])).toEqual([
      ['start', 'branch-a'], ['start', 'branch-b'], ['branch-a', 'finish'], ['branch-b', 'finish'],
    ])
    expect(graph.props!.edges!.every(edge => edge.type === 'smoothstep' && edge.markerEnd)).toBe(true)
    expect(screen.getAllByTestId('target-left')).toHaveLength(4)
    expect(screen.getAllByTestId('source-right')).toHaveLength(4)
  })

  it('keeps execution status, executor, progress, and readable spec IDs', () => {
    render(<RunDagView specNodes={[node('branch-a')]} nodeStatusMap={{ 'branch-a': {
      status: 'running', executorType: 'bot', progressMessage: '正在收集运行证据',
    } }} onNodeClick={vi.fn()} />)
    const button = screen.getByRole('button', { name: '定位节点：节点 branch-a（branch-a）' })
    expect(within(button).getByText('branch-a')).toBeVisible()
    expect(within(button).getByText('运行中')).toBeVisible()
    expect(within(button).getByText('bot')).toHaveAttribute('title', '执行器：bot')
    expect(within(button).getByText('正在收集运行证据')).toHaveAttribute('title', '正在收集运行证据')
  })

  it('shows spec nodes without fabricating execution results or adding execution-only nodes', () => {
    render(<RunDagView specNodes={[node('not-started')]} nodeStatusMap={{
      'dynamic-execution-only': { status: 'succeeded' },
    }} onNodeClick={vi.fn()} />)
    expect(screen.getByText('not-started')).toBeVisible()
    expect(screen.getByText('action')).toBeVisible()
    expect(screen.getByText('无执行记录')).toBeVisible()
    expect(screen.queryByText('待执行')).not.toBeInTheDocument()
    expect(screen.queryByText('已成功')).not.toBeInTheDocument()
    expect(graph.props!.nodes!.map(item => item.id)).toEqual(['not-started'])
    expect(graph.props!.nodes![0].data.status).toBeUndefined()
  })

  it.each([
    ['pending', '待执行'], ['succeeded', '已成功'], ['failed', '已失败'],
    ['waiting', '等待中'], ['blocked', '已阻塞'], ['skipped', '已跳过'],
    ['cancelled', '已中止'], ['postActionsRunning', '运行中'], ['custom-state', 'custom-state'],
  ])('preserves the actual %s execution state', (status, label) => {
    render(<RunDagView specNodes={[node('a')]} nodeStatusMap={{ a: { status } }} onNodeClick={vi.fn()} />)
    expect(screen.getByText(label)).toBeVisible()
  })

  it('calls selection exactly once for click, Enter, and Space without bubbling activation to ReactFlow', async () => {
    const user = userEvent.setup()
    const onNodeClick = vi.fn()
    render(<RunDagView specNodes={[node('node /?#')]} nodeStatusMap={{}} onNodeClick={onNodeClick} />)
    await user.click(screen.getByRole('button', { name: '定位节点：节点 node /?#（node /?#）' }))
    expect(onNodeClick.mock.calls).toEqual([['node /?#']])
    onNodeClick.mockClear()
    await user.keyboard('{Enter}')
    expect(onNodeClick.mock.calls).toEqual([['node /?#']])
    onNodeClick.mockClear()
    await user.keyboard(' ')
    expect(onNodeClick.mock.calls).toEqual([['node /?#']])
    expect(graph.wrapperKeyDown).not.toHaveBeenCalled()
    expect(graph.props!.nodesFocusable).toBe(false)
    expect(graph.props!.elementsSelectable).toBe(false)
    expect(graph.props!.panActivationKeyCode).toBeNull()
    expect(graph.props!.onNodeClick).toBeUndefined()
    await user.keyboard('{ArrowRight}')
    expect(onNodeClick).toHaveBeenCalledTimes(1)
  })

  it('reveals an offscreen first button on Tab in a 60-node chain without zooming or activating it', async () => {
    const user = userEvent.setup()
    const onNodeClick = vi.fn()
    const chain = Array.from({ length: 60 }, (_, index) => node(`step-${index}`, index ? [`step-${index - 1}`] : []))
    render(<RunDagView specNodes={chain} nodeStatusMap={{}} onNodeClick={onNodeClick} />)
    const button = screen.getByRole('button', { name: '定位节点：节点 step-0（step-0）' })
    mockFocusBounds(button, rect(-479, 250, 23.2, 13.6))
    graph.getViewport.mockReturnValue({ x: -499, y: 150, zoom: 0.1 })
    await user.tab()
    expect(button).toHaveFocus()
    expect(graph.setCenter.mock.calls).toEqual([[116, 68, { zoom: 0.1, duration: 0 }]])
    expect(onNodeClick).not.toHaveBeenCalled()
    expect(screen.getAllByRole('button')).toHaveLength(60)
    await user.keyboard('{Enter}')
    expect(onNodeClick.mock.calls).toEqual([['step-0']])
    await user.keyboard(' ')
    expect(onNodeClick.mock.calls).toEqual([['step-0'], ['step-0']])
    expect(graph.setCenter).toHaveBeenCalledTimes(1)
    expect(graph.wrapperKeyDown).not.toHaveBeenCalled()
  })

  it.each([
    ['left', { x: -20, y: 100 }],
    ['right', { x: 900, y: 100 }],
    ['top', { x: 100, y: -20 }],
    ['bottom', { x: 100, y: 300 }],
  ])('reveals a keyboard-focused node clipped at the %s edge at the current user zoom', async (_edge, position) => {
    const user = userEvent.setup()
    render(<RunDagView specNodes={[node('a')]} nodeStatusMap={{}} onNodeClick={vi.fn()} />)
    const button = screen.getByRole('button', { name: '定位节点：节点 a（a）' })
    // Native focus may make DOM bounds appear visible before ReactFlow resets scrolling.
    mockFocusBounds(button, rect(200, 200, 232, 136))
    graph.getViewport.mockReturnValue({ ...position, zoom: 1.3 })
    await user.tab()
    expect(button).toHaveFocus()
    expect(graph.setCenter.mock.calls).toEqual([[116, 68, { zoom: 1.3, duration: 0 }]])
  })

  it('keeps all 60 nodes visible during forward and reverse Tab traversal despite transient native scrolling', async () => {
    const user = userEvent.setup()
    const onNodeClick = vi.fn()
    const chain = Array.from({ length: 60 }, (_, index) => node(`step-${index}`, index ? [`step-${index - 1}`] : []))
    render(<RunDagView specNodes={chain} nodeStatusMap={{}} onNodeClick={onNodeClick} />)
    const canvas = screen.getByTestId('react-flow')
    const canvasBounds = rect(20, 100, 960, 380)
    vi.spyOn(canvas, 'getBoundingClientRect').mockReturnValue(canvasBounds)
    let viewport = { x: 8, y: 183.2, zoom: 0.1 }
    graph.getViewport.mockImplementation(() => viewport)
    graph.setCenter.mockImplementation((x, y, options) => {
      viewport = { x: canvasBounds.width / 2 - x * options.zoom, y: canvasBounds.height / 2 - y * options.zoom, zoom: options.zoom }
      return Promise.resolve(true)
    })
    const buttons = screen.getAllByRole('button')
    buttons.forEach(button => {
      const originalMatches = button.matches.bind(button)
      vi.spyOn(button, 'matches').mockImplementation(selector => selector === ':focus-visible' || originalMatches(selector))
      // Model the browser scrolling an offscreen button into view before focusin.
      // ReactFlow resets scrollLeft afterwards; these DOM bounds are not reliable.
      vi.spyOn(button, 'getBoundingClientRect').mockReturnValue(rect(488, 280, 23.2, 13.6))
    })
    function expectVisible(index: number) {
      const flowNode = graph.props!.nodes![index]
      const left = flowNode.position.x * viewport.zoom + viewport.x
      const top = flowNode.position.y * viewport.zoom + viewport.y
      expect(buttons[index]).toHaveFocus()
      expect(left).toBeGreaterThanOrEqual(0)
      expect(left + 232 * viewport.zoom).toBeLessThanOrEqual(canvasBounds.width)
      expect(top).toBeGreaterThanOrEqual(0)
      expect(top + 136 * viewport.zoom).toBeLessThanOrEqual(canvasBounds.height)
      expect(viewport.zoom).toBe(0.1)
    }
    for (let index = 0; index < 60; index += 1) {
      await user.tab()
      expectVisible(index)
    }
    for (let index = 58; index >= 0; index -= 1) {
      await user.tab({ shift: true })
      expectVisible(index)
    }
    expect(graph.setCenter.mock.calls.length).toBeGreaterThan(2)
    expect(onNodeClick).not.toHaveBeenCalled()
  })

  it('leaves a fully visible keyboard-focused button and its viewport untouched', async () => {
    const user = userEvent.setup()
    render(<RunDagView specNodes={[node('a')]} nodeStatusMap={{}} onNodeClick={vi.fn()} />)
    const button = screen.getByRole('button', { name: '定位节点：节点 a（a）' })
    mockFocusBounds(button, rect(200, 200, 232, 136))
    await user.tab()
    expect(button).toHaveFocus()
    expect(graph.setCenter).not.toHaveBeenCalled()
    expect(graph.getViewport).toHaveBeenCalledOnce()
  })

  it('does not pan on pointer focus or steal the execution-detail focus set by a click', async () => {
    const user = userEvent.setup()
    const onNodeClick = vi.fn(() => document.getElementById('execution-details')!.focus())
    render(<>
      <RunDagView specNodes={[node('a')]} nodeStatusMap={{}} onNodeClick={onNodeClick} />
      <section id="execution-details" aria-label="节点执行详情" tabIndex={-1}>执行详情</section>
    </>)
    const button = screen.getByRole('button', { name: '定位节点：节点 a（a）' })
    mockFocusBounds(button, rect(-479, 200, 232, 136), false)
    await user.click(button)
    expect(onNodeClick).toHaveBeenCalledExactlyOnceWith('a')
    expect(screen.getByRole('region', { name: '节点执行详情' })).toHaveFocus()
    expect(graph.setCenter).not.toHaveBeenCalled()
  })

  it('reflects controlled selection visually and accessibly without treating selection as execution', () => {
    const props = { specNodes: [node('a'), node('b')], nodeStatusMap: {}, onNodeClick: vi.fn() }
    const { rerender } = render(<RunDagView {...props} selectedNodeId="a" />)
    const a = screen.getByRole('button', { name: '定位节点：节点 a（a）' })
    const b = screen.getByRole('button', { name: '定位节点：节点 b（b）' })
    expect(a).toHaveAttribute('aria-pressed', 'true')
    expect(a).toHaveClass('ring-primary')
    expect(b).toHaveAttribute('aria-pressed', 'false')
    rerender(<RunDagView {...props} selectedNodeId="b" />)
    expect(a).toHaveAttribute('aria-pressed', 'false')
    expect(a).not.toHaveClass('ring-primary')
    expect(b).toHaveAttribute('aria-pressed', 'true')
    expect(graph.props!.nodes!.map(item => item.selected)).toEqual([false, true])
    expect(screen.getAllByText('无执行记录')).toHaveLength(2)
  })

  it('refreshes execution evidence and animates only active dependency edges', () => {
    const props = { specNodes: [node('a'), node('b', ['a'])], onNodeClick: vi.fn() }
    const { rerender } = render(<RunDagView {...props} nodeStatusMap={{ b: { status: 'running', progressMessage: '处理中' } }} />)
    expect(graph.props!.edges![0].animated).toBe(true)
    expect(screen.getByText('处理中')).toBeVisible()
    rerender(<RunDagView {...props} nodeStatusMap={{ b: { status: 'succeeded', progressMessage: '已完成' } }} />)
    expect(graph.props!.edges![0].animated).toBe(false)
    expect(screen.getByText('已成功')).toBeVisible()
    expect(screen.getByText('已完成')).toBeVisible()
    expect(screen.queryByText('处理中')).not.toBeInTheDocument()
  })

  it('retains fit/zoom controls, pinch zoom and canvas dragging without topology editing', () => {
    render(<RunDagView specNodes={[node('a')]} nodeStatusMap={{}} onNodeClick={vi.fn()} />)
    expect(screen.getByTestId('graph-controls')).toBeVisible()
    expect(screen.getByTestId('graph-background')).toBeInTheDocument()
    expect(graph.props).toMatchObject({
      fitView: true, minZoom: 0.1, maxZoom: 1.5,
      nodesDraggable: false, nodesConnectable: false, panOnDrag: true, zoomOnPinch: true,
      fitViewOptions: { padding: 0.2, minZoom: 0.1, maxZoom: 1 },
      ariaLabelConfig: { 'controls.fitView.ariaLabel': '适应 DAG 画布' },
    })
    expect(graph.controls!.showInteractive).toBe(false)
    expect(graph.controls!.fitViewOptions).toEqual(graph.props!.fitViewOptions)
  })

  it('handles missing/duplicate dependencies and cycles with no phantom nodes or invalid coordinates', () => {
    render(<RunDagView specNodes={[node('a', ['b', 'missing', 'b']), node('b', ['a']), node('c', ['missing'])]}
      nodeStatusMap={{}} onNodeClick={vi.fn()} />)
    expect(graph.props!.edges!.map(edge => [edge.source, edge.target])).toEqual([['b', 'a'], ['a', 'b']])
    expect(graph.props!.nodes!.every(item => Number.isFinite(item.position.x) && Number.isFinite(item.position.y))).toBe(true)
  })

  it('uses semantic graph/edge colors rather than literal palette colors', () => {
    render(<RunDagView specNodes={[node('a'), node('b', ['a'])]} nodeStatusMap={{ b: { status: 'failed' } }} onNodeClick={vi.fn()} />)
    expect(graph.props!.edges![0].style!.stroke).toContain('var(--destructive,')
    expect(graph.props!.style!['--xy-controls-button-background-color']).toContain('var(--card,')
  })

  it('shows an empty topology without mounting ReactFlow', () => {
    render(<RunDagView specNodes={[]} nodeStatusMap={{ a: { status: 'succeeded' } }} onNodeClick={vi.fn()} />)
    expect(screen.getByRole('status')).toHaveTextContent('当前定义中暂无节点')
    expect(screen.queryByTestId('react-flow')).not.toBeInTheDocument()
  })
})
