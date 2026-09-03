import { Card } from '@/components/ui/Card';
import { IconButton } from '@/components/ui/IconButton';
import { Input } from '@/components/ui/Input';
import { Segmented } from '@/components/ui/Segmented';
import type { BotSearchMode } from '@/domain/collaborationSquare/types';
import { Search, X } from 'lucide-react';

interface SquareSearchBarProps {
  resource: 'bot' | 'group';
  query: string;
  mode?: BotSearchMode;
  onQueryChange: (query: string) => void;
  onModeChange?: (mode: BotSearchMode) => void;
}

export default function SquareSearchBar({
  resource,
  query,
  mode = 'name',
  onQueryChange,
  onModeChange,
}: SquareSearchBarProps) {
  const label = resource === 'bot' ? (mode === 'smart' ? '描述你需要的职责或能力' : '搜索 Bot 名称') : '搜索协作群名称';
  return (
    <Card className="flex flex-col gap-3 p-4 md:flex-row md:items-center">
      {resource === 'bot' && onModeChange && (
        <Segmented
          value={mode}
          options={[
            { value: 'name', label: '名称搜索' },
            { value: 'smart', label: '智能搜索' },
          ]}
          onChange={onModeChange}
          className="w-full md:w-56"
        />
      )}
      <div className="relative min-w-0 flex-1">
        <Search aria-hidden className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-[var(--color-muted)]" />
        <Input
          aria-label={label}
          value={query}
          placeholder={label}
          onChange={(event) => onQueryChange(event.target.value)}
          className="pl-9 pr-10"
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
    </Card>
  );
}
