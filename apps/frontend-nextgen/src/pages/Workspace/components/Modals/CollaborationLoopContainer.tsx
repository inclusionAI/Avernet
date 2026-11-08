import type { Node, NodeProps } from '@xyflow/react';
import { LOOP_RETURN_LANE } from './collaborationLoopLayout';

type ContainerNode = Node<{ title: string; maxIterations: number; loopId: string }, 'loopContainer'>;
export default function CollaborationLoopContainer({ data }: NodeProps<ContainerNode>) {
  return <div className="h-full w-full" style={{ paddingInline: LOOP_RETURN_LANE }}
    data-loop-container={data.loopId} aria-label={`${data.title} Loop，最多 ${data.maxIterations} 次`}>
    <div className="h-full rounded-xl border border-border bg-background/40">
      <div className="flex items-center justify-between gap-2 px-3 py-3 text-[11px] font-medium text-muted-foreground">
        <span className="min-w-0 truncate">{data.title}</span>
        <span>Loop</span>
      </div>
    </div>
  </div>;
}
