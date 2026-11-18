import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from '@/components/ui';
import { GitBranch } from 'lucide-react';
import { useId } from 'react';
import { DemoWorkflow } from './model';

export function WorkflowSelector({
  workflow,
  workflows,
  onSelect,
}: {
  workflow: DemoWorkflow | null;
  workflows: readonly DemoWorkflow[];
  onSelect: (id: string) => void;
}) {
  const descriptionId = useId();
  return (
    <Select value={workflow?.id ?? ''} onValueChange={onSelect} disabled={!workflow || workflows.length === 0}>
      <SelectTrigger
        aria-label="切换工作流"
        className="h-auto min-h-11 w-full min-w-0 max-w-sm gap-3 py-2 text-left hover:border-primary/40"
      >
        <GitBranch className="size-4 shrink-0 text-primary" aria-hidden />
        <span className="min-w-0 flex-1">
          <SelectValue placeholder="选择工作流">
            {workflow && (
              <span className="block min-w-0">
                <span className="block truncate font-medium">{workflow.name}</span>
                <span className="mt-0.5 block truncate font-mono text-[10px] text-muted-foreground">{workflow.id}</span>
              </span>
            )}
          </SelectValue>
        </span>
      </SelectTrigger>
      <SelectContent aria-label="有权限的工作流" aria-describedby={descriptionId} className="max-w-[calc(100vw-24px)]">
        <div className="border-b border-border px-3 py-2.5">
          <p className="text-xs font-medium">有权限的工作流</p>
          <p id={descriptionId} className="mt-1 text-[10px] text-muted-foreground">
            模拟权限 · 仅展示可查看项
          </p>
        </div>
        <SelectGroup className="py-1">
          {workflows.map((item) => (
            <SelectItem key={item.id} value={item.id} textValue={item.name} className="py-2.5">
              <span className="block min-w-0">
                <span className="block truncate font-medium">{item.name}</span>
                <span className="mt-0.5 block truncate font-mono text-[10px] text-muted-foreground">{item.id}</span>
              </span>
            </SelectItem>
          ))}
        </SelectGroup>
      </SelectContent>
    </Select>
  );
}
