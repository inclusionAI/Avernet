import { Avatar, Badge, Button } from '@/components/ui';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import { getBotEngineLabel } from '@/domain/botEngine';
import { getBotTypeLabel } from '@/domain/botType';
import { resolveOpenApiUserId } from '@/domain/userIdentity';
import type { Identity } from '@/services/workspace/workspaceModel';
import { cn } from '@/utils/cn';
import { Check } from 'lucide-react';

function isAvatarUrl(avatar: string): boolean {
  return /^https?:\/\//.test(avatar);
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
  const isOnline = identity.chatStatus === 'online';
  const runtimeStatus = (
    <span
      aria-label={`Bot ${isOnline ? '在线' : '不在线'}`}
      className="inline-flex items-center gap-1"
      tabIndex={isOnline ? undefined : 0}
    >
      <span className={cn('h-1.5 w-1.5 rounded-full', isOnline ? 'bg-success' : 'bg-muted-foreground')} aria-hidden />
      {isOnline ? '在线' : '不在线'}
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
        <span className="mt-0.5 block truncate text-[10px] text-muted-foreground">
          工号：{resolveOpenApiUserId(identity.id)}
        </span>
      )}
      {!summaryOnly && identity.kind === 'bot' && (
        <span className="mt-0.5 flex min-w-0 items-center gap-2 text-[10px] text-muted-foreground">
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
      <h3 id={`identity-section-${title}`} className="px-2.5 pb-1 text-[10px] font-medium text-muted-foreground">
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
