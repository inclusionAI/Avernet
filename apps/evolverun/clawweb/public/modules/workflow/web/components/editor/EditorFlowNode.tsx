import { useEffect } from 'react'
import { Handle, Position, useUpdateNodeInternals, type Node, type NodeProps } from '@xyflow/react'
import type { WorkflowNode } from '@avernet/clawweb-shared/web/types'
import { getEffectiveTriggerRule, getResultBranches } from '../../editor/contracts'
import { getNodeMeta, NODE_CATALOG } from '../../editor/nodeCatalog'
import { branchHandleId, GENERAL_SOURCE_HANDLE, nodeSummary } from './CanvasPresentation'
import FlowNodeIcon from './FlowNodeIcon'

export type EditorCanvasNode = Node<{
  node: WorkflowNode
  errors: string[]
}, 'editorNode'>

const TRIGGER_LABELS: Record<string, string> = {
  all_success: '全部成功后继续',
  one_success: '任一成功后继续',
  all_done: '全部结束后继续',
}
const portClass = '!h-3 !w-3 !border-2 !border-primary !bg-card hover:!bg-primary'

export default function EditorFlowNode({ id, data, selected, isConnectable }: NodeProps<EditorCanvasNode>) {
  const { node, errors } = data
  const type = node.executor?.type ?? '未指定类型'
  const meta = getNodeMeta(type)
  const branches = getResultBranches(node)
  const branchSignature = JSON.stringify(branches?.map((branch) => branch.branchId))
  const updateNodeInternals = useUpdateNodeInternals()
  useEffect(() => { updateNodeInternals(id) }, [id, branchSignature, updateNodeInternals])
  const summary = nodeSummary(node)
  const trigger = getEffectiveTriggerRule(node)
  const showTrigger = (node.dependsOn?.length ?? 0) > 1 || trigger !== 'all_success'
  const hasContent = Object.entries(node.executor ?? {}).some(([key, value]) => key !== 'type' && value != null && value !== '')
  const compatible = !NODE_CATALOG.some((entry) => entry.type === type) || branches === null
  const status = errors.length ? '需要检查' : compatible ? '兼容模式' : hasContent || type === 'done' ? '已填写' : '待配置'

  return (
    <article aria-label={`${node.title || node.id} · ${type}`} className={`relative w-60 rounded-xl border bg-card text-card-foreground shadow-sm transition-shadow ${selected ? 'border-primary ring-2 ring-primary/20' : 'border-input hover:border-primary/50'}`}>
      <Handle type="target" position={Position.Top} isConnectable={isConnectable} className={portClass} aria-label={`${node.title || node.id} 输入`} />
      {showTrigger && <div className="absolute -top-6 left-2 max-w-56 truncate rounded bg-muted px-2 py-0.5 text-[10px] text-muted-foreground" title={String(trigger)}>{TRIGGER_LABELS[trigger] ?? trigger}</div>}
      <div className="flex items-center gap-2.5 px-3.5 pb-2 pt-3.5">
        <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary"><FlowNodeIcon type={type} /></span>
        <div className="min-w-0 flex-1">
          <h3 className="truncate text-sm font-semibold" title={node.title || node.id}>{node.title || node.id}</h3>
          <p className="mt-0.5 truncate text-[10px] text-muted-foreground" title={type}>{meta?.label ?? '扩展节点'} · {type}</p>
        </div>
      </div>
      <p className="line-clamp-2 min-h-9 break-words px-3.5 text-xs leading-[18px] text-muted-foreground" title={summary || undefined}>
        {summary || (type === 'done' ? '标记本节点完成，不取消其他分支' : compatible ? '保留完整原始配置，点击查看' : '点击节点填写配置')}
      </p>
      <div className="mt-2 flex items-center justify-between gap-2 border-t border-input bg-muted/30 px-3.5 py-2 text-[10px]">
        <span className={errors.length ? 'text-destructive' : status === '已填写' ? 'text-success' : 'text-muted-foreground'} title={errors.join('\n') || '配置摘要不代替运行校验'}>{status}</span>
        <span className="max-w-32 truncate font-mono text-muted-foreground" title={node.id}>{node.id}</span>
      </div>
      {branches && branches.length > 0 && <div className="flex border-t border-input px-2 py-2">
        {branches.map((branch) => (
          <div key={branch.branchId} className="relative min-w-0 flex-1 px-1 text-center text-[10px]">
            <span className="block truncate font-mono text-primary" title={branch.branchId}>{branch.branchId}</span>
            <Handle id={branchHandleId(branch.branchId)} type="source" position={Position.Bottom} isConnectable={isConnectable} className={`${portClass} !-bottom-3.5`} aria-label={`分支 ${branch.branchId} 输出`} />
          </div>
        ))}
      </div>}
      <Handle id={GENERAL_SOURCE_HANDLE} type="source" position={branches?.length ? Position.Right : Position.Bottom} isConnectable={isConnectable && !branches?.length} className={portClass} aria-label={`${node.title || node.id} 普通输出`} />
    </article>
  )
}
