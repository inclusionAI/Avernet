import { useCallback, useMemo, useRef } from 'react'
import {
  ReactFlow,
  Background,
  Controls,
  type Node,
  type Edge,
  type NodeProps,
  type OnConnect,
  Handle,
  Position,
  useReactFlow,
  ReactFlowProvider,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { useEditorStore, type EditorNode as StoreEditorNode, type ExecutorType, EXECUTOR_TYPES } from '../editor/store'

/** Layout constants for auto-arranging DAG nodes */
const LAYOUT_LAYER_GAP_Y = 160   // vertical gap between layers
const LAYOUT_NODE_GAP_X = 260    // horizontal gap between nodes in same layer
const LAYOUT_BRANCH_OFFSET_X = 60 // extra horizontal offset for branch nodes
const LAYOUT_ORIGIN_X = 80        // left margin
const LAYOUT_ORIGIN_Y = 60        // top margin

/**
 * Compute DAG layout positions using topological layering.
 *
 * Algorithm:
 * 1. Build adjacency from `dependsOn` → assign each node to the layer after its deepest dependency
 * 2. Group nodes by layer, then spread horizontally within each layer
 * 3. Branch nodes (same branchId) are grouped together and offset slightly
 *
 * Returns a map of nodeId → { x, y }.
 */
function computeDagLayout(nodes: Array<{ id: string; dependsOn?: string[]; branchId?: string }>): Map<string, { x: number; y: number }> {
  const positions = new Map<string, { x: number; y: number }>()
  if (nodes.length === 0) return positions

  const nodeMap = new Map(nodes.map(n => [n.id, n]))
  const layerOf = new Map<string, number>()

  // Compute layer for each node (longest path from root)
  function getLayer(nodeId: string): number {
    if (layerOf.has(nodeId)) return layerOf.get(nodeId)!
    const node = nodeMap.get(nodeId)
    if (!node || !node.dependsOn || node.dependsOn.length === 0) {
      layerOf.set(nodeId, 0)
      return 0
    }
    let maxDepLayer = 0
    for (const dep of node.dependsOn) {
      const depLayer = getLayer(dep)
      if (depLayer >= maxDepLayer) maxDepLayer = depLayer + 1
      else if (depLayer + 1 > maxDepLayer) maxDepLayer = depLayer + 1
    }
    // Ensure node is at least one layer after all its dependencies
    let layer = 0
    for (const dep of node.dependsOn) {
      layer = Math.max(layer, getLayer(dep) + 1)
    }
    layerOf.set(nodeId, layer)
    return layer
  }

  // Compute layers for all nodes
  for (const node of nodes) {
    getLayer(node.id)
  }

  // Group nodes by layer
  const maxLayer = Math.max(...layerOf.values(), 0)
  const layers: Array<string[]> = Array.from({ length: maxLayer + 1 }, () => [])
  for (const node of nodes) {
    const layer = layerOf.get(node.id) ?? 0
    layers[layer].push(node.id)
  }

  // Sort within each layer: group by branchId, then by original order
  for (const layer of layers) {
    layer.sort((a, b) => {
      const nodeA = nodeMap.get(a)!
      const nodeB = nodeMap.get(b)!
      const branchA = nodeA.branchId ?? ''
      const branchB = nodeB.branchId ?? ''
      if (branchA !== branchB) {
        // Non-branch nodes first, then branch groups together
        if (!branchA) return -1
        if (!branchB) return 1
        return branchA.localeCompare(branchB)
      }
      return 0
    })
  }

  // Assign positions
  for (let layerIdx = 0; layerIdx < layers.length; layerIdx++) {
    const layerNodes = layers[layerIdx]
    // Center the layer horizontally
    const totalWidth = (layerNodes.length - 1) * LAYOUT_NODE_GAP_X
    const startX = LAYOUT_ORIGIN_X + Math.max(0, (800 - totalWidth) / 2 - LAYOUT_ORIGIN_X)

    // Track branch group offsets
    let currentBranchOffset = 0

    for (let nodeIdx = 0; nodeIdx < layerNodes.length; nodeIdx++) {
      const id = layerNodes[nodeIdx]
      const node = nodeMap.get(id)!
      const branchId = node.branchId

      // Check if previous node in same branch group
      const prevId = nodeIdx > 0 ? layerNodes[nodeIdx - 1] : null
      const prevBranch = prevId ? nodeMap.get(prevId)?.branchId : null
      if (branchId && branchId === prevBranch) {
        // Same branch group — tighter spacing
        currentBranchOffset += LAYOUT_BRANCH_OFFSET_X
      } else {
        currentBranchOffset = 0
      }

      const x = startX + nodeIdx * LAYOUT_NODE_GAP_X + currentBranchOffset
      const y = LAYOUT_ORIGIN_Y + layerIdx * LAYOUT_LAYER_GAP_Y
      positions.set(id, { x, y })
    }
  }

  return positions
}

const EXECUTOR_COLORS: Record<string, { bg: string; border: string; text: string }> = {
  'embedded-agent': { bg: '#eff6ff', border: '#3b82f6', text: '#1d4ed8' },
  action: { bg: '#faf5ff', border: '#a855f7', text: '#7e22ce' },
  human: { bg: '#fffbeb', border: '#f59e0b', text: '#b45309' },
  'loop-group': { bg: '#f0fdfa', border: '#14b8a6', text: '#0f766e' },
  collaboration: { bg: '#fff1f2', border: '#f43f5e', text: '#be123c' },
  done: { bg: '#f9fafb', border: '#9ca3af', text: '#4b5563' },
  subagent: { bg: '#eef2ff', border: '#6366f1', text: '#4338ca' },
  'bcs-route': { bg: '#ecfeff', border: '#06b6d4', text: '#0e7490' },
  'mcp-call': { bg: '#f5f3ff', border: '#8b5cf6', text: '#6d28d9' },
  'cli-script': { bg: '#f7fee7', border: '#84cc16', text: '#4d7c0f' },
  subworkflow: { bg: '#fff7ed', border: '#f97316', text: '#c2410c' },
  approval: { bg: '#fdf2f8', border: '#ec4899', text: '#be185d' },
}

interface EditorNodeData {
  label: string
  nodeId: string
  executorType: string
  executorDetail: string
  branchId: string | null
  hasError: boolean
  [key: string]: unknown
}

type EditorNode = Node<EditorNodeData>

function EditorNodeComponent({ data }: NodeProps<EditorNode>) {
  const colors = EXECUTOR_COLORS[data.executorType] ?? EXECUTOR_COLORS.done
  return (
    <div
      className="min-w-[140px] rounded-lg border-2 bg-white px-3 py-2 shadow-sm"
      style={{ borderColor: colors.border, backgroundColor: colors.bg }}
    >
      <Handle type="target" position={Position.Top} className="!bg-gray-400" />
      <div className="text-center">
        <div className="font-medium text-xs" style={{ color: colors.text }}>
          {data.label}
        </div>
        <div className="mt-0.5 text-[10px]" style={{ color: colors.text, opacity: 0.7 }}>
          {data.executorType}
        </div>
        {data.executorDetail && (
          <div className="mt-0.5 truncate text-[9px] font-mono" style={{ color: colors.text, opacity: 0.6 }}>
            {data.executorDetail}
          </div>
        )}
        {data.branchId && (
          <div className="mt-0.5 inline-block rounded-full bg-white/60 px-1.5 py-0.5 text-[9px] font-medium" style={{ color: colors.text }}>
            {data.branchId}
          </div>
        )}
        {data.hasError && (
          <div className="mt-0.5 text-[10px] font-bold text-red-500">!</div>
        )}
      </div>
      <Handle type="source" position={Position.Bottom} className="!bg-gray-400" />
    </div>
  )
}

const nodeTypes = { editorNode: EditorNodeComponent }

interface EditorCanvasProps {
  onNodeClick: (nodeId: string) => void
}

function EditorCanvasInner({ onNodeClick }: EditorCanvasProps) {
  const { spec, addNode, addEdge, removeNode, removeEdge, moveNode, selectedNodeId, selectNode, validationErrors } = useEditorStore()
  const wrapperRef = useRef<HTMLDivElement>(null)
  const { screenToFlowPosition } = useReactFlow()

  const flowNodes: EditorNode[] = useMemo(() => {
    if (!spec) return []
    const errorNodeIds = new Set(
      validationErrors.filter((e) => e.nodeId).map((e) => e.nodeId!),
    )

    // Compute DAG layout for nodes that lack saved positions
    const nodesWithPositions = new Set<string>()
    for (const node of spec.nodes) {
      const editorNode = node as StoreEditorNode
      if (editorNode._x != null && editorNode._y != null) {
        nodesWithPositions.add(node.id)
      }
    }

    // Compute auto-layout for all nodes — will be used as fallback
    const layoutPositions = computeDagLayout(
      spec.nodes.map(n => ({
        id: n.id,
        dependsOn: n.dependsOn,
        branchId: (n as StoreEditorNode).branchId,
      }))
    )

    return spec.nodes.map((node) => {
      const exec = node.executor as Record<string, unknown> | undefined
      let executorDetail = ''
      const execType = node.executor?.type ?? 'done'
      if (execType === 'mcp-call' && exec) {
        executorDetail = `${exec.server ?? ''}/${exec.tool ?? ''}`
      } else if (execType === 'bcs-route' && exec?.target) {
        executorDetail = String(exec.target)
      } else if (execType === 'cli-script' && exec?.command) {
        executorDetail = String(exec.command)
      }
      const editorNode = node as StoreEditorNode
      // Prefer saved position, fall back to DAG auto-layout
      const savedPos = (editorNode._x != null && editorNode._y != null)
        ? { x: editorNode._x, y: editorNode._y }
        : null
      const autoPos = layoutPositions.get(node.id) ?? { x: 0, y: 0 }

      return {
        id: node.id,
        type: 'editorNode' as const,
        position: savedPos ?? autoPos,
        data: {
          label: node.title || node.id,
          nodeId: node.id,
          executorType: execType,
          executorDetail,
          branchId: editorNode.branchId ?? null,
          hasError: errorNodeIds.has(node.id),
        },
      }
    })
  }, [spec, validationErrors])

  const flowEdges: Edge[] = useMemo(() => {
    if (!spec) return []
    const edges: Edge[] = []
    for (const node of spec.nodes) {
      const branchId = (node as any).branchId as string | undefined
      for (const dep of node.dependsOn ?? []) {
        const hasCycle = validationErrors.some((e) => e.type === 'cycle')
        const isBranchEdge = !!branchId
        edges.push({
          id: `${dep}-${node.id}`,
          source: dep,
          target: node.id,
          animated: isBranchEdge,
          label: isBranchEdge ? branchId : undefined,
          labelStyle: { fontSize: 9, fontWeight: 600, fill: '#6d28d9' },
          labelBgStyle: { fill: '#f5f3ff', fillOpacity: 0.9 },
          labelBgPadding: [4, 2] as [number, number],
          labelBgBorderRadius: 4,
          style: hasCycle
            ? { stroke: '#ef4444', strokeWidth: 2 }
            : isBranchEdge
              ? { stroke: '#8b5cf6', strokeWidth: 1.5, strokeDasharray: '4 2' }
              : { stroke: '#94a3b8' },
          interactionWidth: 20,
        })
      }
    }
    return edges
  }, [spec, validationErrors])

  const onConnect: OnConnect = useCallback(
    (params) => {
      if (params.source && params.target) {
        addEdge(params.source, params.target)
      }
    },
    [addEdge],
  )

  const onNodeClickHandler = useCallback(
    (_event: React.MouseEvent, node: Node) => {
      selectNode(node.id)
      onNodeClick(node.id)
    },
    [selectNode, onNodeClick],
  )

  const onNodesChange: import('@xyflow/react').OnNodesChange = useCallback(
    (changes) => {
      for (const change of changes) {
        if (change.type === 'position' && change.position && change.id) {
          moveNode(change.id, change.position.x, change.position.y)
        }
      }
    },
    [moveNode],
  )

  const onNodesDelete = useCallback(
    (nodes: Node[]) => {
      for (const node of nodes) {
        removeNode(node.id)
      }
    },
    [removeNode],
  )

  const onEdgesDelete = useCallback(
    (edges: Edge[]) => {
      for (const edge of edges) {
        removeEdge(edge.source, edge.target)
      }
    },
    [removeEdge],
  )

  const onEdgeClick = useCallback(
    (_event: React.MouseEvent, edge: Edge) => {
      removeEdge(edge.source, edge.target)
    },
    [removeEdge],
  )

  const onDragOver = useCallback((event: React.DragEvent) => {
    event.preventDefault()
    event.dataTransfer.dropEffect = 'copy'
  }, [])

  const onDrop = useCallback(
    (event: React.DragEvent) => {
      event.preventDefault()
      const executorType = event.dataTransfer.getData('application/clawflow-node-type') as ExecutorType
      if (!executorType || !EXECUTOR_TYPES.includes(executorType)) return

      const position = screenToFlowPosition({
        x: event.clientX,
        y: event.clientY,
      })

      const id = `node-${Date.now()}`
      addNode({
        id,
        _x: position.x,
        _y: position.y,
        title: executorType,
        executor: { type: executorType },
        dependsOn: [],
      })
      selectNode(id)
    },
    [addNode, selectNode, screenToFlowPosition],
  )

  if (!spec) {
    return (
      <div className="flex h-full items-center justify-center text-gray-400 text-sm">
        从侧边栏选择工作流或创建新工作流
      </div>
    )
  }

  return (
    <div className="h-full w-full" ref={wrapperRef} onDragOver={onDragOver} onDrop={onDrop}>
      <ReactFlow
        nodes={flowNodes}
        edges={flowEdges}
        nodeTypes={nodeTypes}
        onConnect={onConnect}
        onNodeClick={onNodeClickHandler}
        onNodesChange={onNodesChange}
        onNodesDelete={onNodesDelete}
        onEdgesDelete={onEdgesDelete}
        onEdgeClick={onEdgeClick}
        deleteKeyCode="Delete"
        fitView
        minZoom={0.3}
        maxZoom={1.5}
        proOptions={{ hideAttribution: true }}
      >
        <Background />
        <Controls />
      </ReactFlow>
    </div>
  )
}

export default function EditorCanvas(props: EditorCanvasProps) {
  return (
    <ReactFlowProvider>
      <EditorCanvasInner {...props} />
    </ReactFlowProvider>
  )
}