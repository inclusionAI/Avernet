import type { CollaborationDefinitionGraphPreview } from '@/services/backend-api/BcnController';
import React from 'react';

export default function CollaborationPreviewMetadata({ graph }: { graph: CollaborationDefinitionGraphPreview }) {
  if (!graph.execution_graph_mode) return null;
  const loops = new Map(graph.nodes.flatMap((node) => node.execution
    ? [[node.execution.loop_id, node.execution.max_iterations] as const] : []));
  return <div className="space-y-1 border-b border-slate-200/60 px-3 py-2 text-xs text-slate-600">
    <details className="text-slate-500">
      <summary className="cursor-pointer">循环限制</summary>
      <p>{Array.from(loops, ([id, max]) => `${graph.loops?.[id]?.display_name || id}：max_iterations = ${max}`).join('；')}</p>
    </details>
    <p className="text-slate-500">节点 ID 仅用于当前预览；运行后的节点请从运行详情查询。</p>
  </div>;
}
