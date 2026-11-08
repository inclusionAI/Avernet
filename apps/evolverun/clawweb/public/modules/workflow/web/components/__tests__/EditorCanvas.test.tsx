import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, createEvent, fireEvent, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { Edge, ReactFlowProps } from '@xyflow/react'
import type { WorkflowNode, WorkflowSpec } from '@avernet/clawweb-shared/web/types'
import type { EditorCanvasNode } from '../editor/EditorFlowNode'
import EditorCanvas from '../EditorCanvas'
import NodePalette from '../NodePalette'
import { useEditorStore } from '../../editor/store'
import { branchHandleId, canvasTheme, canvasThemeClasses, GENERAL_SOURCE_HANDLE, NODE_DRAG_TYPE } from '../editor/CanvasPresentation'

const flow = vi.hoisted(() => ({
  props: null as ReactFlowProps<EditorCanvasNode, Edge> | null,
  screenToFlowPosition: vi.fn(), setCenter: vi.fn(), fitView: vi.fn(), getNode: vi.fn(), updateNodeInternals: vi.fn(),
}))

// Exercise the actual canvas callbacks and actual Zustand/graph/contracts, not a snapshot.
// React Flow's measured DOM/gesture engine is covered separately by browser E2E.
vi.mock('@xyflow/react', () => ({
  ReactFlowProvider: ({ children }: { children: React.ReactNode }) => <div data-testid="flow-provider">{children}</div>,
  ReactFlow: (props: ReactFlowProps<EditorCanvasNode, Edge>) => {
    flow.props = props
    const NodeComponent = props.nodeTypes!.editorNode
    return <div data-testid="react-flow" tabIndex={0} onKeyDown={(event) => {
      if (!['Delete', 'Backspace'].includes(event.key)) return
      const nodes = props.nodes!.filter((node) => node.selected)
      const edges = props.edges!.filter((edge) => edge.selected || nodes.some((node) => [edge.source, edge.target].includes(node.id)))
      void props.onBeforeDelete!({ nodes, edges })
    }}>
      {props.nodes!.map((node) => <div key={node.id} data-testid={`node-${node.id}`} data-selected={String(node.selected)}>
        <button type="button" aria-label={`选择 ${node.id}`} onClick={(event) => props.onNodeClick!(event, node)}>选择</button>
        <NodeComponent id={node.id} data={node.data} type="editorNode" selected={node.selected} isConnectable={true} dragging={false} zIndex={0} draggable={true} selectable={true} deletable={true} positionAbsoluteX={node.position.x} positionAbsoluteY={node.position.y} />
      </div>)}
      {props.edges!.map((edge) => <button type="button" key={edge.id} data-testid={`edge-${edge.source}-${edge.target}`} aria-pressed={Boolean(edge.selected)} onClick={(event) => props.onEdgeClick!(event, edge)}>{edge.label ?? `${edge.source} → ${edge.target}`}</button>)}
      {props.children}
    </div>
  },
  Handle: ({ id, type, 'aria-label': label, isConnectable }: { id?: string; type: string; 'aria-label': string; isConnectable: boolean }) => <div data-testid="handle" data-handle-id={id ?? ''} data-handle-type={type} data-connectable={String(isConnectable)} aria-label={label} />,
  Position: { Top: 'top', Bottom: 'bottom', Left: 'left', Right: 'right' },
  Background: () => <div data-testid="background" />,
  Controls: () => <div aria-label="画布缩放控制" />,
  MiniMap: () => <div aria-label="流程小地图" />,
  useReactFlow: () => ({ screenToFlowPosition: flow.screenToFlowPosition, setCenter: flow.setCenter, fitView: flow.fitView, getNode: flow.getNode }),
  useUpdateNodeInternals: () => flow.updateNodeInternals,
}))

function node(id: string, overrides: Partial<WorkflowNode> = {}): WorkflowNode {
  return { id, title: `任务 ${id}`, phase: 'P1', executor: { type: 'embedded-agent', prompt: `处理 ${id} 的任务内容` }, ...overrides }
}
function load(nodes: WorkflowNode[]) {
  const spec: WorkflowSpec = { id: 'canvas-test', title: '画布测试', version: '1.0.0', nodes }
  useEditorStore.getState().loadSpec(spec)
  return spec
}
function props() { return flow.props! }
function modernBranches(...branchIds: string[]): WorkflowNode['onResult'] {
  return { branches: branchIds.map((branchId, index) => ({ branchId, match: { status: index } })) } as unknown as WorkflowNode['onResult']
}
const connection = (source: string, target: string, sourceHandle = GENERAL_SOURCE_HANDLE) => ({ source, target, sourceHandle, targetHandle: null })

