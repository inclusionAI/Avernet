import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from '@/components/ui';
import type { WorkflowListItem } from '@/services/tasks/taskService';
import { GitBranch } from 'lucide-react';
import { useId } from 'react';

export function WorkflowSelector({
  workflow,
  workflows,
  onSelect,
}: {
  workflow: WorkflowListItem | null;
  workflows: WorkflowListItem[];
  onSelect: (id: string) => void;
}) {
  const descriptionId = useId();
  return (
    <Select value={workflow?.workflowId ?? ''} onValueChange={onSelect} disabled={!workflow || workflows.length === 0}>
      <SelectTrigger
        aria-label="切换工作流"
        className="h-auto min-h-11 w-full min-w-0 max-w-sm gap-3 py-2 text-left hover:border-primary/40"
      >
        <GitBranch className="size-4 shrink-0 text-primary" aria-hidden />
        <span className="min-w-0 flex-1">
          <SelectValue placeholder="选择工作流">
            {workflow && (
              <span className="block min-w-0">
                <span className="block truncate font-medium">{workflow.title}</span>
                <span className="mt-0.5 block truncate font-mono text-[10px] text-muted-foreground">{workflow.workflowId}</span>
              </span>
            )}
          </SelectValue>
        </span>
      </SelectTrigger>
      <SelectContent aria-label="当前用户可执行的工作流" aria-describedby={descriptionId} className="max-w-[calc(100vw-24px)]">
        <div className="border-b border-border px-3 py-2.5">
          <p id={descriptionId} className="text-xs font-medium">当前用户可执行的工作流</p>
        </div>
        <SelectGroup className="py-1">
          {workflows.map((item) => (
            <SelectItem key={item.workflowId} value={item.workflowId} textValue={item.title} className="py-2.5">
              <span className="block min-w-0">
                <span className="block truncate font-medium">{item.title}</span>
                <span className="mt-0.5 block truncate font-mono text-[10px] text-muted-foreground">{item.workflowId}</span>
              </span>
            </SelectItem>
          ))}
        </SelectGroup>
      </SelectContent>
    </Select>
  );
}
