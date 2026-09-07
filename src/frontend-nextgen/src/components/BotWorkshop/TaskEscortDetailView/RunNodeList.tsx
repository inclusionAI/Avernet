import type { NodeExecution } from '@/components/BotWorkshop/TaskEscort/types';
import { STATUS_LABEL, STATUS_TONE, formatDuration } from '@/components/BotWorkshop/TaskEscort/utils';
import { Badge } from '@/components/ui/Badge';
import { Card } from '@/components/ui/Card';
import { cn } from '@/utils/cn';
import React from 'react';
import { NodeDetailPanel } from './NodeDetailPanel';

interface RunNodeListProps {
  nodes: NodeExecution[];
  selectedNodeId: string | null;
  onSelectNode: (nodeId: string) => void;
}

export const RunNodeList: React.FC<RunNodeListProps> = ({ nodes, selectedNodeId, onSelectNode }) => {
  const selectedNode = selectedNodeId ? nodes.find((n) => n.node_id === selectedNodeId) : undefined;

  return (
    <div className="space-y-2">
      <div className="space-y-1">
        {nodes.map((node) => {
          const isSelected = selectedNodeId === node.node_id;
          return (
            <Card
              key={node.node_id}
              onClick={() => onSelectNode(node.node_id)}
              className={cn(
                'flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 shadow-none transition-colors',
                isSelected ? 'bg-primary/10 ring-1 ring-primary' : 'bg-[var(--color-panel)] hover:bg-accent',
              )}
            >
              <div className="min-w-0 flex-1">
                <div className="truncate text-xs font-medium">{node.node_title || node.node_id}</div>
                <div className="truncate text-[10px] text-[var(--color-muted)]">
                  {node.node_id} · {node.executor_type}
                  {node.phase && ` · ${node.phase}`}
                  {node.duration_ms !== null && ` · ${formatDuration(node.duration_ms)}`}
                </div>
              </div>
              <Badge tone={STATUS_TONE[node.status] || 'neutral'}>{STATUS_LABEL[node.status] || node.status}</Badge>
            </Card>
          );
        })}
      </div>

      {selectedNode && <NodeDetailPanel node={selectedNode} onClose={() => onSelectNode('')} />}
    </div>
  );
};
