import { Button } from '@/components/ui';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import { cn } from '@/utils/cn';

export type SessionTabValue = 'all' | 'favorite';

interface SessionTabsProps {
  value: SessionTabValue;
  onChange: (value: SessionTabValue) => void;
  /** 仅在不会与外层总数重复的场景显示数量。 */
  showCount?: boolean;
  allCount?: number;
  favoriteCount?: number;
  /** 数量文案：默认兼容 Bot 侧栏的括号形式；协作群使用标签后缀形式。 */
  countFormat?: 'parenthesized' | 'suffix';
  className?: string;
  /** 传值后收藏项不可用并以 Tooltip 说明(bot 单聊等暂无收藏能力的场景)。 */
  favoriteDisabledReason?: string;
}

/** 会话列表过滤：全部 / 已收藏；数量作为弱化的辅助信息展示。 */
export function SessionTabs({
  value,
  allCount = 0,
  favoriteCount = 0,
  countFormat = 'parenthesized',
  onChange,
  showCount = false,
  favoriteDisabledReason,
  className,
}: SessionTabsProps) {
  const renderTab = (tabValue: SessionTabValue, label: string, disabledReason?: string) => {
    const active = value === tabValue;
    const count = tabValue === 'all' ? allCount : favoriteCount;
    const compactLabel = tabValue === 'all' ? '全部' : '已收藏';
    const usesSuffixCount = showCount && countFormat === 'suffix';
    const buttonLabel = showCount && countFormat === 'parenthesized' ? `${compactLabel} (${count})` : label;
    const accessibleLabel = showCount ? (usesSuffixCount ? `${label} ${count}` : buttonLabel) : label;
    const className = cn(
      'h-8 rounded-md border-0 px-3 text-xs',
      active
        ? 'bg-primary/10 font-medium text-primary hover:bg-primary/15 hover:text-primary'
        : 'bg-transparent font-normal text-muted-foreground hover:bg-accent hover:text-foreground',
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
              aria-label={accessibleLabel}
              className={cn(className, 'cursor-not-allowed opacity-50')}
            >
              <span>{buttonLabel}</span>
              {usesSuffixCount && (
                <span aria-hidden="true" className="tabular-nums text-muted-foreground">
                  {count}
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
        aria-label={accessibleLabel}
        aria-pressed={active}
        onClick={(e) => {
          e.stopPropagation();
          onChange(tabValue);
        }}
        className={className}
      >
        <span>{buttonLabel}</span>
        {usesSuffixCount && (
          <span aria-hidden="true" className={cn('tabular-nums', active ? 'text-primary/70' : 'text-muted-foreground')}>
            {count}
          </span>
        )}
      </Button>
    );
  };

  return (
    <TooltipProvider delayDuration={300}>
      <div className={cn('flex items-center gap-1', className)}>
        {renderTab('all', '全部会话')}
        {renderTab('favorite', '已收藏会话', favoriteDisabledReason)}
      </div>
    </TooltipProvider>
  );
}
