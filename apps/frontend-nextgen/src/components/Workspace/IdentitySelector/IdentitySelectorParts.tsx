import { Avatar, Badge, Button, type ButtonProps } from '@/components/ui';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import { getBotEngineLabel } from '@/domain/botEngine';
import { getBotTypeLabel } from '@/domain/botType';
import { resolveOpenApiUserId } from '@/domain/userIdentity';
import type { Identity } from '@/services/workspace/workspaceModel';
import { cn } from '@/utils/cn';
import { Check, ChevronDown, Info } from 'lucide-react';
import { forwardRef } from 'react';

function isAvatarUrl(avatar: string): boolean {
  return /^https?:\/\//.test(avatar);
}

export type IdentitySelectorLayout = 'default' | 'sidebar' | 'collapsed';

/**
 * 非折叠布局的头部标签区（sidebar / default 两形态）：
 * - 标题与说明 Tooltip 支持定制（headerLabel / headerTooltip，缺省保持全局默认文案）；
 * - default 形态在未定制标题且身份多于 1 个时显示「可切换其他协作身份」副提示。
 * 从 index.tsx 抽出（TC-G005 文件体积拆分，行为不变）。
 */
export function IdentitySectionHeader({
  layout,
  headerLabel,
  headerTooltip,
  showSwitchHint,
}: {
  layout: IdentitySelectorLayout;
  headerLabel?: string;
  headerTooltip?: string;
  showSwitchHint: boolean;
}) {
  const sidebarLayout = layout === 'sidebar';
  const defaultTooltipText =
    headerTooltip ??
    '当前工作身份决定你以个人或指定 Bot 身份使用工作区各项功能，并影响各菜单中可查看的数据和可执行的操作。';
  return (
    <TooltipProvider delayDuration={300}>
      <div
        className={cn(
          'flex items-center gap-1 px-1 text-xs text-foreground',
          sidebarLayout ? 'pb-1 font-semibold' : 'font-medium',
        )}
      >
        <span>{sidebarLayout ? headerLabel ?? '工作身份' : headerLabel ?? '当前协作身份'}</span>
        {showSwitchHint ? (
          <span className="text-[10px] font-normal text-muted-foreground">可切换其他协作身份</span>
        ) : null}
        <Tooltip>
          <TooltipTrigger asChild>
            <span
              role="img"
              aria-label={sidebarLayout ? '工作身份说明' : '协作身份说明'}
              tabIndex={0}
              className={cn(
                'inline-flex cursor-help items-center text-muted-foreground',
                sidebarLayout && 'relative top-px text-muted-foreground/70',
              )}
            >
              <Info className={sidebarLayout ? 'h-3 w-3' : 'h-3.5 w-3.5'} aria-hidden />
            </span>
          </TooltipTrigger>
          <TooltipContent>{defaultTooltipText}</TooltipContent>
        </Tooltip>
      </div>
    </TooltipProvider>
  );
}

export function IdentityAvatar({
  identity,
  size = 'md',
  userAvatarUrl,
}: {
  identity: Identity;
  size?: 'xs' | 'sm' | 'md';
  userAvatarUrl?: string;
}) {
  const avatarUrl = identity.kind === 'user' ? userAvatarUrl : identity.avatar;
  const avatarSize = size === 'xs' ? 24 : size === 'sm' ? 32 : 36;
  return (
    <span className="shrink-0">
      <Avatar
        name={identity.name}
        src={avatarUrl && isAvatarUrl(avatarUrl) ? avatarUrl : undefined}
        size={avatarSize}
      />
    </span>
  );
}

