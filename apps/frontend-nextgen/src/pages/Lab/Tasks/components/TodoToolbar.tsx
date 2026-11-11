import { Input } from '@/components/ui/Input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/Select';
import { Button } from '@/components/ui/Button';
import { TODO_STATUS_OPTIONS } from '@/domain/todos/status';
import type { TodoStatus } from '@/domain/todos/models';
import type { Space } from '@/domain/admin/models';
import { Plus, Search } from 'lucide-react';

interface TodoToolbarProps {
  space?: Space;
  keyword: string;
  status?: TodoStatus;
  onKeywordChange: (value: string) => void;
  onStatusChange: (value: TodoStatus | undefined) => void;
  onCreate: () => void;
}

export function TodoToolbar({
  space,
  keyword,
  status,
  onKeywordChange,
  onStatusChange,
  onCreate,
}: TodoToolbarProps) {
  return (
    <section className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <span>当前空间</span>
          <span className="rounded-md border border-border bg-muted/40 px-2 py-1 font-medium text-foreground">
            {space?.spaceName ?? '空间加载中…'}
          </span>
          {space?.spaceType === 'PERSONAL' ? <span>个人空间</span> : null}
          {space?.spaceType === 'TEAM' ? <span>团队空间</span> : null}
        </div>
        <Button onClick={onCreate} leftIcon={<Plus className="size-4" aria-hidden />}>
          创建待办
        </Button>
      </div>
      <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_180px]">
        <div className="relative min-w-0">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden />
          <Input
            value={keyword}
            onChange={(event) => onKeywordChange(event.target.value)}
            placeholder="搜索待办标题或描述"
            aria-label="搜索待办"
            className="pl-9"
          />
        </div>
        <Select
          value={status ?? 'all'}
          onValueChange={(value) => onStatusChange(value === 'all' ? undefined : (value as TodoStatus))}
        >
          <SelectTrigger aria-label="待办状态">
            <SelectValue placeholder="全部状态" />
          </SelectTrigger>
          <SelectContent>
            {TODO_STATUS_OPTIONS.map((option) => (
              <SelectItem key={option.value} value={option.value}>
                {option.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
    </section>
  );
}
