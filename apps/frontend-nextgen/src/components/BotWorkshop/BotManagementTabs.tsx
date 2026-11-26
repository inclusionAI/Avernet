import { Button } from '@/components/ui/Button';
import { cn } from '@/utils/cn';

export type BotManagementTab = 'tc' | 'external';

/**
 * 「Bot 管理」页顶部的 TC Bot / 外部 Bot 分类切换。
 * dmore 稿（10/19 Bot管理首页）：胶囊式激活态（primary 12% 蓝底 + 蓝字 13px/500），
 * 未激活为灰字，不再使用底部下划线式 tab。
 */
export function BotManagementTabs({
  value,
  onChange,
  className,
}: {
  value: BotManagementTab;
  onChange: (tab: BotManagementTab) => void;
  className?: string;
}) {
  return (
    <div className={cn('flex items-center gap-1', className)} role="tablist" aria-label="Bot 类型">
      {(
        [
          ['tc', '内部Bot'],
          ['external', '外部 Bot'],
        ] as const
      ).map(([tab, label]) => (
        <Button
          key={tab}
          variant="ghost"
          role="tab"
          type="button"
          aria-selected={value === tab}
          className={cn(
            'inline-flex h-8 items-center rounded-md px-3 text-[13px] transition-colors cursor-pointer',
            value === tab
              ? 'bg-primary/10 font-medium text-primary'
              : 'font-normal text-muted-foreground hover:bg-muted hover:text-foreground',
          )}
          onClick={() => onChange(tab)}
        >
          {label}
        </Button>
      ))}
    </div>
  );
}