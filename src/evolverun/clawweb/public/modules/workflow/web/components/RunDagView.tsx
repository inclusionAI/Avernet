import { useMemo } from 'react'
import {
  ReactFlow,
  Background,
  Controls,
  type Node,
  type Edge,
  type NodeProps,
  Handle,
  Position,
  type ProOptions,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import type { WorkflowNode, NodeStatus } from '@avernet/clawweb-shared/web/types'

const STATUS_COLORS: Record<string, string> = {
  succeeded: '#22c55e',
  failed: '#ef4444',
  running: '#3b82f6',
  waiting: '#eab308',
  pending: '#d1d5db',
  skipped: '#9ca3af',
  blocked: '#f97316',
  postActionsRunning: '#3b82f6',
}

const STATUS_BG: Record<string, string> = {
  succeeded: '#f0fdf4',
  failed: '#fef2f2',
  running: '#eff6ff',
  waiting: '#fefce8',
  pending: '#f9fafb',
  skipped: '#f9fafb',
  blocked: '#fff7ed',
  postActionsRunning: '#eff6ff',
}

const STATUS_LABELS: Record<string, string> = {
  succeeded: '已成功',
  failed: '已失败',
  running: '运行中',
  waiting: '等待中',
  pending: '待执行',
  skipped: '已跳过',
  blocked: '已阻塞',
  postActionsRunning: '运行中',
}

interface RunDagNodeData {
  label: string
  nodeId: string
  status: string
  executorType: string
  progressMessage?: string | null
  onClick?: (nodeId: string) => void
  [key: string]: unknown
}

type RunDagNode = Node<RunDagNodeData>

function RunDagNodeComponent({ data }: NodeProps<RunDagNode>) {
  const borderColor = STATUS_COLORS[data.status] ?? '#d1d5db'
  const bgColor = STATUS_BG[data.status] ?? '#f9fafb'
  const statusLabel = STATUS_LABELS[data.status] ?? data.status

  return (
    <div
      className="cursor-pointer rounded-lg border-2 px-3 py-2 shadow-sm transition-shadow hover:shadow-md"
      style={{ borderColor, backgroundColor: bgColor }}
      onClick={() => data.onClick?.(data.nodeId)}
    >
      <Handle type="target" position={Position.Top} className="!bg-gray-300" />
      <div className="text-center">
        <div className="font-medium text-gray-800 text-xs">{data.label}</div>
        <div className="mt-0.5 text-[10px] text-gray-400">{data.executorType}</div>
        <div
          className="mt-0.5 inline-block rounded-sm px-1.5 py-px text-[9px] font-medium"
          style={{ color: borderColor, backgroundColor: `${borderColor}15` }}
        >
          {statusLabel}
        </div>
        {data.progressMessage && (
          <div className="mt-0.5 truncate text-[9px] text-gray-400" style={{ maxWidth: 140 }}>
            {data.progressMessage}
          </div>
        )}
      </div>
      <Handle type="source" position={Position.Bottom} className="!bg-gray-300" />
    </div>
  )
}

const nodeTypes = { runDagNode: RunDagNodeComponent }

interface RunDagViewProps {
  specNodes: WorkflowNode[]
  nodeStatusMap: Record<string, { status: string; executorType?: string; progressMessage?: string | null }>
  onNodeClick: (nodeId: string) => void
}

export default function RunDagView({ specNodes, nodeStatusMap, onNodeClick }: RunDagViewProps) {
  const { flowNodes, edges } = useMemo(() => {
    const edges: Edge[] = []

    // Build edges from spec dependsOn
    for (const specNode of specNodes) {
      const deps = specNode.dependsOn ?? []
      for (const dep of deps) {
        const targetStatus = nodeStatusMap[specNode.id]?.status
        edges.push({
          id: `${dep}->${specNode.id}`,
          source: dep,
          target: specNode.id,
          animated: targetStatus === 'running',
          style: {
            stroke: targetStatus === 'succeeded' ? '#86efac' : targetStatus === 'failed' ? '#fca5a5' : '#94a3b8',
            strokeWidth: 2,
          },
        })
      }
    }

    // Layout: arrange nodes by dependency level
    const levels = new Map<string, number>()
    const resolveLevel = (id: string, visited = new Set<string>()): number => {
      if (levels.has(id)) return levels.get(id)!
      if (visited.has(id)) return 0
      visited.add(id)
      const deps = specNodes.find(n => n.id === id)?.dependsOn ?? []
      if (deps.length === 0) {
        levels.set(id, 0)
        return 0
      }
      const maxDepLevel = Math.max(...deps.map((d) => resolveLevel(d, visited)))
      const level = maxDepLevel + 1
      levels.set(id, level)
      return level
    }
    for (const n of specNodes) {
      resolveLevel(n.id)
    }

    // Group nodes by level for positioning
    const levelGroups = new Map<number, string[]>()
    for (const n of specNodes) {
      const level = levels.get(n.id) ?? 0
      const group = levelGroups.get(level) ?? []
      group.push(n.id)
      levelGroups.set(level, group)
    }

    const flowNodes: RunDagNode[] = specNodes.map((specNode) => {
      const level = levels.get(specNode.id) ?? 0
      const siblings = levelGroups.get(level) ?? [specNode.id]
      const indexInLevel = siblings.indexOf(specNode.id)
      const levelCount = siblings.length

      const execInfo = nodeStatusMap[specNode.id]
      const status = execInfo?.status ?? 'pending'
      const executorType = execInfo?.executorType ?? specNode.executor.type ?? 'unknown'

      return {
        id: specNode.id,
        type: 'runDagNode',
        position: {
          x: indexInLevel * 220 + (3 - levelCount) * 110,
          y: level * 140,
        },
        data: {
          label: specNode.title || specNode.id,
          nodeId: specNode.id,
          status,
          executorType,
          progressMessage: execInfo?.progressMessage,
          onClick: onNodeClick,
        },
      }
    })

    return { flowNodes, edges }
  }, [specNodes, nodeStatusMap, onNodeClick])

  if (specNodes.length === 0) {
    return (
      <div className="flex h-64 items-center justify-center text-gray-400 text-sm">
        暂无节点数据可用于DAG可视化
      </div>
    )
  }

  const proOptions: ProOptions = { hideAttribution: true }

  return (
    <div className="h-96 rounded-lg border border-gray-200 bg-white">
      <ReactFlow
        nodes={flowNodes}
        edges={edges}
        nodeTypes={nodeTypes}
        fitView
        proOptions={proOptions}
        minZoom={0.3}
        maxZoom={1.5}
      >
        <Background />
        <Controls />
      </ReactFlow>
    </div>
  )
}