beforeEach(() => {
  useEditorStore.setState(useEditorStore.getInitialState(), true)
  vi.clearAllMocks()
  flow.props = null
  flow.screenToFlowPosition.mockReturnValue({ x: 175, y: 225 })
  flow.setCenter.mockResolvedValue(true)
  flow.fitView.mockResolvedValue(true)
  flow.getNode.mockImplementation((id: string) => props().nodes?.find((item) => item.id === id))
  vi.spyOn(window, 'confirm').mockReturnValue(true)
})
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('EditorCanvas rendering and navigation', () => {
  it('owns exactly one provider and has a genuine unloaded state', () => {
    render(<EditorCanvas onNodeClick={vi.fn()} />)
    expect(screen.getAllByTestId('flow-provider')).toHaveLength(1)
    expect(screen.getByText('从侧边栏选择工作流或创建新工作流')).toBeVisible()
    expect(screen.queryByTestId('react-flow')).not.toBeInTheDocument()
  })

  it('renders readable titles, real unknown types, summaries and configuration state', () => {
    load([node('ai', { title: '分析新工单' }), node('custom', { executor: { type: 'future-executor', settings: { keep: true } } }), node('empty', { executor: { type: 'cli-script', command: '' } })])
    render(<EditorCanvas onNodeClick={vi.fn()} />)
    expect(screen.getByRole('heading', { name: '分析新工单' })).toBeVisible()
    expect(screen.getByText('处理 ai 的任务内容')).toBeVisible()
    expect(within(screen.getByTestId('node-ai')).getByText('已填写')).toBeVisible()
    expect(within(screen.getByTestId('node-custom')).getByText(/future-executor/)).toBeVisible()
    expect(within(screen.getByTestId('node-custom')).getByText('兼容模式')).toBeVisible()
    expect(within(screen.getByTestId('node-empty')).getByText('待配置')).toBeVisible()
    expect(screen.getByLabelText('画布缩放控制')).toBeInTheDocument()
    expect(screen.getByLabelText('流程小地图')).toBeInTheDocument()
  })

  it('retains measured node dimensions across edits for the real MiniMap without serializing them', () => {
    const original = load([node('a'), node('b')])
    render(<EditorCanvas onNodeClick={vi.fn()} />)
    act(() => props().onNodesChange!([
      { id: 'a', type: 'dimensions', dimensions: { width: 240, height: 170 } },
      { id: 'b', type: 'dimensions', dimensions: { width: 240, height: 140 } },
    ]))
    expect(props().nodes!.map(item => item.measured)).toEqual([{ width: 240, height: 170 }, { width: 240, height: 140 }])
    expect(useEditorStore.getState().spec).toEqual(original)
    expect(useEditorStore.getState().canUndo).toBe(false)
    act(() => {
      useEditorStore.getState().updateNode('a', { title: 'Updated title' })
      useEditorStore.getState().selectNode('b')
      useEditorStore.getState().moveNode('a', 25, 40)
    })
    expect(props().nodes![0].measured).toEqual({ width: 240, height: 170 })
    expect(props().nodes![1].measured).toEqual({ width: 240, height: 140 })
    act(() => props().onNodesChange!([{ id: 'a', type: 'dimensions', dimensions: { width: 240, height: 220 } }]))
    expect(props().nodes![0].measured).toEqual({ width: 240, height: 220 })
    expect(useEditorStore.getState().spec!.nodes.every(item => !('measured' in item) && !('width' in item) && !('height' in item))).toBe(true)
  })

  it('resolves SVG and control colors through semantic inline theme tokens', async () => {
    const { compile } = await import('@tailwindcss/node')
    const { build } = await compile('@import "tailwindcss"; @import "@avernet/clawweb-shared/theme.css";', { base: process.cwd(), onDependency() {} })
    const css = build(canvasThemeClasses.split(' '))
    for (const token of ['primary', 'foreground', 'card', 'muted', 'muted-foreground', 'border']) {
      expect(css).toContain(`--canvas-${token}: hsl(var(--${token},`)
    }
    expect(canvasTheme['--xy-edge-stroke' as keyof typeof canvasTheme]).toBe('var(--canvas-muted-foreground)')
  })

  it('renders cyclic and dangling imported graphs without recursive layout failure', () => {
    load([node('a', { dependsOn: ['b'] }), node('b', { dependsOn: ['a', 'missing'] })])
    render(<EditorCanvas onNodeClick={vi.fn()} />)
    expect(props().nodes).toHaveLength(2)
    expect(props().edges).toHaveLength(2)
    expect(screen.getByText(/项配置问题/)).toBeVisible()
    expect(useEditorStore.getState().validationErrors.some((error) => error.type === 'cycle')).toBe(true)
    expect(props().nodes!.every((item) => Number.isFinite(item.position.x) && Number.isFinite(item.position.y))).toBe(true)
  })

  it('keeps the initial long graph at 100% and makes total overview explicit', async () => {
    load(Array.from({ length: 40 }, (_, index) => node(`n${index}`, { dependsOn: index ? [`n${index - 1}`] : [] })))
    render(<EditorCanvas onNodeClick={vi.fn()} />)
    expect(props().fitView).not.toBe(true)
    expect(props().defaultViewport?.zoom).toBe(1)
    expect(flow.fitView).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button', { name: '总览' }))
    expect(flow.fitView).toHaveBeenCalledWith(expect.objectContaining({ minZoom: 0.08, maxZoom: 1 }))
  })

  it('finds nodes by Chinese title and type and focuses at a readable zoom', async () => {
    load([node('a'), node('script', { title: '核对订单', executor: { type: 'cli-script', command: 'check-orders' } })])
    const onNodeClick = vi.fn()
    render(<EditorCanvas onNodeClick={onNodeClick} />)
    const search = screen.getByRole('textbox', { name: '查找画布节点' })
    await userEvent.type(search, '核对')
    await userEvent.click(screen.getByRole('button', { name: /核对订单.*script/ }))
    expect(useEditorStore.getState().selectedNodeId).toBe('script')
    expect(onNodeClick).toHaveBeenCalledWith('script')
    expect(flow.setCenter).toHaveBeenCalledWith(expect.any(Number), expect.any(Number), { zoom: 1, duration: 240 })
    await userEvent.type(search, 'cli-script{Enter}')
    expect(search).toHaveValue('')
    await userEvent.type(search, '不存在')
    expect(screen.getByRole('status')).toHaveTextContent('没有匹配的画布节点')
  })
})

