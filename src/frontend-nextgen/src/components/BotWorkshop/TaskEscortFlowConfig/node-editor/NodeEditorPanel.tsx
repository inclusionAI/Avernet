import type { TaskEscortWorkflowNode, TaskEscortWorkflowSpec } from '@/components/BotWorkshop/TaskEscort/types';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { cn } from '@/utils/cn';
import { Trash2, X } from 'lucide-react';
import { useState } from 'react';
import { NodeActionsTab } from './NodeActionsTab';
import { NodeAdvancedTab } from './NodeAdvancedTab';
import { NodeAlertsTab } from './NodeAlertsTab';
import { NodeBasicTab } from './NodeBasicTab';
import type { NodeDetailTabId } from './types';
import { hasAdvancedConfig } from './utils';

function getExecutorType(node: TaskEscortWorkflowNode): string {
  const executor = node.executor;
  if (typeof executor === 'string') return executor || (node.type ?? 'node');
  if (executor && typeof executor === 'object') return (executor.type as string) || (node.type ?? 'node');
  return node.type ?? 'node';
}

interface NodeEditorPanelProps {
  spec: TaskEscortWorkflowSpec;
  selectedNodeId: string;
  onChange: (nextSpec: TaskEscortWorkflowSpec) => void;
  onClose: () => void;
}

export function NodeEditorPanel({ spec, selectedNodeId, onChange, onClose }: NodeEditorPanelProps) {
  const [activeTab, setActiveTab] = useState<NodeDetailTabId>('basic');

  const node = spec.nodes.find((n) => n.id === selectedNodeId);
  if (!node) return null;

  const updateNode = (updates: Partial<TaskEscortWorkflowNode>) => {
    const nextNodes = spec.nodes.map((n) => (n.id === selectedNodeId ? { ...n, ...updates } : n));
    onChange({ ...spec, nodes: nextNodes });
  };

  const removeNode = () => {
    const nextNodes = spec.nodes
      .filter((n) => n.id !== selectedNodeId)
      .map((n) => ({
        ...n,
        dependsOn: (n.dependsOn ?? []).filter((d) => d !== selectedNodeId),
      }));
    onChange({ ...spec, nodes: nextNodes });
    onClose();
  };

  const tabs: { id: NodeDetailTabId; label: string; badge?: string }[] = [
    { id: 'basic', label: 'Basic' },
    {
      id: 'advanced',
      label: 'Advanced',
      badge: hasAdvancedConfig(node) ? 'configured' : undefined,
    },
    {
      id: 'actions',
      label: 'Actions',
      badge: node.onResult?.length ? `${node.onResult.length}` : undefined,
    },
    {
      id: 'alerts',
      label: 'Alerts',
      badge: node.alerting ? 'on' : undefined,
    },
  ];

  return (
    <Card className="flex w-[320px] shrink-0 flex-col overflow-hidden border-l shadow-sm">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-border px-3 py-2">
        <div className="flex min-w-0 items-center gap-2">
          <Badge tone="primary" className="shrink-0">
            {getExecutorType(node)}
          </Badge>
          <span className="truncate text-xs font-medium">{node.title || node.id}</span>
        </div>
        <Button variant="ghost" size="icon" className="h-6 w-6 shrink-0" onClick={onClose}>
          <X className="h-3.5 w-3.5" />
        </Button>
      </div>

      {/* Tabs */}
      <div className="flex border-b border-border bg-muted/30">
        {tabs.map((tab) => (
          <Button
            key={tab.id}
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => setActiveTab(tab.id)}
            className={cn(
              'flex h-auto flex-1 items-center justify-center gap-1 rounded-none px-1 py-1.5 text-[11px] font-medium transition-colors',
              activeTab === tab.id
                ? 'border-b-2 border-primary text-primary hover:text-primary'
                : 'text-muted-foreground hover:text-foreground',
            )}
          >
            <span>{tab.label}</span>
            {tab.badge && (
              <span className="inline-flex h-4 min-w-4 items-center justify-center rounded-full bg-primary/10 px-1 text-[9px] text-primary">
                {tab.badge}
              </span>
            )}
          </Button>
        ))}
      </div>

      {/* Content */}
      <div className="min-h-0 flex-1 overflow-y-auto p-3">
        {activeTab === 'basic' && <NodeBasicTab node={node} spec={spec} onChange={updateNode} />}
        {activeTab === 'advanced' && <NodeAdvancedTab node={node} onChange={updateNode} />}
        {activeTab === 'actions' && <NodeActionsTab node={node} spec={spec} onChange={updateNode} />}
        {activeTab === 'alerts' && <NodeAlertsTab />}
      </div>

      {/* Footer */}
      <div className="border-t border-border px-3 py-2">
        <Button
          variant="outline"
          size="sm"
          className="w-full text-destructive hover:bg-destructive/10"
          onClick={removeNode}
        >
          <Trash2 className="mr-1.5 h-3 w-3" />
          删除节点
        </Button>
      </div>
    </Card>
  );
}
