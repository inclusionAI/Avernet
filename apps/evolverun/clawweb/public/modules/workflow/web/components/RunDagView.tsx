import { useMemo, type CSSProperties } from 'react'
import {
  ReactFlow,
  Background,
  Controls,
  Handle,
  MarkerType,
  Panel,
  Position,
  useReactFlow,
  type BuiltInEdge,
  type Node,
  type NodeProps,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import type { WorkflowNode, NodeStatus } from '@avernet/clawweb-shared/web/types'

// Match the shared semantic tokens, including their public-host defaults.
const COLORS = {
  primary: 'hsl(var(--primary, var(--brand, 221.2 83.2% 53.3%)))',
  success: 'hsl(var(--success, 142.1 76.2% 36.3%))',
  destructive: 'hsl(var(--destructive, 0 72.2% 50.6%))',
  warning: 'hsl(var(--warning-foreground, 26 90% 30%))',
  muted: 'hsl(var(--muted-foreground, 215.4 16.3% 46.9%))',
  border: 'hsl(var(--border, 214.3 31.8% 91.4%))',
  card: 'hsl(var(--card, 0 0% 100%))',
  foreground: 'hsl(var(--foreground, 222.2 47.4% 11.2%))',
  surface: 'hsl(var(--muted, 210 40% 96.1%))',
}

const STATUS_LABELS: Record<NodeStatus, string> = {
  succeeded: '已成功',
  failed: '已失败',
  running: '运行中',
  waiting: '等待中',
  pending: '待执行',
  skipped: '已跳过',
  blocked: '已阻塞',
  postActionsRunning: '运行中',
  cancelled: '已中止',
}

function statusColor(status?: string) {
  if (status === 'succeeded') return COLORS.success
  if (status === 'failed' || status === 'cancelled' || status === 'timeout') return COLORS.destructive
  if (status === 'running' || status === 'postActionsRunning') return COLORS.primary
  if (status === 'waiting' || status === 'blocked') return COLORS.warning
  return COLORS.muted
}

interface RunDagNodeData extends Record<string, unknown> {
  label: string
  nodeId: string
  status?: string
  executorType: string
  progressMessage?: string | null
  onClick: (nodeId: string) => void
}

type RunDagNode = Node<RunDagNodeData>

function RunDagNodeComponent({ data, selected, positionAbsoluteX, positionAbsoluteY, width = 232, height = 136 }: NodeProps<RunDagNode>) {
  const { getViewport, setCenter } = useReactFlow()
  const color = statusColor(data.status)
  const borderColor = selected ? COLORS.primary : `color-mix(in srgb, ${color} 35%, ${COLORS.border})`
  const statusLabel = data.status ? STATUS_LABELS[data.status] ?? data.status : '无执行记录'

  return (
    <div className="relative">
      <Handle type="target" position={Position.Left} isConnectable={false} />
      <button
        type="button"
        aria-label={`定位节点：${data.label}（${data.nodeId}）`}
        aria-pressed={selected}
        onClick={() => data.onClick(data.nodeId)}
        onFocus={(event) => {
          const button = event.currentTarget
          if (!button.matches(':focus-visible')) return
          const canvas = button.closest('.react-flow')?.getBoundingClientRect()
          if (!canvas?.width || !canvas.height) return
          // Native focus can temporarily scroll the canvas before ReactFlow resets
          // it. Project flow coordinates instead of trusting those transient DOM bounds.
          const viewport = getViewport()
          const left = positionAbsoluteX * viewport.zoom + viewport.x
          const top = positionAbsoluteY * viewport.zoom + viewport.y
          if (left < 0 || top < 0 || left + width * viewport.zoom > canvas.width || top + height * viewport.zoom > canvas.height) {
            void setCenter(positionAbsoluteX + width / 2, positionAbsoluteY + height / 2, { zoom: viewport.zoom, duration: 0 })
          }
        }}
        onKeyDown={(event) => {
          // The native button owns Enter/Space activation. Do not also invoke the
          // callback here or let ReactFlow interpret these keys as selection/pan.
          if (event.key === 'Enter' || event.key === ' ') event.stopPropagation()
        }}
        className={`nodrag nopan flex h-[136px] w-[232px] cursor-pointer flex-col gap-2 rounded-xl border border-l-[3px] bg-card p-3.5 text-left text-card-foreground shadow-sm transition-shadow hover:shadow-md focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-ring ${selected ? 'ring-2 ring-primary ring-offset-2 ring-offset-background' : ''}`}
        style={{
          borderTopColor: borderColor,
          borderRightColor: borderColor,
          borderBottomColor: borderColor,
          borderLeftColor: color,
        }}
      >
        <span className="flex min-w-0 items-center gap-2">
          <span className="h-1.5 w-1.5 shrink-0 rounded-full" style={{ backgroundColor: color }} aria-hidden="true" />
          <span className="truncate text-xs font-semibold" title={data.label}>{data.label}</span>
        </span>
        <span className="block w-full truncate font-mono text-[10px] text-muted-foreground" title={data.nodeId}>
          {data.nodeId}
        </span>
        <span className="mt-auto flex min-w-0 items-center justify-between gap-2">
          <span
            className="shrink-0 rounded-md px-1.5 py-0.5 text-[10px] font-medium"
            style={{ color, backgroundColor: `color-mix(in srgb, ${color} 10%, transparent)` }}
          >
            {statusLabel}
          </span>
          <span className="truncate font-mono text-[10px] text-muted-foreground" title={`执行器：${data.executorType}`}>
            {data.executorType}
          </span>
        </span>
        {data.progressMessage && (
          <span className="block w-full truncate text-[10px] text-muted-foreground" title={data.progressMessage}>
            {data.progressMessage}
          </span>
        )}
      </button>
      <Handle type="source" position={Position.Right} isConnectable={false} />
    </div>
  )
}

const nodeTypes = { runDagNode: RunDagNodeComponent }
const NODE_WIDTH = 232
const COLUMN_GAP = 96
const ROW_PITCH = 176
const FIT_OPTIONS = { padding: 0.2, minZoom: 0.1, maxZoom: 1 }
const PRO_OPTIONS = { hideAttribution: true }
const ARIA_LABELS = {
  'controls.ariaLabel': 'DAG 画布控制',
  'controls.zoomIn.ariaLabel': '放大 DAG',
  'controls.zoomOut.ariaLabel': '缩小 DAG',
  'controls.fitView.ariaLabel': '适应 DAG 画布',
  'handle.ariaLabel': '节点依赖连接点',
}
const FLOW_STYLE = {
  '--xy-background-color': 'transparent',
  '--xy-controls-button-background-color': COLORS.card,
  '--xy-controls-button-background-color-hover': COLORS.surface,
  '--xy-controls-button-color': COLORS.foreground,
  '--xy-controls-button-color-hover': COLORS.foreground,
  '--xy-controls-button-border-color': COLORS.border,
  '--xy-controls-box-shadow': `0 2px 8px color-mix(in srgb, ${COLORS.foreground} 8%, transparent)`,
  '--xy-handle-background-color': COLORS.card,
  '--xy-handle-border-color': COLORS.muted,
} as CSSProperties

export interface RunDagViewProps {
  specNodes: WorkflowNode[]
  nodeStatusMap: Record<string, { status: string; executorType?: string; progressMessage?: string | null }>
  onNodeClick: (nodeId: string) => void
  selectedNodeId?: string
  expanded?: boolean
}

export default function RunDagView({ specNodes, nodeStatusMap, onNodeClick, selectedNodeId, expanded = false }: RunDagViewProps) {
  const { flowNodes, edges } = useMemo(() => {
    const nodeIds = new Set(specNodes.map(node => node.id))
    const dependents = new Map<string, string[]>()
    const remaining = new Map<string, number>()
    const levels = new Map<string, number>()
    const edges: BuiltInEdge[] = []

    for (const specNode of specNodes) {
      // A dependency not present in this spec must not create a phantom node.
      const dependencies = [...new Set(specNode.dependsOn ?? [])].filter(id => nodeIds.has(id))
      remaining.set(specNode.id, dependencies.length)
      levels.set(specNode.id, 0)
      for (const dependency of dependencies) {
        const children = dependents.get(dependency) ?? []
        children.push(specNode.id)
        dependents.set(dependency, children)
        const status = nodeStatusMap[specNode.id]?.status
        const color = statusColor(status)
        edges.push({
          id: JSON.stringify([dependency, specNode.id]),
          source: dependency,
          target: specNode.id,
          type: 'smoothstep',
          animated: status === 'running' || status === 'postActionsRunning',
          ariaLabel: `依赖：${dependency} → ${specNode.id}`,
          markerEnd: { type: MarkerType.ArrowClosed, color, width: 16, height: 16 },
          style: { stroke: color, strokeWidth: 1.5, opacity: 0.65 },
          pathOptions: { borderRadius: 18 },
        })
      }
    }

    // Stable topological layering, left to right. Iteration also avoids recursion
    // overflow for long workflows; malformed cycles retain finite positions.
    const queue = specNodes.filter(node => remaining.get(node.id) === 0).map(node => node.id)
    for (let cursor = 0; cursor < queue.length; cursor += 1) {
      const id = queue[cursor]
      for (const child of dependents.get(id) ?? []) {
        levels.set(child, Math.max(levels.get(child) ?? 0, (levels.get(id) ?? 0) + 1))
        const count = (remaining.get(child) ?? 1) - 1
        remaining.set(child, count)
        if (count === 0) queue.push(child)
      }
    }

    const groups = new Map<number, string[]>()
    for (const node of specNodes) {
      const level = levels.get(node.id) ?? 0
      const siblings = groups.get(level) ?? []
      siblings.push(node.id)
      groups.set(level, siblings)
    }

    const flowNodes: RunDagNode[] = specNodes.map((specNode) => {
      const level = levels.get(specNode.id) ?? 0
      const siblings = groups.get(level)!
      const execution = nodeStatusMap[specNode.id]
      return {
        id: specNode.id,
        type: 'runDagNode',
        selected: specNode.id === selectedNodeId,
        sourcePosition: Position.Right,
        targetPosition: Position.Left,
        // ReactFlow otherwise disables pointer events when nodes cannot be
        // selected/dragged. Only the inner native button is interactive here.
        style: { pointerEvents: 'all' },
        position: {
          x: level * (NODE_WIDTH + COLUMN_GAP),
          y: (siblings.indexOf(specNode.id) - (siblings.length - 1) / 2) * ROW_PITCH,
        },
        data: {
          label: specNode.title || specNode.id,
          nodeId: specNode.id,
          status: execution?.status,
          executorType: execution?.executorType ?? specNode.executor.type,
          progressMessage: execution?.progressMessage,
          onClick: onNodeClick,
        },
      }
    })
    return { flowNodes, edges }
  }, [specNodes, nodeStatusMap, onNodeClick, selectedNodeId])

  if (specNodes.length === 0) {
    return (
      <div className="flex min-h-48 items-center justify-center bg-muted/20 px-6 text-center text-sm text-muted-foreground" role="status">
        当前定义中暂无节点，暂无 DAG 可展示。
      </div>
    )
  }

  return (
    <div className={`relative min-w-0 bg-muted/20 ${expanded ? 'h-[min(70vh,680px)] min-h-[380px]' : 'h-[340px] sm:h-[380px]'}`}>
      <ReactFlow
        aria-label="运行节点执行依赖图"
        nodes={flowNodes}
        edges={edges}
        nodeTypes={nodeTypes}
        fitView
        fitViewOptions={FIT_OPTIONS}
        proOptions={PRO_OPTIONS}
        minZoom={0.1}
        maxZoom={1.5}
        nodesDraggable={false}
        nodesConnectable={false}
        nodesFocusable={false}
        edgesFocusable={false}
        elementsSelectable={false}
        deleteKeyCode={null}
        selectionKeyCode={null}
        panActivationKeyCode={null}
        panOnDrag
        zoomOnPinch
        ariaLabelConfig={ARIA_LABELS}
        style={FLOW_STYLE}
      >
        <Background color={COLORS.border} gap={20} size={1} />
        <Panel position="top-left" className="pointer-events-none text-[10px] font-medium tracking-wider text-muted-foreground">
          执行依赖 · 从左到右
        </Panel>
        <Controls showInteractive={false} fitViewOptions={FIT_OPTIONS} />
        <Panel position="bottom-right" className="pointer-events-none hidden text-[10px] text-muted-foreground sm:block">
          拖动画布平移 · 点击节点查看详情
        </Panel>
      </ReactFlow>
    </div>
  )
}
