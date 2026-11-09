import { helpDimaSyncSummary, helpSourceContext } from '@/domain/communityHelp/sources';
import type { HelpTask } from '@/domain/communityHelp/types';

export function HelpTaskSourceInfo({ task }: { task: HelpTask }) {
  return (
    <div className="space-y-1 break-all text-xs leading-5 text-muted-foreground">
      <p>{helpSourceContext(task.sourceDetails)}</p>
      {task.source === '手动输入' && <p>{helpDimaSyncSummary(task)}</p>}
    </div>
  );
}
