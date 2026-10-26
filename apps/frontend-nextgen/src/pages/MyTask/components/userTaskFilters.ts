import type { TaskListItem } from '@/domain/tasks/models';
import { getUserTaskTitle } from '../userTaskUtils';

export interface UserTaskOwnerBotOption {
  value: string;
  label: string;
  aliases?: string[];
}

interface FilterUserTaskRecordsOptions {
  taskRecords: TaskListItem[];
  keyword: string;
  ownerBotFilter: string;
  ownerBotOptions: UserTaskOwnerBotOption[];
}

/** 状态过滤由服务端完成；这里只处理列表已有的关键字与 Owner Bot 本地筛选。 */
export function filterUserTaskRecords({
  taskRecords,
  keyword,
  ownerBotFilter,
  ownerBotOptions,
}: FilterUserTaskRecordsOptions): TaskListItem[] {
  const normalizedKeyword = keyword.trim().toLowerCase();
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
    const title = getUserTaskTitle(record).toLowerCase();
    const taskId = record.task_id.toLowerCase();
    const botId = record.owner_bot_id.toLowerCase();
    const sourceType = (record.source_type ?? '').toLowerCase();
    const matchesKeyword =
      !normalizedKeyword ||
      title.includes(normalizedKeyword) ||
      taskId.includes(normalizedKeyword) ||
      botId.includes(normalizedKeyword) ||
      sourceType.includes(normalizedKeyword);
    const matchesOwnerBot = !selectedOwnerBotIds || selectedOwnerBotIds.has(botId);
    return matchesKeyword && matchesOwnerBot;
  });
}
