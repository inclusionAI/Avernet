import { Button, Popover, PopoverAnchor, PopoverContent, PopoverTrigger } from '@/components/ui';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import { useCollapsedIdentityTooltip } from '@/hooks/useCollapsedIdentityTooltip';
import type { HumanIdentityStatus } from '@/hooks/useHumanIdentity';
import type { Identity } from '@/services/workspace/workspaceModel';
import { cn } from '@/utils/cn';
import { ChevronDown, Info, Loader2 } from 'lucide-react';
import { useRef, useState } from 'react';
import {
  IdentitySection,
  IdentitySectionHeader,
  IdentityTriggerButton,
  type IdentitySelectorLayout,
} from './IdentitySelectorParts';
import { createCollaborationOpenerHandlers } from './collaborationOpener';
interface WorkspaceIdentitySelectorProps {
  identities: Identity[];
  activeId: string | null;
  onChange: (id: string) => void;
  /** 顶栏右侧当前用户头像；所有用户身份复用该头像，Bot 仍使用自身头像。 */
  userAvatarUrl?: string;
  layout?: IdentitySelectorLayout;
  identityStatus?: HumanIdentityStatus;
  identityError?: string;
  identityListLoading?: boolean;
  /** 头部标签定制（如「为 Ta 加好友：」）；定制时隐藏切换副提示，缺省显示「当前协作身份」。 */
  headerLabel?: string;
  /** 触发按钮样式定制（透传 IdentityTriggerButton）：如深浅背景页面需覆盖默认 muted 底色。 */
  triggerClassName?: string;
  /** 头部说明 Tooltip 文案定制（如协作广场公开Bot Tab 的好友关系说明）；缺省保持全局默认文案。 */
  headerTooltip?: string;
  /**
   * 身份列表加载失败时的页内重试回调（AC-6）：提供时错误态展示「重试」按钮；
   * 缺省保持原空态文案（左上角等既有消费方行为不变）。
   */
  onRetry?: () => void;
}
/** Workspace 业务层身份选择器：只消费已映射的 Identity，不直接读取 Store 或调用接口。 */
export function WorkspaceIdentitySelector({
  identities,
  activeId,
  onChange,
  userAvatarUrl,
  layout = 'default',
  identityStatus,
  identityError,
  identityListLoading,
  headerLabel,
  triggerClassName,
  headerTooltip,
  onRetry,
}: WorkspaceIdentitySelectorProps) {
  const [open, setOpen] = useState(false);
  const identityCardRef = useRef<HTMLButtonElement>(null);
  const switchButtonRef = useRef<HTMLButtonElement>(null);
  const collaborationOpenerRef = useRef<'card' | 'switch' | null>(null);
  const collapsedTooltip = useCollapsedIdentityTooltip(activeId, open);
  const activeIdentity = identities.find((identity) => identity.id === activeId) ?? identities[0] ?? null;
  const userIdentities = identities.filter((identity) => identity.kind === 'user');
  const botIdentities = identities.filter((identity) => identity.kind === 'bot');
  const sidebarLayout = layout === 'sidebar';
  const collapsedLayout = layout === 'collapsed';
  const collaborationLayout = layout === 'collaboration';
  const navigationLayout = sidebarLayout || collapsedLayout;
  const popoverWidthClass = collaborationLayout ? 'w-[var(--radix-popover-trigger-width)]' : 'w-[320px]';
  const showListLoading = identityListLoading || (identities.length === 0 && identityStatus === 'loading');
  const emptyIdentityLabel = identityStatus === 'error' ? '暂无可协作身份，请刷新重试' : '暂无可协作身份';
  const handlePopoverOpenChange = (nextOpen: boolean) => {
    setOpen(nextOpen);
    if (nextOpen && collapsedLayout) collapsedTooltip.closeAndSuppress();
  };
  const handleIdentitySelect = (id: string) => {
    if (collapsedLayout) collapsedTooltip.closeAndSuppress();
    onChange(id);
    setOpen(false);
  };
  const toggleCollaborationPopover = () => setOpen((currentOpen) => !currentOpen);
  const collaborationTriggerProps = (source: 'card' | 'switch', toggle?: () => void) =>
    collaborationLayout ? createCollaborationOpenerHandlers(collaborationOpenerRef, source, toggle) : undefined;
  const activeIdentityTrigger = activeIdentity ? (
    <IdentityTriggerButton
      ref={identityCardRef}
      identity={activeIdentity}
      open={open}
      layout={layout}
      userAvatarUrl={userAvatarUrl}
      className={triggerClassName}
      {...collaborationTriggerProps('card', toggleCollaborationPopover)}
    />
  ) : null;
  return (
    <div className={collapsedLayout ? undefined : 'space-y-1'}>
      <Popover open={open} onOpenChange={handlePopoverOpenChange}>
        {collapsedLayout ? null : (
          <IdentitySectionHeader
            layout={layout}
            headerLabel={headerLabel}
            headerTooltip={headerTooltip}
            showSwitchHint={!collaborationLayout && headerLabel === undefined && identities.length > 1}
            action={
              collaborationLayout && activeIdentity ? (
                <PopoverTrigger asChild>
                  <Button
                    ref={switchButtonRef}
                    variant="link"
                    size="sm"
                    aria-label="切换工作身份"
                    aria-expanded={open}
                    aria-haspopup="dialog"
                    {...collaborationTriggerProps('switch')}
                  >
                    切换
                  </Button>
                </PopoverTrigger>
              ) : undefined
            }
          />
        )}
        {activeIdentity ? (
          <>
            {collapsedLayout ? (
              <TooltipProvider delayDuration={300}>
                <Tooltip open={collapsedTooltip.open} onOpenChange={collapsedTooltip.onOpenChange}>
                  <TooltipTrigger asChild>
                    <span className="inline-flex" {...collapsedTooltip.triggerHandlers}>
                      <PopoverTrigger asChild>{activeIdentityTrigger}</PopoverTrigger>
                    </span>
                  </TooltipTrigger>
                  <TooltipContent side="right">{activeIdentity.name}</TooltipContent>
                </Tooltip>
              </TooltipProvider>
            ) : collaborationLayout ? (
              <PopoverAnchor asChild>{activeIdentityTrigger}</PopoverAnchor>
            ) : (
              <PopoverTrigger asChild>{activeIdentityTrigger}</PopoverTrigger>
            )}
            <PopoverContent
              align="start"
              className={cn(popoverWidthClass, 'max-w-[calc(100vw-24px)] p-2 shadow-lg')}
              onOpenAutoFocus={(event) => {
                // 不将焦点自动落到说明图标，避免打开身份列表时立即触发 Tooltip。
                event.preventDefault();
              }}
              onCloseAutoFocus={(event) => {
                if (!collaborationLayout) return;
                event.preventDefault();
                const openerRef = collaborationOpenerRef.current === 'switch' ? switchButtonRef : identityCardRef;
                openerRef.current?.focus();
              }}
            >
              <div className="mb-2 flex items-center justify-between gap-2 border-b border-border px-2.5 pb-2">
                <div className="flex min-w-0 items-center gap-1">
                  <p className="text-xs font-medium text-foreground">切换工作身份</p>
                  {!navigationLayout ? (
                    <TooltipProvider delayDuration={300}>
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <span
                            role="img"
                            aria-label="协作身份说明"
                            tabIndex={0}
                            className="inline-flex cursor-help items-center text-muted-foreground"
                          >
                            <Info className="h-3.5 w-3.5" aria-hidden />
                          </span>
                        </TooltipTrigger>
                        <TooltipContent>
                          当前工作身份决定你以个人或指定 Bot
                          身份使用工作区各项功能，并影响各菜单中可查看的数据和可执行的操作。
                        </TooltipContent>
                      </Tooltip>
                    </TooltipProvider>
                  ) : null}
                </div>
              </div>
              <div className="app-scrollbar max-h-80 space-y-3 overflow-y-auto">
                <IdentitySection
                  title="用户身份"
                  identities={userIdentities}
                  activeId={activeId}
                  onSelect={handleIdentitySelect}
                  userAvatarUrl={userAvatarUrl}
                />
                <IdentitySection
                  title="Bot 身份"
                  identities={botIdentities}
                  activeId={activeId}
                  onSelect={handleIdentitySelect}
                  userAvatarUrl={userAvatarUrl}
                />
                {identities.length === 0 ? (
                  <p className="px-2.5 py-4 text-center text-xs text-muted-foreground">{emptyIdentityLabel}</p>
                ) : null}
              </div>
            </PopoverContent>
          </>
        ) : showListLoading ? (
          <Button
            type="button"
            variant="ghost"
            disabled
            aria-live="polite"
            aria-label="协作身份加载中"
            className={cn(
              collapsedLayout
                ? 'h-8 w-8 shrink-0 rounded-full border border-border bg-muted/40 p-0'
                : collaborationLayout
                ? 'h-auto min-h-10 w-full justify-between gap-2 rounded-lg border border-border bg-muted/40 px-4 py-2 text-left text-xs text-foreground'
                : 'h-auto min-h-9 w-full justify-between gap-2 rounded-lg border border-border bg-muted/40 px-2.5 py-1.5 text-left text-foreground',
            )}
          >
            {collapsedLayout ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin text-primary" aria-hidden />
            ) : (
              <>
                <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-primary/10">
                  <Loader2 className="h-3.5 w-3.5 animate-spin text-primary" aria-hidden />
                </span>
                <span className="min-w-0 flex-1 truncate text-left text-xs font-medium">加载中…</span>
                <ChevronDown className="h-3.5 w-3.5 shrink-0 text-muted-foreground transition-transform" aria-hidden />
              </>
            )}
          </Button>
        ) : collapsedLayout ? (
          <div
            role="status"
            aria-label={emptyIdentityLabel}
            className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-dashed border-border text-xs text-muted-foreground"
          >
            ?{identityStatus === 'error' && identityError ? <span className="sr-only">{identityError}</span> : null}
          </div>
        ) : (
          <div
            role="status"
            className={cn(
              'px-3 py-3 text-center text-xs text-muted-foreground',
              !sidebarLayout && 'rounded-lg border border-dashed border-border',
            )}
          >
            {identityStatus === 'error' && onRetry ? (
              <>
                <p className="m-0">身份加载失败</p>
                <Button size="sm" variant="secondary" className="mt-2" onClick={onRetry}>
                  重试
                </Button>
              </>
            ) : (
              emptyIdentityLabel
            )}
            {identityStatus === 'error' && identityError ? <span className="sr-only">{identityError}</span> : null}
          </div>
        )}
      </Popover>
    </div>
  );
}
