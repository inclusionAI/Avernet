/**
 * Bot管理「更多」菜单基础原语（collab-permission-entry-migration）：
 * 从 BotManagementMenu 抽出，保持菜单主文件在组件体积门禁内。
 */
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import { CircleHelp } from 'lucide-react';
import { Children, type ReactNode } from 'react';

/** 菜单项文本 + 右侧说明问号（hover Tooltip 展示禁用原因或操作说明）。 */
export function ActionHelp({ children, description }: { children: string; description: string }) {
  return (
    <span className="flex min-w-0 flex-1 items-center justify-between gap-2">
      {children}
      <TooltipProvider>
        <Tooltip>
          <TooltipTrigger asChild>
            <span tabIndex={0} aria-label={`${children}说明`} className="inline-flex shrink-0 text-muted-foreground">
              <CircleHelp className="size-3.5" />
            </span>
          </TooltipTrigger>
          <TooltipContent className="max-w-72">{description}</TooltipContent>
        </Tooltip>
      </TooltipProvider>
    </span>
  );
}

/**
 * 菜单分组（验收：按操作频次与风险分组，低风险高频在上）。
 * 组内条件项全部不满足时整组不渲染，避免悬空占位。
 * 分组分割线此前落在不稳定的条件交界上（上一组末项/下一组首项都可能条件缺席），
 * 先整体移除，待分组语义定稿后再以确定性锚点恢复。
 */
export function MenuSection({ children }: { children: ReactNode }) {
  const items = Children.toArray(children).filter(Boolean);
  if (items.length === 0) return null;
  return <>{items}</>;
}
