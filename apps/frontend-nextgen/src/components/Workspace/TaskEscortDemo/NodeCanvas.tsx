import { Button, IconButton } from '@/components/ui';
import { cn } from '@/utils/cn';
import { Check, Expand, Flag, GitBranch, Minus, Plus, ScanSearch, UserRoundCheck } from 'lucide-react';
import { useState } from 'react';
import { StatusBadge } from './DemoPrimitives';
import { DemoNode, NodeId, RunStatus } from './model';

export function getNodeIcon(kind: string) {
  switch (kind) {
    case 'trigger':
      return Flag;
    case 'analysis':
      return ScanSearch;
    case 'condition':
      return GitBranch;
    case 'human':
      return UserRoundCheck;
    case 'output':
      return Check;
    default:
      return GitBranch;
  }
}
export function NodeCanvas({
  nodes,
  nodeStatuses,
  selectedId,
  onSelect,
}: {
  nodes: DemoNode[];
  nodeStatuses: Record<NodeId, RunStatus>;
  selectedId: NodeId;
  onSelect: (id: NodeId) => void;
}) {
  const [zoom, setZoom] = useState(100);
  const canvasHeight = Math.max(110, nodes.length * 104 - 10);
  return (
    <section aria-label="工作流节点画布" className="min-w-0 overflow-hidden rounded-xl border border-border bg-card">
      <div className="flex items-center justify-between gap-1 border-b border-border bg-card px-3 py-2">
        <span className="text-[10px] text-muted-foreground">示意编排 · {nodes.length} 个演示节点</span>
        <div className="flex items-center gap-0.5">
          <IconButton
            size="sm"
            label="缩小画布"
            disabled={zoom <= 75}
            onClick={() => setZoom((value) => value - 25)}
            icon={<Minus className="size-3" />}
          />
          <span aria-live="polite" className="w-9 text-center font-mono text-[10px] text-muted-foreground">
            {zoom}%
          </span>
          <IconButton
            size="sm"
            label="放大画布"
            disabled={zoom >= 150}
            onClick={() => setZoom((value) => value + 25)}
            icon={<Plus className="size-3" />}
          />
          <IconButton
            size="sm"
            label="还原画布缩放"
            onClick={() => setZoom(100)}
            icon={<Expand className="size-3" />}
          />
        </div>
      </div>
      <div className="escort-canvas h-[530px] overflow-auto p-4">
        <div className="relative mx-auto" style={{ width: (340 * zoom) / 100, height: (canvasHeight * zoom) / 100 }}>
          <div
            className="absolute left-0 top-0 w-[340px] origin-top-left"
            style={{ height: canvasHeight, transform: `scale(${zoom / 100})` }}
          >
            <svg
              aria-hidden
              viewBox={`0 0 340 ${canvasHeight}`}
              className="pointer-events-none absolute inset-0 size-full"
            >
              {nodes.slice(0, -1).map((node, index) => (
                <g key={node.id}>
                  <path
                    d={`M 170 ${82 + index * 104} L 170 ${110 + index * 104}`}
                    fill="none"
                    className={nodeStatuses[node.id] === 'success' ? 'stroke-primary/50' : 'stroke-border'}
                    strokeWidth="2"
                    strokeDasharray={nodeStatuses[node.id] === 'success' ? undefined : '4 4'}
                  />
                  <path
                    d={`M 166 ${105 + index * 104} L 170 ${110 + index * 104} L 174 ${105 + index * 104}`}
                    fill="none"
                    className="stroke-muted-foreground/50"
                    strokeWidth="1.5"
                  />
                </g>
              ))}
            </svg>
            {nodes.map((node, index) => {
              const Icon = getNodeIcon(node.kind);
              return (
                <Button
                  key={node.id}
                  data-demo-selected-node={selectedId === node.id}
                  variant="outline"
                  aria-label={`选择节点 ${node.name}`}
                  aria-pressed={selectedId === node.id}
                  onClick={() => onSelect(node.id)}
                  className={cn(
                    'escort-node absolute left-[60px] h-[72px] w-[220px] justify-start gap-3 rounded-xl bg-card p-3 text-left shadow-sm hover:bg-card',
                    selectedId === node.id && 'border-primary',
                  )}
                  style={{ top: 10 + index * 104 }}
                >
                  <span className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
                    <Icon className="size-4" aria-hidden />
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-xs font-medium">{node.name || '未命名节点'}</span>
                    <span className="mt-1 block font-mono text-[9px] text-muted-foreground">{node.id}</span>
                  </span>
                  <StatusBadge status={nodeStatuses[node.id]} />
                </Button>
              );
            })}
          </div>
        </div>
      </div>
    </section>
  );
}