function BotRuntimeStatus({ identity }: { identity: Identity }) {
  const isOnline = identity.reachability ? identity.reachability !== 'unreachable' : identity.chatStatus === 'online';
  const runtimeStatus = (
    <span
      aria-label={`Bot ${isOnline ? '在线' : '离线'}`}
      className="inline-flex items-center gap-1"
      tabIndex={isOnline ? undefined : 0}
    >
      <span className={cn('h-1.5 w-1.5 rounded-full', isOnline ? 'bg-success' : 'bg-muted-foreground')} aria-hidden />
      {isOnline ? '在线' : '离线'}
    </span>
  );

  if (isOnline) return runtimeStatus;

  return (
    <TooltipProvider delayDuration={0}>
      <Tooltip>
        <TooltipTrigger asChild>{runtimeStatus}</TooltipTrigger>
        <TooltipContent>请检查 Bot 实例状态</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

export function IdentityDetails({
  identity,
  compact = false,
  summaryOnly = false,
}: {
  identity: Identity;
  compact?: boolean;
  summaryOnly?: boolean;
}) {
  const identityLabel = summaryOnly
    ? identity.kind === 'user'
      ? '用户'
      : 'BOT'
    : identity.kind === 'user'
    ? '用户'
    : getBotTypeLabel(identity.botType);
  const engineLabel = identity.kind === 'bot' ? getBotEngineLabel(identity.engine) : undefined;
  return (
    <span className="min-w-0 flex-1 text-left">
      <span className="flex min-w-0 items-center gap-1.5">
        <span className={cn('truncate font-medium text-foreground', compact ? 'text-xs' : 'text-sm')}>
          {identity.name}
        </span>
        {identityLabel ? (
          <Badge
            tone={identity.kind === 'user' ? 'primary' : 'neutral'}
            className="shrink-0 whitespace-nowrap rounded-sm px-1 py-0 text-[10px] font-normal leading-4"
          >
            {identityLabel}
          </Badge>
        ) : null}
      </span>
      {!summaryOnly && identity.kind === 'user' && (
        <span className="mt-0.5 block truncate text-xs text-muted-foreground">
          工号：{resolveOpenApiUserId(identity.id)}
        </span>
      )}
      {!summaryOnly && identity.kind === 'bot' && (
        <span className="mt-0.5 flex min-w-0 items-center gap-2 text-xs text-muted-foreground">
          <span className="truncate">{engineLabel || '引擎类型暂无'}</span>
          <BotRuntimeStatus identity={identity} />
        </span>
      )}
    </span>
  );
}

interface IdentityTriggerButtonProps extends Omit<ButtonProps, 'children' | 'variant'> {
  identity: Identity;
  open: boolean;
  layout: IdentitySelectorLayout;
  userAvatarUrl?: string;
}

export const IdentityTriggerButton = forwardRef<HTMLButtonElement, IdentityTriggerButtonProps>(
  ({ identity, open, layout, userAvatarUrl, className, ...buttonProps }, ref) => {
    const sidebarLayout = layout === 'sidebar';
    const collapsedLayout = layout === 'collapsed';
    const navigationLayout = sidebarLayout || collapsedLayout;

    return (
      <Button
        ref={ref}
        {...buttonProps}
        variant={navigationLayout ? 'ghost' : 'outline'}
        aria-expanded={open}
        aria-label={`当前协作身份：${identity.name}`}
        className={cn(
          collapsedLayout
            ? 'h-8 w-8 shrink-0 rounded-full border border-border bg-muted/40 p-0 text-xs font-semibold text-primary hover:bg-muted hover:text-primary'
            : 'h-auto w-full justify-between gap-2 text-left',
          sidebarLayout
            ? 'min-h-9 rounded-lg border border-border bg-muted/40 px-2.5 py-1.5 text-foreground hover:bg-muted hover:text-foreground'
            : !collapsedLayout && 'min-h-10 rounded-lg px-2 py-1',
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
        ) : (
          <>
            <IdentityAvatar identity={identity} size={sidebarLayout ? 'xs' : 'sm'} userAvatarUrl={userAvatarUrl} />
            <IdentityDetails identity={identity} compact={sidebarLayout} summaryOnly={sidebarLayout} />
            <ChevronDown
              className={cn(
                sidebarLayout ? 'h-3.5 w-3.5' : 'h-4 w-4',
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

function IdentityOption({
  identity,
  active,
  onSelect,
  userAvatarUrl,
}: {
  identity: Identity;
  active: boolean;
  onSelect: (id: string) => void;
  userAvatarUrl?: string;
}) {
  return (
    <Button
      variant="ghost"
      size="sm"
      aria-current={active ? 'true' : undefined}
      onClick={() => {
        if (!active) onSelect(identity.id);
      }}
      className={cn(
        'h-auto min-h-10 w-full justify-start gap-2 rounded-lg px-2 py-1 text-left',
        active ? 'bg-accent text-foreground hover:bg-accent' : 'hover:bg-accent',
      )}
    >
      <IdentityAvatar identity={identity} size="sm" userAvatarUrl={userAvatarUrl} />
      <IdentityDetails identity={identity} compact />
      {active ? <Check className="ml-auto h-4 w-4 shrink-0 text-primary" aria-hidden /> : null}
    </Button>
  );
}

export function IdentitySection({
  title,
  identities,
  activeId,
  onSelect,
  userAvatarUrl,
}: {
  title: string;
  identities: Identity[];
  activeId: string | null;
  onSelect: (id: string) => void;
  userAvatarUrl?: string;
}) {
  if (identities.length === 0) return null;
  return (
    <section aria-labelledby={`identity-section-${title}`}>
      <h3 id={`identity-section-${title}`} className="px-2.5 pb-1 text-xs font-medium text-muted-foreground">
        {title}
      </h3>
      <div className="space-y-1">
        {identities.map((identity) => (
          <IdentityOption
            key={identity.id}
            identity={identity}
            active={identity.id === activeId}
            onSelect={onSelect}
            userAvatarUrl={userAvatarUrl}
          />
        ))}
      </div>
    </section>
  );
}
