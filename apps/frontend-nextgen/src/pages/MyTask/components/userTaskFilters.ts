import type { TaskListItem } from '@/domain/tasks/models';

export interface UserTaskOwnerBotOption {
  value: string;
  label: string;
  aliases?: string[];
}

interface FilterUserTaskRecordsOptions {
  taskRecords: TaskListItem[];
  ownerBotFilter: string;
  ownerBotOptions: UserTaskOwnerBotOption[];
}

/** 状态与关键字由服务端完成；Lab 的 Owner Bot 是展示层筛选，仍在本页过滤。 */
export function filterUserTaskRecords({
  taskRecords,
  ownerBotFilter,
  ownerBotOptions,
}: FilterUserTaskRecordsOptions): TaskListItem[] {
  const selectedOwnerBot = ownerBotOptions.find((item) => item.value === ownerBotFilter);
  const selectedOwnerBotIds =
    ownerBotFilter === 'all'
      ? null
      : new Set(
          [selectedOwnerBot?.value ?? ownerBotFilter, ...(selectedOwnerBot?.aliases ?? [])].map((value) =>
            value.trim().toLowerCase(),
          ),
        );

  return taskRecords.filter((record) => {
    const botId = record.owner_bot_id.toLowerCase();
    const matchesOwnerBot = !selectedOwnerBotIds || selectedOwnerBotIds.has(botId);
    return matchesOwnerBot;
  });
}