describe('EditorCanvas drag and controlled selection', () => {
  it('transforms actual drop coordinates, adds a valid node and keeps coordinates out of spec', () => {
    load([])
    const onNodeClick = vi.fn()
    render(<EditorCanvas onNodeClick={onNodeClick} />)
    const dropzone = screen.getByTestId('editor-canvas-dropzone')
    const dataTransfer = { getData: vi.fn(() => 'cli-script'), dropEffect: '' }
    fireEvent.dragOver(dropzone, { dataTransfer })
    expect(dataTransfer.dropEffect).toBe('copy')
    const event = createEvent.drop(dropzone, { dataTransfer })
    Object.defineProperties(event, { clientX: { value: 420 }, clientY: { value: 310 } })
    fireEvent(dropzone, event)
    expect(dataTransfer.getData).toHaveBeenCalledWith(NODE_DRAG_TYPE)
    expect(flow.screenToFlowPosition).toHaveBeenCalledExactlyOnceWith({ x: 420, y: 310 })
    const state = useEditorStore.getState(), added = state.spec!.nodes[0]
    expect(added.executor.type).toBe('cli-script')
    expect(added.phase).toMatch(/^P\d+$/)
    expect(added).not.toHaveProperty('_x')
    expect(added).not.toHaveProperty('_y')
    expect(state.positions[added.id]).toEqual({ x: 175, y: 225 })
    expect(state.selectedNodeId).toBe(added.id)
    expect(onNodeClick).toHaveBeenCalledWith(added.id)
    expect(flow.setCenter).not.toHaveBeenCalled()
    act(() => useEditorStore.getState().undo())
    expect(useEditorStore.getState().spec!.nodes).toHaveLength(0)
  })

  it('ignores an unrelated drop instead of manufacturing an executor', () => {
    load([])
    render(<EditorCanvas onNodeClick={vi.fn()} />)
    fireEvent.drop(screen.getByTestId('editor-canvas-dropzone'), { dataTransfer: { getData: () => 'text/plain' } })
    expect(useEditorStore.getState().spec!.nodes).toHaveLength(0)
    expect(flow.screenToFlowPosition).not.toHaveBeenCalled()
  })

  it('keeps multiple drag position changes in one undo step and preserves legacy coordinates', () => {
    const initial = load([node('a', { _x: 20, _y: 30 })])
    render(<EditorCanvas onNodeClick={vi.fn()} />)
    act(() => {
      props().onNodeDragStart!({} as never, props().nodes![0], [])
      props().onNodesChange!([{ type: 'position', id: 'a', position: { x: 80, y: 90 }, dragging: true }])
      props().onNodesChange!([{ type: 'position', id: 'a', position: { x: 160, y: 210 }, dragging: true }])
      props().onNodeDragStop!({} as never, { ...props().nodes![0], position: { x: 180, y: 230 } }, [])
    })
    expect(props().nodes![0].position).toEqual({ x: 180, y: 230 })
    expect(useEditorStore.getState().spec).toEqual(initial)
    expect(useEditorStore.getState().isDirty).toBe(false)
    act(() => useEditorStore.getState().undo())
    expect(useEditorStore.getState().positions.a).toEqual({ x: 20, y: 30 })
    expect(useEditorStore.getState().canUndo).toBe(false)
    act(() => useEditorStore.getState().redo())
    expect(useEditorStore.getState().positions.a).toEqual({ x: 180, y: 230 })
  })

  it('reflects selection callbacks in controlled nodes so keyboard deletion works after confirmation', async () => {
    load([node('a'), node('b')])
    const onNodeClick = vi.fn()
    render(<EditorCanvas onNodeClick={onNodeClick} />)
    act(() => props().onNodesChange!([{ type: 'select', id: 'a', selected: true }]))
    expect(props().nodes![0].selected).toBe(true)
    expect(screen.getByTestId('node-a')).toHaveAttribute('data-selected', 'true')
    expect(onNodeClick).toHaveBeenCalledWith('a')
    vi.mocked(window.confirm).mockReturnValueOnce(false)
    await act(async () => fireEvent.keyDown(screen.getByTestId('react-flow'), { key: 'Delete' }))
    expect(useEditorStore.getState().spec!.nodes).toHaveLength(2)
    await act(async () => fireEvent.keyDown(screen.getByTestId('react-flow'), { key: 'Delete' }))
    expect(window.confirm).toHaveBeenCalledTimes(2)
    expect(useEditorStore.getState().spec!.nodes.map((item) => item.id)).toEqual(['b'])
  })

  it('honors guarded node deletion and does not remove its incident edges on rejection', async () => {
    load([node('a'), node('b', { dependsOn: ['a'], executor: { type: 'embedded-agent', prompt: '{{nodeOutput.a.result}}' } })])
    render(<EditorCanvas onNodeClick={vi.fn()} />)
    await userEvent.click(screen.getByRole('button', { name: '选择 a' }))
    await act(async () => fireEvent.keyDown(screen.getByTestId('react-flow'), { key: 'Delete' }))
    expect(window.confirm).toHaveBeenCalled()
    expect(useEditorStore.getState().spec!.nodes).toHaveLength(2)
    expect(useEditorStore.getState().spec!.nodes[1].dependsOn).toEqual(['a'])
    expect(screen.getByRole('alert')).toHaveTextContent(/仍有引用/)
    expect(props().nodes![0].selected).toBe(true)
  })

  it('closes an in-progress drag transaction on unmount', () => {
    load([node('a')])
    const { unmount } = render(<EditorCanvas onNodeClick={vi.fn()} />)
    act(() => { props().onNodeDragStart!({} as never, props().nodes![0], []); props().onNodesChange!([{ type: 'position', id: 'a', position: { x: 500, y: 600 }, dragging: true }]) })
    unmount()
    expect(useEditorStore.getState().canUndo).toBe(true)
    expect(useEditorStore.getState()._transactionDepth).toBe(0)
  })
})

