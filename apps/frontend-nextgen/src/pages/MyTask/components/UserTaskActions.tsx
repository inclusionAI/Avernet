import { Button } from '@/components/ui/Button';
import type { TaskListItem } from '@/domain/tasks/models';
import { Points } from '@/tracking/points';
import { cn } from '@/utils/cn';
import { Tracert } from '@/utils/tracert';
import { Eye } from 'lucide-react';
import { ViewSessionButton } from './ViewSessionButton';

export function UserTaskActions({
  record,
  lab,
  ownerBotUserIdMap,
  onSelect,
}: {
  record: TaskListItem;
  lab: boolean;
  ownerBotUserIdMap: Record<string, string>;
  onSelect: (id: string) => void;
}) {
  return (
    <div
      className={cn('flex flex-row items-center', lab ? 'gap-4 whitespace-nowrap' : 'justify-end gap-2')}
      onClick={(event) => event.stopPropagation()}
    >
      {lab ? (
        <>
          <Button
            variant="link"
            className="h-auto px-0 py-0 text-sm no-underline"
            size="sm"
            onClick={(event) => {
              event.stopPropagation();
              Tracert.click(Points.myTask.openDetail, { task_id: record.task_id });
              onSelect(record.task_id);
            }}
          >
            查看产物
          </Button>
          <ViewSessionButton
            record={record}
            label="进入对话"
            displayVariant="lab"
            ownerBotUserIdMap={ownerBotUserIdMap}
          />
        </>
      ) : (
        <>
          <ViewSessionButton record={record} ownerBotUserIdMap={ownerBotUserIdMap} />
          <Button
            variant="secondary"
            size="sm"
            leftIcon={<Eye className="size-4" />}
            onClick={(event) => {
              event.stopPropagation();
              Tracert.click(Points.myTask.openDetail, { task_id: record.task_id });
              onSelect(record.task_id);
            }}
          >
            查看详情
          </Button>
        </>
      )}
    </div>
  );
}
