import { Button, Segmented } from '@/components/ui';
import { Input } from '@/components/ui/Input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/Select';
import type { Space } from '@/domain/admin/models';
import type { TodoBotOption, TodoStatus } from '@/domain/todos/models';
import { TODO_STATUS_OPTIONS } from '@/domain/todos/status';
import { Plus, Search } from 'lucide-react';

interface TodoToolbarProps {
  space?: Space;
  bots: TodoBotOption[];
  botsLoading: boolean;
  keyword: string;
  status?: TodoStatus;
  botId?: string;
  onKeywordChange: (value: string) => void;
  onStatusChange: (value: TodoStatus | undefined) => void;
  onBotIdChange: (value: string | undefined) => void;
  onCreate: () => void;
}

export function TodoToolbar({
  space,
  bots,
  botsLoading,
  keyword,
  status,
  botId,
  onKeywordChange,
  onStatusChange,
  onBotIdChange,
  onCreate,
}: TodoToolbarProps) {
  const activeStatus = status ?? 'all';

  return (
    <section role="region" aria-label="待办筛选" className="space-y-3">
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        <span>当前空间</span>
        <span className="rounded-md border border-border bg-muted/40 px-2 py-1 font-medium text-foreground">
          {space?.spaceName ?? '空间加载中…'}
        </span>
        {space?.spaceType === 'PERSONAL' ? <span>个人空间</span> : null}
        {space?.spaceType === 'TEAM' ? <span>团队空间</span> : null}
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <Segmented
          aria-label="待办状态"
          value={activeStatus}
          options={TODO_STATUS_OPTIONS}
          onChange={(value) => onStatusChange(value === 'all' ? undefined : value)}
          className="flex-wrap"
          activeOptionClassName="bg-primary text-primary-foreground hover:bg-primary"
          inactiveOptionClassName="text-foreground/70"
        />

        <span aria-hidden className="mx-1 h-4 w-px bg-border" />

        <Select
          value={botId ?? 'all'}
          onValueChange={(value) => onBotIdChange(value === 'all' ? undefined : value)}
          disabled={botsLoading}
        >
          <SelectTrigger aria-label="负责人" className="h-8 w-40 rounded-lg">
            <SelectValue placeholder={botsLoading ? '负责人加载中…' : '全部负责人'} />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">全部负责人</SelectItem>
            {bots.map((bot) => (
              <SelectItem key={bot.botId} value={bot.botId}>
                {bot.botName}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        <div className="relative w-48 min-w-0">
          <Search
            className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground"
            aria-hidden
          />
          <Input
            value={keyword}
            onChange={(event) => onKeywordChange(event.target.value)}
            placeholder="搜索待办标题"
            aria-label="搜索待办"
            className="pl-8"
          />
        </div>

        <Button className="ml-auto" onClick={onCreate} leftIcon={<Plus className="size-4" aria-hidden />}>
          发起待办
        </Button>
      </div>
    </section>
  );
}
