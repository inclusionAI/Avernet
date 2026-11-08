import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/Select';
import type { UserTaskOwnerBotOption } from './userTaskFilters';

interface UserTaskOwnerBotFilterProps {
  value: string;
  options: UserTaskOwnerBotOption[];
  onChange: (value: string) => void;
}

/** 实验室任务列表的 Owner Bot 筛选器。 */
export function UserTaskOwnerBotFilter({ value, options, onChange }: UserTaskOwnerBotFilterProps) {
  return (
    <div className="flex min-w-0 items-center gap-2">
      <span className="shrink-0 text-xs font-medium text-muted-foreground">Owner Bot</span>
      <Select value={value} onValueChange={onChange}>
        <SelectTrigger aria-label="Owner Bot" className="w-full text-xs">
          <SelectValue placeholder="请选择 Owner Bot" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="all">全部 Owner Bot</SelectItem>
          {options.map((item) => (
            <SelectItem key={item.value} value={item.value}>
              {item.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}
