import type { CollaborationDefinitionGraphPreview } from '@/domain/collaboration/graphTypes';

export default function CollaborationPreviewMetadata({ graph }: { graph: CollaborationDefinitionGraphPreview }) {
  if (!graph.execution_graph_mode) return null;
  const loops = Object.entries(graph.loops || {});
  return <div className="space-y-1 border-b border-border px-3 py-2 text-xs text-muted-foreground">
    <details className="text-muted-foreground">
      <summary className="cursor-pointer">循环限制</summary>
      <p>{loops.map(([path, loop]) => {
        const label = loop.display_name && path.includes('/')
          ? `${loop.display_name}（${path}）`
          : loop.display_name || path;
        return `${label}：max_iterations = ${loop.max_iterations}`;
      }).join('；')}</p>
    </details>
    <p className="text-muted-foreground">节点 ID 仅用于当前预览；运行后的节点请从运行详情查询。</p>
  </div>;
}
