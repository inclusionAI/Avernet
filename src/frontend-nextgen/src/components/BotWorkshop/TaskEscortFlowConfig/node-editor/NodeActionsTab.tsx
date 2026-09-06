import type { TaskEscortWorkflowNode, TaskEscortWorkflowSpec } from '@/components/BotWorkshop/TaskEscort/types';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/Select';
import { X } from 'lucide-react';

interface NodeActionsTabProps {
  node: TaskEscortWorkflowNode;
  spec: TaskEscortWorkflowSpec;
  onChange: (updates: Partial<TaskEscortWorkflowNode>) => void;
}

export function NodeActionsTab({ node, spec, onChange }: NodeActionsTabProps) {
  const routes = node.onResult ?? [];

  const updateRoute = (index: number, updates: Partial<{ value: string; target: string }>) => {
    const next = routes.map((r, i) => (i === index ? { ...r, ...updates } : r));
    onChange({ onResult: next });
  };

  const removeRoute = (index: number) => {
    const next = routes.filter((_, i) => i !== index);
    onChange({ onResult: next.length > 0 ? next : undefined });
  };

  return (
    <div className="space-y-3">
      {routes.length === 0 && (
        <div className="rounded-md border border-dashed border-border bg-muted/30 p-3 text-center text-xs text-muted-foreground">
          暂无 onResult 路由
        </div>
      )}

      {routes.map((route, index) => (
        <div key={index} className="space-y-2 rounded-md border border-border bg-card p-2">
          <div className="flex items-center justify-between">
            <span className="text-[10px] font-medium text-muted-foreground">路由 {index + 1}</span>
            <Button variant="ghost" size="icon" className="h-5 w-5" onClick={() => removeRoute(index)}>
              <X className="h-3 w-3" />
            </Button>
          </div>
          <div className="grid grid-cols-2 gap-2">
            <Input
              value={route.value}
              onChange={(e) => updateRoute(index, { value: e.target.value })}
              placeholder="分支值"
              className="h-7 text-xs"
            />
            <Select value={route.target} onValueChange={(v) => updateRoute(index, { target: v })}>
              <SelectTrigger className="h-7 text-xs">
                <SelectValue placeholder="目标节点" />
              </SelectTrigger>
              <SelectContent>
                {spec.nodes
                  .filter((n) => n.id !== node.id)
                  .map((n) => (
                    <SelectItem key={n.id} value={n.id} className="text-xs">
                      {n.title || n.id}
                    </SelectItem>
                  ))}
              </SelectContent>
            </Select>
          </div>
        </div>
      ))}

      <Button
        variant="secondary"
        size="sm"
        className="w-full"
        onClick={() => onChange({ onResult: [...routes, { value: '', target: '' }] })}
      >
        + 添加路由
      </Button>
    </div>
  );
}
