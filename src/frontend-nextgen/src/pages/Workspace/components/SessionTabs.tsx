import { Button } from '@/components/ui';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import { cn } from '@/utils/cn';

export type SessionTabValue = 'all' | 'favorite';

interface SessionTabsProps {
  value: SessionTabValue;
  /** 「全部」计数(未过滤)。 */
  allCount: number;
  /** 「已收藏」计数。 */
  favoriteCount: number;
  onChange: (value: SessionTabValue) => void;
  /** 传值后收藏项不可用并以 Tooltip 说明(bot 单聊等暂无收藏能力的场景)。 */
  favoriteDisabledReason?: string;
}

/** 会话列表过滤:全部 / 已收藏 文本 pill,选中项蓝色胶囊,对齐设计稿。 */
export function SessionTabs({ value, allCount, favoriteCount, onChange, favoriteDisabledReason }: SessionTabsProps) {
  const renderTab = (tabValue: SessionTabValue, label: string, disabledReason?: string) => {
    const active = value === tabValue;
    const className = cn(
      'h-auto rounded-full border-0 px-3 py-1 text-xs',
      active
        ? 'bg-[var(--color-primary-soft)] font-medium text-[var(--color-primary)] hover:bg-[var(--color-primary-soft)] hover:text-[var(--color-primary)]'
        : 'bg-transparent font-normal text-[var(--color-muted)] hover:bg-transparent',
    );
    // 不可用项用 aria-disabled(保留指针事件)挂 Tooltip,而非 disabled(收不到 hover)。
    if (disabledReason) {
      return (
        <Tooltip key={tabValue}>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size="sm"
              aria-disabled="true"
              className={cn(className, 'cursor-not-allowed opacity-50')}
            >
              {label}
            </Button>
          </TooltipTrigger>
          <TooltipContent>{disabledReason}</TooltipContent>
        </Tooltip>
      );
    }
    return (
      <Button
        key={tabValue}
        variant="ghost"
        size="sm"
        aria-pressed={active}
        onClick={(e) => {
          e.stopPropagation();
          onChange(tabValue);
        }}
        className={className}
      >
        {label}
      </Button>
    );
  };

  return (
    <TooltipProvider delayDuration={300}>
      <div className="flex items-center gap-1">
        {renderTab('all', `全部 (${allCount})`)}
        {renderTab('favorite', `已收藏 (${favoriteCount})`, favoriteDisabledReason)}
      </div>
    </TooltipProvider>
  );
}
