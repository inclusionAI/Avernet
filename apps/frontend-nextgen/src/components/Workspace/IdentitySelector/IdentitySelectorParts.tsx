import { Avatar, Badge, Button } from '@/components/ui';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import { getBotEngineLabel } from '@/domain/botEngine';
import { getBotTypeLabel } from '@/domain/botType';
import { resolveOpenApiUserId } from '@/domain/userIdentity';
import type { Identity } from '@/services/workspace/workspaceModel';
import { cn } from '@/utils/cn';
import { Check, Info, type LucideIcon } from 'lucide-react';
import { type ReactNode } from 'react';

// 触发按钮已拆至同目录 IdentityTriggerButton.tsx（工程红线：Component 单文件 ≤300 行）；
// IdentityAvatar/IdentityDetails/IdentitySelectorLayout 仍在本文件，供其复用。
export { IdentityTriggerButton } from './IdentityTriggerButton';

function isAvatarUrl(avatar: string): boolean {
  return /^https?:\/\//.test(avatar);
}

export type IdentitySelectorLayout = 'default' | 'sidebar' | 'collapsed' | 'collaboration' | 'sidebarChip' | 'inline';

/** 非折叠布局头部标签区，保留 default/sidebar 标题与 Tooltip 合同。 */
export function IdentitySectionHeader({
  layout,
  headerLabel,
  headerTooltip,
  showSwitchHint,
  action,
}: {
  layout: IdentitySelectorLayout;
  headerLabel?: string;
  headerTooltip?: string;
  showSwitchHint: boolean;
  action?: ReactNode;
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
          layout === 'inline' && 'shrink-0 whitespace-nowrap',
          sidebarLayout ? 'pb-1 font-semibold' : layout === 'collaboration' ? 'font-semibold' : 'font-medium',
        )}
      >
        <span>{sidebarLayout ? headerLabel ?? '工作身份' : headerLabel ?? '当前协作身份'}</span>
        {showSwitchHint ? (
          <span className="text-[10px] font-normal text-muted-foreground">可切换其他协作身份</span>
        ) : null}
        {layout !== 'collaboration' || headerTooltip ? (
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
        ) : null}
        {action ? <span className="ml-auto shrink-0">{action}</span> : null}
      </div>
    </TooltipProvider>
  );
}
export function IdentityAvatar({
  identity,
  size = 'md',
  userAvatarUrl,
  className,
}: {
  identity: Identity;
  size?: 'xxs' | 'xs' | 'sm' | 'md' | 'lg';
  userAvatarUrl?: string;
  className?: string;
}) {
  const avatarUrl = identity.kind === 'user' ? userAvatarUrl : identity.avatar;
  const avatarSize = size === 'xxs' ? 16 : size === 'xs' ? 24 : size === 'sm' ? 32 : size === 'lg' ? 48 : 36;
  return (
    <span className={cn('shrink-0', className)}>
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
  userIdLabel = '工号',
  nameClassName,
}: {
  identity: Identity;
  compact?: boolean;
  summaryOnly?: boolean;
  userIdLabel?: string;
  nameClassName?: string;
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
        <span className={cn('truncate font-medium text-foreground', compact ? 'text-xs' : 'text-sm', nameClassName)}>
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
          {userIdLabel}：{resolveOpenApiUserId(identity.id)}
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
        // v2 气泡精修：36px 紧凑选项行（原 40px），静止态仅 hover 微透 accent，选中实底 + Check。
        'h-auto min-h-9 w-full justify-start gap-2 rounded-md px-1.5 py-1 text-left',
        active ? 'bg-accent text-foreground hover:bg-accent' : 'hover:bg-accent/50',
      )}
    >
      <IdentityAvatar
        identity={identity}
        size="sm"
        userAvatarUrl={userAvatarUrl}
        // 选中行给头像一圈 primary 光环，与 Check 呼应，避免选中态只有底色一种表达。
        className={cn('rounded-full', active && 'ring-2 ring-primary/20')}
      />
      <IdentityDetails identity={identity} compact />
      {active ? <Check className="ml-auto h-4 w-4 shrink-0 text-primary" aria-hidden /> : null}
    </Button>
  );
}

export function IdentitySection({
  title,
  icon: SectionIcon,
  identities,
  activeId,
  onSelect,
  userAvatarUrl,
}: {
  title: string;
  /** 小节头 kind 图标（用户=UserRound / Bot=Bot），编码两类身份的语义差异。 */
  icon: LucideIcon;
  identities: Identity[];
  activeId: string | null;
  onSelect: (id: string) => void;
  userAvatarUrl?: string;
}) {
  if (identities.length === 0) return null;
  return (
    <section aria-labelledby={`identity-section-${title}`}>
      <h3
        id={`identity-section-${title}`}
        className="flex items-center gap-1.5 px-1.5 pb-1.5 pt-1 text-[11px] font-medium text-muted-foreground"
      >
        <SectionIcon className="h-3 w-3 shrink-0 text-muted-foreground/80" aria-hidden />
        <span className="shrink-0">{title}</span>
        <span className="text-[10px] font-normal tabular-nums text-muted-foreground/70">
          {identities.length}
        </span>
      </h3>
      <div className="space-y-0.5">
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
