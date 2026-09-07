import type { NodeExecution } from '@avernet/clawweb-shared/web/types'
import NodeOutputViewer from './NodeOutputViewer'

interface NodeInputPanelProps {
  nodeId: string
  inputJson: string | null
  nodes: NodeExecution[]
}

/**
 * A folded "Input" viewer (NodeOutputViewer, collapsed by default — same as
 * Output). Shows the node's input with upstream `nodeOutputKeys` resolved to
 * their actual sibling output values (取值后), using only REAL field names
 * (`params`, `nodeOutputKeys`); no fabricated keys, no separate raw viewer.
 *
 * ClawMind persists `node_executions.input_json` as
 *   `{ params, nodeOutputKeys: ["nodeA", "nodeB"] }`
 * (see ClawMind/src/controller.ts `nodeInputSummary`). The keys alone are not
 * useful for debugging — this panel cross-references each key against the same
 * run's sibling node `output_json` and shows the resolved values. The label
 * carries the resolve ratio (resolved/total) so partial resolution is visible.
 *
 * Non-standard input shapes fall back to showing the raw content as `Input`.
 */
export default function NodeInputPanel({ nodeId, inputJson, nodes }: NodeInputPanelProps) {
  if (!inputJson) {
    return (
      <div className="rounded-md bg-gray-50 px-3 py-2 text-gray-400 text-xs italic">
        暂无输入数据
      </div>
    )
  }

  let parsed: unknown
  let standard: boolean
  try {
    parsed = JSON.parse(inputJson)
    standard =
      parsed != null &&
      typeof parsed === 'object' &&
      !Array.isArray(parsed) &&
      'nodeOutputKeys' in (parsed as Record<string, unknown>)
  } catch {
    return <NodeOutputViewer nodeId={nodeId} label="输入" data={inputJson} />
  }

  // Non-standard shape (or not JSON): show the raw content as Input.
  if (!standard) {
    return <NodeOutputViewer nodeId={nodeId} label="输入" data={inputJson} />
  }

  const obj = parsed as { params?: unknown; nodeOutputKeys?: unknown }
  const keys = Array.isArray(obj.nodeOutputKeys)
    ? (obj.nodeOutputKeys as unknown[]).map(String)
    : []

  // Build nodeId → parsed output map from sibling executions (prefer succeeded/latest).
  const outputByNode = new Map<string, string>()
  for (const n of nodes) {
    if (!n.output_json) continue
    const existing = outputByNode.get(n.node_id)
    if (existing == null || n.status === 'succeeded') {
      outputByNode.set(n.node_id, n.output_json)
    }
  }

  const upstream: Record<string, unknown> = {}
  let resolvedCount = 0
  for (const key of keys) {
    const raw = outputByNode.get(key)
    if (raw == null) {
      upstream[key] = '(未找到该节点输出)'
    } else {
      try {
        upstream[key] = JSON.parse(raw)
      } catch {
        upstream[key] = raw
      }
      resolvedCount++
    }
  }

  // Resolved input — real field names only: params + nodeOutputKeys (取值后).
  const resolvedView: Record<string, unknown> = {}
  if (obj.params != null) resolvedView['params'] = obj.params
  if (Object.keys(upstream).length > 0) resolvedView['nodeOutputKeys'] = upstream
  const resolvedJson = JSON.stringify(resolvedView, null, 2)

  const label = keys.length > 0 ? `输入（上游取值后 · ${resolvedCount}/${keys.length}）` : '输入'
  return <NodeOutputViewer nodeId={nodeId} label={label} data={resolvedJson} />
}
