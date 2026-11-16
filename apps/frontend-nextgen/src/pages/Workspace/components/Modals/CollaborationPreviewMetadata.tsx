import type { CollaborationDefinitionGraphPreview } from '@/domain/collaboration/graphTypes';

export default function CollaborationPreviewMetadata({ graph }: { graph: CollaborationDefinitionGraphPreview }) {
  const logicalNodes = new Set(graph.nodes.map((node) => node.execution
    ? JSON.stringify([node.execution.loop_path?.map((frame) => frame.loop_id) ?? [node.execution.loop_id], node.execution.definition_node_id])
    : node.node_id));
  const loopCount = Object.keys(graph.loops ?? {}).length;
  return <div className="flex shrink-0 flex-wrap gap-x-3 gap-y-1 border-b border-border px-4 py-2 text-xs text-muted-foreground">
    <span>{logicalNodes.size} 个节点{loopCount > 0 ? ` · ${loopCount} 个循环` : ''}</span>
    <span>点击节点查看详情</span>
  </div>;
}