describe('EditorCanvas connections and edge actions', () => {
  it('only selects an edge on click, then deletes via an explicit action with undo', async () => {
    load([node('a'), node('b', { dependsOn: ['a'] })])
    render(<EditorCanvas onNodeClick={vi.fn()} />)
    await userEvent.click(screen.getByTestId('edge-a-b'))
    expect(useEditorStore.getState().spec!.nodes[1].dependsOn).toEqual(['a'])
    expect(props().edges![0].selected).toBe(true)
    expect(screen.getByRole('button', { name: '删除连线' })).toBeVisible()
    await userEvent.click(screen.getByRole('button', { name: '删除连线' }))
    expect(useEditorStore.getState().spec!.nodes[1].dependsOn).toEqual([])
    expect(window.confirm).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button', { name: '撤销' }))
    expect(useEditorStore.getState().spec!.nodes[1].dependsOn).toEqual(['a'])
  })

  it('accepts keyboard edge selection changes and explicit Delete', async () => {
    load([node('a'), node('b', { dependsOn: ['a'] })])
    render(<EditorCanvas onNodeClick={vi.fn()} />)
    act(() => props().onEdgesChange!([{ type: 'select', id: props().edges![0].id, selected: true }]))
    expect(props().edges![0].selected).toBe(true)
    await act(async () => fireEvent.keyDown(screen.getByTestId('react-flow'), { key: 'Delete' }))
    expect(useEditorStore.getState().spec!.nodes[1].dependsOn).toEqual([])
  })

  it('adds an ordinary dependency through the real onConnect callback', () => {
    load([node('a'), node('b')])
    render(<EditorCanvas onNodeClick={vi.fn()} />)
    expect(props().isValidConnection!(connection('a', 'b'))).toBe(true)
    act(() => props().onConnect!(connection('a', 'b')))
    expect(useEditorStore.getState().spec!.nodes[1].dependsOn).toEqual(['a'])
  })

  it.each([
    ['self', 'a', 'a'], ['duplicate', 'a', 'b'], ['missing', 'a', 'absent'], ['cycle', 'b', 'a'],
  ])('rejects %s connections through both validation and mutation paths', (_reason, source, target) => {
    const initial = load([node('a'), node('b', { dependsOn: ['a'] })])
    render(<EditorCanvas onNodeClick={vi.fn()} />)
    expect(props().isValidConnection!(connection(source, target))).toBe(false)
    act(() => props().onConnect!(connection(source, target)))
    expect(useEditorStore.getState().spec).toEqual(initial)
    expect(screen.getByRole('alert')).toBeVisible()
  })

  it('uses encoded branch handles and only labels edges whose source owns the branch', () => {
    const branchId = 'source / 同意'
    load([node('route', { onResult: modernBranches(branchId) }), node('ordinary'), node('target', { branchId, dependsOn: ['route', 'ordinary'] }), node('new')])
    render(<EditorCanvas onNodeClick={vi.fn()} />)
    const handles = within(screen.getByTestId('node-route')).getAllByTestId('handle')
    expect(handles.some((handle) => handle.getAttribute('data-handle-id') === branchHandleId(branchId))).toBe(true)
    expect(branchHandleId(branchId)).not.toBe(GENERAL_SOURCE_HANDLE)
    const branchEdge = props().edges!.find((edge) => edge.source === 'route')!
    const ordinaryEdge = props().edges!.find((edge) => edge.source === 'ordinary')!
    expect(branchEdge.label).toBe(branchId)
    expect(branchEdge.sourceHandle).toBe(branchHandleId(branchId))
    expect(ordinaryEdge.label).toBeUndefined()
    expect(ordinaryEdge.sourceHandle).toBe(GENERAL_SOURCE_HANDLE)
    act(() => props().onConnect!(connection('route', 'new', branchHandleId(branchId))))
    expect(useEditorStore.getState().spec!.nodes.find((item) => item.id === 'new')).toMatchObject({ branchId, dependsOn: ['route'] })
  })

  it('rejects incompatible ownership without treating complete as terminal flow', () => {
    load([node('route', { onResult: { branches: [
      { branchId: 'yes', match: { result: true } },
      { branchId: 'stop', match: {}, complete: true },
    ] } as unknown as WorkflowNode['onResult'] }), node('target', { branchId: 'other' })])
    render(<EditorCanvas onNodeClick={vi.fn()} />)
    expect(screen.getByLabelText('分支 stop 输出')).toHaveAttribute('data-connectable', 'true')
    expect(props().isValidConnection!(connection('route', 'target', branchHandleId('yes')))).toBe(false)
    act(() => props().onConnect!(connection('route', 'target', branchHandleId('yes'))))
    expect(useEditorStore.getState().spec!.nodes[1].branchId).toBe('other')
    expect(useEditorStore.getState().spec!.nodes[1].dependsOn).toBeUndefined()
    expect(screen.getByRole('alert')).toHaveTextContent('branchId')
  })

  it('surfaces an invalid gesture error from onConnectEnd without mutating the graph', () => {
    const initial = load([node('a'), node('b', { dependsOn: ['a'] })])
    render(<EditorCanvas onNodeClick={vi.fn()} />)
    act(() => props().onConnectEnd!({} as never, {
      isValid: false, fromNode: { id: 'b' }, toNode: { id: 'a' },
      fromHandle: { type: 'source', id: GENERAL_SOURCE_HANDLE }, toHandle: { type: 'target', id: null },
    } as never))
    expect(screen.getByRole('alert')).toHaveTextContent('cycle')
    expect(useEditorStore.getState().spec).toEqual(initial)
  })

  it('preserves legacy branches without inventing ports and shows effective trigger rules', () => {
    load([node('legacy', { onResult: [{ value: 'yes', target: 'single' }] }), node('plain'), node('single', { dependsOn: ['legacy'], join: 'any' }), node('merge', { dependsOn: ['single', 'plain'] }), node('explicit', { dependsOn: ['merge'], join: 'any', triggerRule: 'all_done' })])
    render(<EditorCanvas onNodeClick={vi.fn()} />)
    expect(within(screen.getByTestId('node-legacy')).getByText('兼容模式')).toBeVisible()
    expect(within(screen.getByTestId('node-legacy')).getAllByTestId('handle')).toHaveLength(2)
    expect(within(screen.getByTestId('node-single')).getByText('任一成功后继续')).toBeVisible()
    expect(within(screen.getByTestId('node-merge')).getByText('全部成功后继续')).toBeVisible()
    expect(within(screen.getByTestId('node-explicit')).getByText('全部结束后继续')).toBeVisible()
  })

  it('does not delete a previous edge after a palette addition selects a node', async () => {
    load([node('a'), node('b', { dependsOn: ['a'] })])
    render(<><NodePalette /><EditorCanvas onNodeClick={vi.fn()} /></>)
    await userEvent.click(screen.getByTestId('edge-a-b'))
    await userEvent.click(screen.getByRole('button', { name: '添加完成' }))
    expect(props().edges![0].selected).toBe(false)
    expect(screen.queryByRole('button', { name: '删除连线' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '删除节点' })).toBeVisible()
    expect(flow.setCenter).toHaveBeenCalled()
  })

  it('handles a history shortcut once without bubbling to the host Editor shortcut', () => {
    load([node('a')])
    useEditorStore.getState().updateNode('a', { title: '第一次修改' })
    useEditorStore.getState().updateNode('a', { title: '第二次修改' })
    render(<EditorCanvas onNodeClick={vi.fn()} />)
    const hostShortcut = vi.fn(() => useEditorStore.getState().undo())
    window.addEventListener('keydown', hostShortcut)
    try {
      fireEvent.keyDown(screen.getByTestId('react-flow'), { key: 'z', ctrlKey: true })
      expect(hostShortcut).not.toHaveBeenCalled()
      expect(useEditorStore.getState().spec!.nodes[0].title).toBe('第一次修改')
      expect(useEditorStore.getState().canUndo).toBe(true)
    } finally { window.removeEventListener('keydown', hostShortcut) }
  })

  it('protects pending drafts from history/delete shortcuts and leaves typing undo alone', async () => {
    load([node('a')])
    useEditorStore.getState().selectNode('a')
    useEditorStore.getState().updateNode('a', { title: '修改后的标题' })
    useEditorStore.getState().setPendingEdit('node:a:json', '未应用 JSON')
    render(<EditorCanvas onNodeClick={vi.fn()} />)
    expect(screen.getByRole('button', { name: '撤销' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '删除节点' })).toBeDisabled()
    fireEvent.keyDown(screen.getByRole('textbox', { name: '查找画布节点' }), { key: 'z', ctrlKey: true })
    expect(useEditorStore.getState().actionError).toBeNull()
    await act(async () => fireEvent.keyDown(screen.getByTestId('react-flow'), { key: 'Delete' }))
    expect(window.confirm).not.toHaveBeenCalled()
    expect(screen.getByRole('alert')).toHaveTextContent('未应用')
    expect(useEditorStore.getState().spec!.nodes).toHaveLength(1)
    expect(useEditorStore.getState().pendingEdits).toEqual({ 'node:a:json': '未应用 JSON' })
  })
})
