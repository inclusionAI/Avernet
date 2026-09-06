import { IconButton } from '@/components/ui/IconButton';
import { Input } from '@/components/ui/Input';
import { Segmented } from '@/components/ui/Segmented';
import type { BotSearchMode } from '@/domain/collaborationSquare/types';
import { cn } from '@/utils/cn';
import { Search, X } from 'lucide-react';

interface SquareSearchBarProps {
  resource: 'bot' | 'group' | 'task';
  query: string;
  mode?: BotSearchMode;
  onQueryChange: (query: string) => void;
  onModeChange?: (mode: BotSearchMode) => void;
  className?: string;
}

export default function SquareSearchBar({
  resource,
  query,
  mode = 'name',
  onQueryChange,
  onModeChange,
  className,
}: SquareSearchBarProps) {
  const label =
    resource === 'bot'
      ? mode === 'smart'
        ? '描述你需要的职责或能力'
        : '搜索 Bot 名称'
      : resource === 'group'
      ? '搜索协作群名称'
      : '搜索任务';
  const handleModeChange = (nextMode: BotSearchMode) => {
    if (nextMode !== mode) onQueryChange('');
    onModeChange?.(nextMode);
  };
  return (
    <div className={cn('flex min-h-8 min-w-0 flex-col gap-3', className)}>
      {resource === 'bot' && onModeChange && (
        <Segmented
          aria-label="Bot 搜索模式"
          value={mode}
          options={[
            { value: 'name', label: '名称搜索' },
            { value: 'smart', label: '智能搜索' },
          ]}
          onChange={handleModeChange}
          className="w-full md:w-56"
          inactiveOptionClassName="text-muted-foreground hover:text-foreground"
        />
      )}
      <div className="relative h-8 w-full max-w-md">
        <Search aria-hidden className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
        <Input
          aria-label={label}
          value={query}
          placeholder={label}
          onChange={(event) => onQueryChange(event.target.value)}
          className="h-8 pl-9 pr-10"
        />
        {query && (
          <IconButton
            label="清除搜索"
            icon={<X aria-hidden className="h-4 w-4" />}
            onClick={() => onQueryChange('')}
            className="absolute right-0 top-0"
          />
        )}
      </div>
    </div>
  );
}
