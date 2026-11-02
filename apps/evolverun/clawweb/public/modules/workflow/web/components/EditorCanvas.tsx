import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  ReactFlow, Background, Controls, MiniMap, ReactFlowProvider, useReactFlow,
  type Edge, type Connection, type OnNodesChange, type OnEdgesChange, type OnBeforeDelete,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { useEditorStore } from '../editor/store'
import { canConnect, computeDagLayout, dependencies } from '../editor/graph'
import { getResultBranches } from '../editor/contracts'
import { NODE_CATALOG, createEditorNode } from '../editor/nodeCatalog'
import EditorFlowNode, { type EditorCanvasNode } from './editor/EditorFlowNode'
import EditorCanvasToolbar from './editor/EditorCanvasToolbar'
import { branchHandleId, branchIdFromHandle, canvasTheme, canvasThemeClasses, GENERAL_SOURCE_HANDLE, NODE_DRAG_TYPE } from './editor/CanvasPresentation'

const nodeTypes = { editorNode: EditorFlowNode }

export interface EditorCanvasProps {
  onNodeClick: (nodeId: string) => void
}

function EditorCanvasInner({ onNodeClick }: EditorCanvasProps) {
  const {
    spec, positions, selectedNodeId, validationErrors, actionError, pendingEdits,
    canUndo, canRedo, undo, redo, beginTransaction, endTransaction, setActionError,
    addNode, addEdge, removeNode, removeEdge, moveNode, selectNode,
  } = useEditorStore()
  const [selectedEdgeId, setSelectedEdgeId] = useState<string | null>(null)
  // MiniMap reads dimensions from the controlled user node, not only Flow's
  // internal node. Keep measurements UI-only and stable when spec data changes.
  const [measurements, setMeasurements] = useState<Record<string, { width: number; height: number }>>({})
  const dragActive = useRef(false)
  const droppedNodeId = useRef<string | null>(null)
  const { screenToFlowPosition, setCenter, fitView, getNode } = useReactFlow<EditorCanvasNode>()
  const hasPendingEdits = Object.keys(pendingEdits).length > 0
  const layout = useMemo(() => computeDagLayout(spec?.nodes ?? []), [spec?.nodes])

  const flowNodes: EditorCanvasNode[] = useMemo(() => (spec?.nodes ?? []).map((node) => {
    const legacy = typeof node._x === 'number' && typeof node._y === 'number' && Number.isFinite(node._x) && Number.isFinite(node._y)
      ? { x: node._x, y: node._y } : undefined
    return {
      id: node.id, type: 'editorNode',
      position: positions[node.id] ?? legacy ?? layout.get(node.id) ?? { x: 0, y: 0 },
      selected: selectedNodeId === node.id,
      measured: measurements[node.id],
      ariaLabel: `${node.title || node.id} · ${node.executor?.type ?? '未指定类型'}`,
      data: { node, errors: validationErrors.filter((error) => error.nodeId === node.id).map((error) => error.message) },
    }
  }), [spec?.nodes, positions, selectedNodeId, validationErrors, layout, measurements])

  const flowEdges: Edge[] = useMemo(() => {
    const nodesById = new Map((spec?.nodes ?? []).map((node) => [node.id, node]))
    return (spec?.nodes ?? []).flatMap((node) => [...new Set(dependencies(node))].flatMap((source) => {
      const upstream = nodesById.get(source)
      // Malformed imports retain their data, but dangling edges cannot be drawn.
      if (!upstream) return []
      const branch = node.branchId && getResultBranches(upstream)?.find((candidate) => candidate.branchId === node.branchId)
      const id = JSON.stringify([source, node.id])
      return [{
        id, source, target: node.id, sourceHandle: branch ? branchHandleId(branch.branchId) : GENERAL_SOURCE_HANDLE,
        selected: selectedEdgeId === id, label: branch ? branch.branchId : undefined,
        ariaLabel: `${upstream.title || source} → ${node.title || node.id}${branch ? ` · ${branch.branchId}` : ''}`,
        labelStyle: { fontSize: 11, fill: 'var(--canvas-foreground)' },
        labelBgStyle: { fill: 'var(--canvas-card)' }, labelBgPadding: [6, 3] as [number, number], labelBgBorderRadius: 4,
        style: { strokeWidth: selectedEdgeId === id ? 2.5 : 1.5 }, interactionWidth: 24,
      }]
    }))
  }, [spec?.nodes, selectedEdgeId])
  const selectedEdge = flowEdges.find((edge) => edge.id === selectedEdgeId)
  const selection = selectedEdge ? 'edge' : selectedNodeId ? 'node' : null

  const chooseNode = useCallback((nodeId: string) => {
    selectNode(nodeId)
    if (useEditorStore.getState().selectedNodeId !== nodeId) return
    setSelectedEdgeId(null)
    onNodeClick(nodeId)
  }, [selectNode, onNodeClick])

  const focusNode = useCallback((nodeId: string) => {
    const node = flowNodes.find((item) => item.id === nodeId)
    if (!node) return
    const measured = getNode(nodeId)?.measured
    void setCenter(node.position.x + (measured?.width ?? 240) / 2, node.position.y + (measured?.height ?? 140) / 2, { zoom: 1, duration: 240 })
  }, [flowNodes, getNode, setCenter])

  // Adding via the palette should not leave the selected node off-screen.
  const previousIds = useRef(new Set(spec?.nodes.map((node) => node.id)))
  useEffect(() => {
    if (selectedNodeId && !previousIds.current.has(selectedNodeId) && droppedNodeId.current !== selectedNodeId) focusNode(selectedNodeId)
    droppedNodeId.current = null
    previousIds.current = new Set(spec?.nodes.map((node) => node.id))
  }, [spec?.nodes, selectedNodeId, focusNode])
  useEffect(() => { if (selectedNodeId) setSelectedEdgeId(null) }, [selectedNodeId])
  useEffect(() => () => { if (dragActive.current) endTransaction() }, [endTransaction])

  const connectionError = useCallback((connection: Connection | Edge) => {
    const current = useEditorStore.getState().spec
    if (!current) return '请先选择工作流'
    const branchId = branchIdFromHandle(connection.sourceHandle)
    if (connection.sourceHandle && connection.sourceHandle !== GENERAL_SOURCE_HANDLE && branchId === undefined) return '无法识别的分支端口'
    return canConnect(current, connection.source, connection.target, branchId)
  }, [])
  const onConnect = useCallback((connection: Connection) => {
    const error = connectionError(connection)
    if (error) { setActionError(error); return }
    addEdge(connection.source, connection.target, branchIdFromHandle(connection.sourceHandle))
  }, [connectionError, setActionError, addEdge])

  const startDrag = useCallback(() => {
    if (dragActive.current) return
    beginTransaction()
    dragActive.current = true
  }, [beginTransaction])
  const onNodesChange: OnNodesChange<EditorCanvasNode> = useCallback((changes) => {
    setMeasurements((previous) => {
      let next = previous
      for (const change of changes) {
        if (change.type !== 'dimensions' || !change.dimensions) continue
        const { width, height } = change.dimensions
        if (!Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0) continue
        if (previous[change.id]?.width === width && previous[change.id]?.height === height) continue
        if (next === previous) next = { ...previous }
        next[change.id] = { width, height }
      }
      return next
    })
    // Handle deselections before selections: a batch may contain both.
    const chosen = changes.find((change) => change.type === 'select' && change.selected)
    if (chosen && chosen.type === 'select') chooseNode(chosen.id)
    else if (changes.some((change) => change.type === 'select' && !change.selected && change.id === useEditorStore.getState().selectedNodeId)) selectNode(null)
    for (const change of changes) {
      if (change.type === 'position' && change.position) {
        if (change.dragging) startDrag()
        moveNode(change.id, change.position.x, change.position.y)
      }
    }
  }, [chooseNode, selectNode, moveNode, startDrag])
  const onEdgesChange: OnEdgesChange = useCallback((changes) => {
    const chosen = changes.find((change) => change.type === 'select' && change.selected)
    if (chosen && chosen.type === 'select') {
      selectNode(null)
      if (useEditorStore.getState().selectedNodeId === null) setSelectedEdgeId(chosen.id)
    } else if (changes.some((change) => change.type === 'select' && !change.selected && change.id === selectedEdgeId)) setSelectedEdgeId(null)
  }, [selectNode, selectedEdgeId])

  const deleteSelection = useCallback((nodeIds: string[], edges: Edge[]) => {
    if (Object.keys(useEditorStore.getState().pendingEdits).length) {
      setActionError('请先应用或放弃未应用的编辑，再删除节点或连线。')
      return
    }
    if (nodeIds.length) {
      const titles = nodeIds.map((id) => useEditorStore.getState().spec?.nodes.find((node) => node.id === id)?.title || id)
      if (!window.confirm(`删除节点“${titles.join('、')}”？已知依赖会被清理；存在其他引用时将阻止删除。`)) return
      beginTransaction()
      try { for (const id of nodeIds) removeNode(id) } finally { endTransaction() }
      // Never separately remove React Flow's incident edges: a guarded node may remain.
    } else {
      beginTransaction()
      try { for (const edge of edges) removeEdge(edge.source, edge.target) } finally { endTransaction() }
      setSelectedEdgeId(null)
    }
  }, [beginTransaction, endTransaction, removeNode, removeEdge, setActionError])
  const onBeforeDelete: OnBeforeDelete<EditorCanvasNode, Edge> = useCallback(async ({ nodes, edges }) => {
    deleteSelection(nodes.map((node) => node.id), edges)
    // The store is authoritative, including reference checks and draft protection.
    return false
  }, [deleteSelection])

  const onDrop = useCallback((event: React.DragEvent) => {
    event.preventDefault()
    const type = event.dataTransfer.getData(NODE_DRAG_TYPE)
    if (!NODE_CATALOG.some((entry) => entry.type === type)) return
    const position = screenToFlowPosition({ x: event.clientX, y: event.clientY })
    const node = createEditorNode(type, position)
    droppedNodeId.current = node.id
    beginTransaction()
    try {
      addNode(node)
      if (useEditorStore.getState().spec?.nodes.some((item) => item.id === node.id)) {
        moveNode(node.id, position.x, position.y)
        chooseNode(node.id)
      }
    } finally { endTransaction() }
  }, [screenToFlowPosition, beginTransaction, endTransaction, addNode, moveNode, chooseNode])

  if (!spec) return <div className="flex h-full items-center justify-center bg-muted/30 text-sm text-muted-foreground">从侧边栏选择工作流或创建新工作流</div>
  const first = flowNodes[0]?.position ?? { x: 0, y: 0 }

  return (
    <section aria-label="工作流画布" className="flex h-full min-h-0 w-full min-w-0 flex-col bg-background text-foreground"
      onKeyDown={(event) => {
        const target = event.target as HTMLElement
        if (target.closest('input, textarea, select, [contenteditable="true"]')) return
        if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'z') {
          event.preventDefault()
          event.stopPropagation() // Do not also trigger the host Editor's history shortcut.
          if (hasPendingEdits) { setActionError('请先应用或放弃未应用的编辑，再撤销或重做。'); return }
          if (event.shiftKey) redo(); else undo()
        }
      }}>
      <EditorCanvasToolbar nodes={spec.nodes} canUndo={canUndo} canRedo={canRedo} hasPendingEdits={hasPendingEdits} selection={selection}
        onUndo={undo} onRedo={redo} onFocus={(id) => { chooseNode(id); focusNode(id) }}
        onFit={() => { void fitView({ padding: 0.2, minZoom: 0.08, maxZoom: 1, duration: 240 }) }}
        onReadableZoom={() => { const id = selectedNodeId ?? spec.nodes[0]?.id; if (id) focusNode(id) }}
        onDelete={() => deleteSelection(selectedEdge ? [] : selectedNodeId ? [selectedNodeId] : [], selectedEdge ? [selectedEdge] : [])} />
      {actionError && <div role="alert" className="flex items-start justify-between gap-3 border-b border-destructive/20 bg-destructive/10 px-3 py-2 text-xs text-destructive"><span>{actionError}</span><button type="button" onClick={() => setActionError(null)} aria-label="关闭操作提示" className="shrink-0 rounded px-1 focus-visible:ring-2 focus-visible:ring-ring">×</button></div>}
      {validationErrors.length > 0 && <details className="border-b border-input bg-card px-3 py-2 text-xs text-destructive"><summary className="cursor-pointer">{validationErrors.length} 项配置问题（原始定义已保留）</summary><ul className="mt-2 max-h-28 list-inside list-disc overflow-auto">{validationErrors.map((error, index) => <li key={index}>{error.nodeId ? `${error.nodeId}: ` : ''}{error.message}</li>)}</ul></details>}
      <div className="relative min-h-48 flex-1" data-testid="editor-canvas-dropzone" onDragOver={(event) => { event.preventDefault(); event.dataTransfer.dropEffect = 'copy' }} onDrop={onDrop}>
        <ReactFlow<EditorCanvasNode, Edge> nodes={flowNodes} edges={flowEdges} nodeTypes={nodeTypes} style={canvasTheme} className={canvasThemeClasses}
          onConnect={onConnect} isValidConnection={(connection) => connectionError(connection) === null}
          onConnectEnd={(_event, state) => {
            if (state.isValid || !state.fromNode || !state.toNode) return
            const fromSource = state.fromHandle?.type === 'source'
            const error = connectionError({ source: (fromSource ? state.fromNode : state.toNode).id, target: (fromSource ? state.toNode : state.fromNode).id, sourceHandle: (fromSource ? state.fromHandle : state.toHandle)?.id ?? null, targetHandle: null })
            if (error) setActionError(error)
          }}
          onNodeClick={(_event, node) => chooseNode(node.id)} onNodesChange={onNodesChange} onEdgesChange={onEdgesChange}
          onNodeDragStart={startDrag} onNodeDragStop={(_event, node) => {
            moveNode(node.id, node.position.x, node.position.y)
            if (dragActive.current) { dragActive.current = false; endTransaction() }
          }}
          onEdgeClick={(_event, edge) => { selectNode(null); if (useEditorStore.getState().selectedNodeId === null) setSelectedEdgeId(edge.id) }}
          onPaneClick={() => { selectNode(null); setSelectedEdgeId(null) }} onBeforeDelete={onBeforeDelete}
          deleteKeyCode={['Delete', 'Backspace']} multiSelectionKeyCode={null} selectionKeyCode={null}
          defaultViewport={{ x: typeof spec.nodes[0]?._x === 'number' ? 48 - first.x : 24, y: 64 - first.y, zoom: 1 }} minZoom={0.08} maxZoom={2} proOptions={{ hideAttribution: true }}
          ariaLabelConfig={{ 'controls.zoomIn.ariaLabel': '放大', 'controls.zoomOut.ariaLabel': '缩小', 'controls.fitView.ariaLabel': '适应全部节点', 'minimap.ariaLabel': '流程小地图' }}>
          <Background gap={20} size={1} />
          <Controls showInteractive={false} orientation="horizontal" fitViewOptions={{ padding: 0.2, minZoom: 0.08, maxZoom: 1 }} aria-label="画布缩放控制" className="overflow-hidden rounded-lg border border-input shadow-sm" />
          <MiniMap pannable zoomable aria-label="流程小地图" nodeColor={(node) => node.selected ? 'var(--canvas-primary)' : 'var(--canvas-muted-foreground)'} className="overflow-hidden rounded-lg border border-input" style={{ width: 140, height: 96 }} />
        </ReactFlow>
        {!spec.nodes.length && <div className="pointer-events-none absolute inset-0 flex items-center justify-center"><div className="rounded-xl border border-dashed border-input bg-card/90 px-8 py-6 text-center"><p className="text-sm font-medium">从一个节点开始</p><p className="mt-2 text-xs text-muted-foreground">点击左侧节点，或将它拖到这里</p></div></div>}
      </div>
      <footer className="flex flex-wrap items-center gap-3 border-t border-input bg-card px-3 py-2 text-[10px] text-muted-foreground"><span>{spec.nodes.length} 个节点 · {flowEdges.length} 条连线</span>{selectedEdge && <span className="text-primary">已选择连线 · Delete 删除</span>}<span className="ml-auto">拖动空白处平移 · Delete 删除 · Ctrl/⌘ Z 撤销</span></footer>
    </section>
  )
}

export default function EditorCanvas(props: EditorCanvasProps) {
  const workflowId = useEditorStore((state) => state.spec?.id)
  return <ReactFlowProvider key={workflowId ?? 'empty'}><EditorCanvasInner {...props} /></ReactFlowProvider>
}
