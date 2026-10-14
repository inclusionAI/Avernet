// 身份选择触发按钮（各布局形态：default/sidebar/collapsed/collaboration/sidebarChip）。
// 从 IdentitySelectorParts 拆出为独立文件（工程红线：Component 单文件 ≤300 行），
// 渲染语义与拆分前一一对应，无行为变化。
import { Button, type ButtonProps } from '@/components/ui';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import { getBotEngineLabel } from '@/domain/botEngine';
import { resolveOpenApiUserId } from '@/domain/userIdentity';
import type { Identity } from '@/services/workspace/workspaceModel';
import { cn } from '@/utils/cn';
import { ChevronDown, Settings2 } from 'lucide-react';
import { forwardRef } from 'react';
import { IdentityAvatar, IdentityDetails, type IdentitySelectorLayout } from './IdentitySelectorParts';

interface IdentityTriggerButtonProps extends Omit<ButtonProps, 'children' | 'variant'> {
  identity: Identity;
  open: boolean;
  layout: IdentitySelectorLayout;
  userAvatarUrl?: string;
}

export const IdentityTriggerButton = forwardRef<HTMLButtonElement, IdentityTriggerButtonProps>(
  ({ identity, open, layout, userAvatarUrl, className, ...buttonProps }, ref) => {
    const inlineLayout = layout === 'inline';
    const sidebarLayout = layout === 'sidebar';
    const collapsedLayout = layout === 'collapsed';
    const collaborationLayout = layout === 'collaboration';
    const sidebarChipLayout = layout === 'sidebarChip';
    const navigationLayout = sidebarLayout || collapsedLayout;

    return (
      <Button
        ref={ref}
        {...buttonProps}
        variant={navigationLayout ? 'ghost' : 'outline'}
        aria-expanded={open}
        aria-label={`当前协作身份：${identity.name}`}
        className={cn(
          inlineLayout
            ? 'h-9 min-w-0 flex-1 justify-between gap-2 rounded-lg px-2.5 text-left text-xs'
            : collapsedLayout
            ? 'h-8 w-8 shrink-0 rounded-full border border-border bg-muted/40 p-0 text-xs font-semibold text-primary hover:bg-muted hover:text-primary'
            : sidebarChipLayout
            ? 'h-8 w-full rounded-lg bg-transparent px-2 text-left hover:bg-accent/50 hover:text-foreground'
            : collaborationLayout
            ? 'h-auto min-h-10 w-full justify-between gap-2 rounded-lg border border-border bg-muted/40 px-4 py-2 text-left text-xs text-foreground hover:bg-muted hover:text-foreground'
            : 'h-auto w-full justify-between gap-2 text-left',
          sidebarLayout
            ? 'min-h-9 rounded-lg border border-border bg-muted/40 px-2.5 py-1.5 text-foreground hover:bg-muted hover:text-foreground'
            : !inlineLayout && !collapsedLayout && !collaborationLayout && !sidebarChipLayout && 'min-h-10 rounded-lg px-2 py-1',
          navigationLayout && open && 'border-primary',
          className,
        )}
      >
        {collapsedLayout ? (
          identity.kind === 'user' ? (
            <IdentityAvatar identity={identity} size="xs" userAvatarUrl={userAvatarUrl} />
          ) : (
            <span aria-hidden>{Array.from(identity.name.trim())[0] ?? '?'}</span>
          )
        ) : sidebarChipLayout ? (
          /* v2 设计稿(dmore artboard-010 Frame 1912056221)协作侧栏身份行：16px 头像 +
             名称 + (短 ID) + 用户|Bot 身份分段指示 + 右端设置入口；整行触发身份切换 Popover。 */
          <>
            <IdentityAvatar identity={identity} size="xxs" userAvatarUrl={userAvatarUrl} />
            <span className="min-w-0 shrink truncate text-xs text-foreground">{identity.name}</span>
            <span className="shrink-0 truncate text-xs text-muted-foreground">
              ({resolveOpenApiUserId(identity.id)})
            </span>
            <span
              aria-hidden="true"
              className="ml-1 flex h-[18px] shrink-0 items-center gap-0.5 rounded-md bg-muted p-0.5"
            >
              {(
                [
                  { key: 'user' as const, label: '用户' },
                  { key: 'bot' as const, label: 'Bot' },
                ]
              ).map((seg) => (
                <span
                  key={seg.key}
                  className={cn(
                    'flex h-4 items-center rounded-[4px] px-1.5 text-[11px] leading-none',
                    identity.kind === seg.key
                      ? 'bg-background font-medium text-foreground shadow-[0_1px_3px_rgba(0,0,0,0.1)]'
                      : 'text-muted-foreground',
                  )}
                >
                  {seg.label}
                </span>
              ))}
            </span>
            <Settings2 className="ml-auto h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />
          </>
        ) : (
          <>
            <IdentityAvatar
              identity={identity}
              size={inlineLayout || sidebarLayout || collaborationLayout ? 'xs' : 'sm'}
              userAvatarUrl={userAvatarUrl}
            />
            {inlineLayout ? (
              <>
                <TooltipProvider delayDuration={200}>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <span className="min-w-0 flex-1 truncate">{identity.name}</span>
                    </TooltipTrigger>
                    <TooltipContent>{identity.name}</TooltipContent>
                  </Tooltip>
                </TooltipProvider>
                {identity.kind === 'bot' ? (
                  <TooltipProvider delayDuration={200}>
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <span className="max-w-[40%] shrink-0 truncate rounded-md bg-muted px-2 py-0.5 text-xs font-normal text-foreground">
                          {getBotEngineLabel(identity.engine) || '引擎类型暂无'}
                        </span>
                      </TooltipTrigger>
                      <TooltipContent>{getBotEngineLabel(identity.engine) || '引擎类型暂无'}</TooltipContent>
                    </Tooltip>
                  </TooltipProvider>
                ) : (
                  <span className="shrink-0 rounded-md bg-primary/10 px-2 py-0.5 text-xs font-normal text-primary">
                    用户
                  </span>
                )}
              </>
            ) : (
              <IdentityDetails
                identity={identity}
                compact={sidebarLayout || collaborationLayout}
                summaryOnly={sidebarLayout}
                userIdLabel={collaborationLayout ? '用户 ID' : undefined}
              />
            )}
            <ChevronDown
              className={cn(
                collaborationLayout ? 'h-3 w-3' : sidebarLayout ? 'h-3.5 w-3.5' : 'h-4 w-4',
                'shrink-0 text-muted-foreground transition-transform',
                open && 'rotate-180',
              )}
            />
          </>
        )}
      </Button>
    );
  },
);
IdentityTriggerButton.displayName = 'IdentityTriggerButton';