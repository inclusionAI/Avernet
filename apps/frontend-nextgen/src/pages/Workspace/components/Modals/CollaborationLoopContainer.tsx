import { Button } from '@/components/ui';
import { Handle, Position, type Node, type NodeProps } from '@xyflow/react';
import { LOOP_RETURN_LANE } from './collaborationLoopLayout';

type ContainerNode = Node<{ title: string; maxIterations: number; loopId: string; collapsed: boolean; onToggle: () => void }, 'loopContainer'>;
export default function CollaborationLoopContainer({ data }: NodeProps<ContainerNode>) {
  return <div className="h-full w-full" style={{ paddingInline: data.collapsed ? 0 : LOOP_RETURN_LANE }}
    data-loop-container={data.loopId} aria-label={`${data.title} Loop，最多 ${data.maxIterations} 次`}>
    <div className="h-full rounded-xl border border-border bg-background/70">
      <div className="flex h-12 items-center justify-between gap-2 px-3 text-sm font-medium">
        <div className="min-w-0"><div className="truncate">{data.title}</div><div className="text-xs font-normal text-muted-foreground">最多 {data.maxIterations} 轮</div></div>
        <Button variant="ghost" size="sm" className="nodrag nopan shrink-0" aria-label={`${data.collapsed ? '展开' : '收起'}循环 ${data.title}`} aria-expanded={!data.collapsed} onClick={data.onToggle}>
          {data.collapsed ? '展开' : '收起'}
        </Button>
      </div>
    </div>
    {data.collapsed && <>
      <Handle id="main" type="target" position={Position.Top} className="pointer-events-none opacity-0" />
      <Handle id="main" type="source" position={Position.Bottom} className="pointer-events-none opacity-0" />
      <Handle id="loop-return" type="source" position={Position.Right} style={{ top: '65%' }} className="pointer-events-none opacity-0" />
      <Handle id="loop-return" type="target" position={Position.Right} style={{ top: '35%' }} className="pointer-events-none opacity-0" />
    </>}
  </div>;
}
