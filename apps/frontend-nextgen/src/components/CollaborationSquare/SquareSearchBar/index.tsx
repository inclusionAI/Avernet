import { IconButton } from '@/components/ui/IconButton';
import { Input } from '@/components/ui/Input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/Select';
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
  const hasModeSelect = resource === 'bot' && Boolean(onModeChange);
  const framed = hasModeSelect || resource === 'group';
  return (
    <div className={cn('flex min-h-8 min-w-0 flex-col gap-3', className)}>
      <div className={cn(
        'relative flex w-full max-w-md items-center',
        framed ? 'h-9 rounded-lg border border-input bg-background' : 'h-8',
      )}>
        {hasModeSelect && (
          <div className="relative flex h-5 shrink-0 items-center border-r border-divider">
            <Select value={mode} onValueChange={(value) => handleModeChange(value as BotSearchMode)}>
              <SelectTrigger
                aria-label="Bot 搜索模式"
                className="h-8 w-auto gap-2 rounded-lg border-0 bg-transparent px-3 font-normal text-muted-foreground focus-visible:ring-0 focus-visible:ring-offset-0"
              >
                <SelectValue />
              </SelectTrigger>
              <SelectContent align="start" className="min-w-36 rounded-lg p-1 shadow-lg">
                <SelectItem value="name" className="min-h-9 font-normal text-muted-foreground focus:text-muted-foreground data-[state=checked]:bg-accent data-[state=checked]:text-muted-foreground [&>span:first-child]:text-primary">
                  名称搜索
                </SelectItem>
                <SelectItem value="smart" className="min-h-9 font-normal text-muted-foreground focus:text-muted-foreground data-[state=checked]:bg-accent data-[state=checked]:text-muted-foreground [&>span:first-child]:text-primary">
                  智能搜索
                </SelectItem>
              </SelectContent>
            </Select>
          </div>
        )}
        <Search aria-hidden className={cn(
          'h-4 w-4 text-muted-foreground',
          framed ? 'ml-3 shrink-0' : 'absolute left-3 top-1/2 -translate-y-1/2',
        )} />
        <Input
          aria-label={label}
          value={query}
          placeholder={label}
          onChange={(event) => onQueryChange(event.target.value)}
          className={cn('h-8 min-w-0 pr-10', framed
            ? 'flex-1 border-0 bg-transparent pl-2 shadow-none focus-visible:border-transparent focus-visible:ring-0 focus-visible:ring-offset-0'
            : 'pl-9')}
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
