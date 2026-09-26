import { Button } from '@/components/ui';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import { cn } from '@/utils/cn';

export type SessionTabValue = 'all' | 'favorite';

interface SessionTabsProps {
  value: SessionTabValue;
  onChange: (value: SessionTabValue) => void;
  showCount?: boolean;
  allCount?: number;
  favoriteCount?: number;
  className?: string;
  /** 传值后收藏项不可用并以 Tooltip 说明(bot 单聊等暂无收藏能力的场景)。 */
  favoriteDisabledReason?: string;
}

/** 会话范围过滤：使用轻量文本 Tab，数量是辅助信息，不再与外层标题重复。 */
export function SessionTabs({
  value,
  allCount,
  favoriteCount,
  onChange,
  showCount = false,
  favoriteDisabledReason,
  className,
}: SessionTabsProps) {
  const renderTab = (tabValue: SessionTabValue, label: string, disabledReason?: string) => {
    const active = value === tabValue;
    const count = tabValue === 'all' ? allCount : favoriteCount;
    const buttonLabel = showCount ? `${label} ${count === undefined ? '…' : count}` : label;
    const classes = cn(
      'h-8 rounded-none border-0 gap-1 px-2.5 text-xs',
      active
        ? 'bg-transparent font-medium text-primary hover:bg-transparent hover:text-primary'
        : 'bg-transparent font-normal text-muted-foreground hover:bg-transparent hover:text-foreground',
    );

    if (disabledReason) {
      return (
        <Tooltip key={tabValue}>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size="sm"
              aria-disabled="true"
              aria-label={buttonLabel}
              className={cn(classes, 'cursor-not-allowed opacity-50')}
            >
              <span>{label}</span>
              {showCount && (
                <span aria-hidden="true" className="whitespace-nowrap tabular-nums text-muted-foreground">
                  {count === undefined ? '…' : count}
                </span>
              )}
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
        aria-label={buttonLabel}
        aria-pressed={active}
        onClick={(event) => {
          event.stopPropagation();
          onChange(tabValue);
        }}
        className={classes}
      >
        <span>{label}</span>
        {showCount && (
          <span
            aria-hidden="true"
            className={cn('whitespace-nowrap tabular-nums', active ? 'text-primary/80' : 'text-muted-foreground')}
          >
            {count === undefined ? '…' : count}
          </span>
        )}
      </Button>
    );
  };

  return (
    <TooltipProvider delayDuration={300}>
      <div className={cn('flex min-w-0 items-center gap-2', className)}>
        {renderTab('all', '全部会话')}
        {renderTab('favorite', '已收藏会话', favoriteDisabledReason)}
      </div>
    </TooltipProvider>
  );
}
