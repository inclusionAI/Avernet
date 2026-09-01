import { Avatar, Badge, Button, Popover, PopoverContent, PopoverTrigger } from '@/components/ui';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import { getBotTypeLabel } from '@/domain/botType';
import { resolveOpenApiUserId } from '@/domain/userIdentity';
import type { Identity } from '@/services/workspace/workspaceModel';
import { cn } from '@/utils/cn';
import { Check, ChevronDown, Info, ShieldCheck } from 'lucide-react';
import { useState } from 'react';

interface WorkspaceIdentitySelectorProps {
  identities: Identity[];
  activeId: string | null;
  onChange: (id: string) => void;
  onOpenPermissions?: () => void;
  /** 顶栏右侧当前用户头像；所有用户身份复用该头像，Bot 仍使用自身头像。 */
  userAvatarUrl?: string;
}

function isAvatarUrl(avatar: string): boolean {
  return /^https?:\/\//.test(avatar);
}

function IdentityAvatar({
  identity,
  size = 'md',
  userAvatarUrl,
}: {
  identity: Identity;
  size?: 'sm' | 'md';
  userAvatarUrl?: string;
}) {
  const avatarUrl = identity.kind === 'user' ? userAvatarUrl : identity.avatar;
  return (
    <span className="shrink-0">
      <Avatar
        name={identity.name}
        src={avatarUrl && isAvatarUrl(avatarUrl) ? avatarUrl : undefined}
        size={size === 'sm' ? 32 : 36}
      />
    </span>
  );
}

function BotStatus({ identity }: { identity: Identity }) {
  // 保持既有领域语义：文案由 chatStatus 决定，状态点由 reachability 决定。
  const available = identity.chatStatus !== 'hidden';
  const reachable = identity.reachability !== 'unreachable';
  return (
    <span className="inline-flex items-center gap-1 text-[10px] text-muted-foreground">
      <span className={cn('h-1.5 w-1.5 rounded-full', reachable ? 'bg-success' : 'bg-destructive')} aria-hidden />
      {available ? '可群聊' : '不可群聊'}
    </span>
  );
}

function IdentityDetails({ identity, compact = false }: { identity: Identity; compact?: boolean }) {
  const identityLabel = identity.kind === 'user' ? '用户' : getBotTypeLabel(identity.botType);
  return (
    <span className="min-w-0 flex-1 text-left">
      <span className="flex min-w-0 items-center gap-1.5">
        <span className={cn('truncate font-medium text-foreground', compact ? 'text-xs' : 'text-sm')}>
          {identity.name}
        </span>
        {identityLabel ? <Badge tone={identity.kind === 'user' ? 'primary' : 'neutral'}>{identityLabel}</Badge> : null}
      </span>
      {identity.kind === 'user' && (
        <span className="mt-0.5 block truncate text-[10px] text-muted-foreground">
          工号：{resolveOpenApiUserId(identity.id)}
        </span>
      )}
      {identity.kind === 'bot' && (
        <span className="mt-0.5 flex min-w-0 items-center gap-2 text-[10px] text-muted-foreground">
          <span className="truncate">{identity.engine || '引擎类型暂无'}</span>
          <BotStatus identity={identity} />
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

function IdentitySection({
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

/** Workspace 业务层身份选择器：只消费已映射的 Identity，不直接读取 Store 或调用接口。 */
export function WorkspaceIdentitySelector({
  identities,
  activeId,
  onChange,
  onOpenPermissions,
  userAvatarUrl,
}: WorkspaceIdentitySelectorProps) {
  const [open, setOpen] = useState(false);
  const activeIdentity = identities.find((identity) => identity.id === activeId) ?? identities[0] ?? null;
  const userIdentities = identities.filter((identity) => identity.kind === 'user');
  const botIdentities = identities.filter((identity) => identity.kind === 'bot');

  return (
    <div className="space-y-1">
      <TooltipProvider delayDuration={0}>
        <div className="flex items-center gap-1 px-1 text-xs font-medium text-foreground">
          <span>当前协作身份</span>
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
            <TooltipContent>当前协作身份决定在下方对话或群聊中，你以个人或指定 Bot 身份可查看的数据范围</TooltipContent>
          </Tooltip>
        </div>
      </TooltipProvider>
      {activeIdentity ? (
        <Popover open={open} onOpenChange={setOpen}>
          <PopoverTrigger asChild>
            <Button
              variant="outline"
              aria-expanded={open}
              aria-label={`当前协作身份：${activeIdentity.name}`}
              className="h-auto min-h-10 w-full justify-between gap-2 rounded-lg px-2 py-1"
            >
              <IdentityAvatar identity={activeIdentity} size="sm" userAvatarUrl={userAvatarUrl} />
              <IdentityDetails identity={activeIdentity} />
              <ChevronDown
                className={cn('h-4 w-4 shrink-0 text-muted-foreground transition-transform', open && 'rotate-180')}
              />
            </Button>
          </PopoverTrigger>
          <PopoverContent align="start" className="w-[320px] max-w-[calc(100vw-24px)] p-2">
            <div className="mb-2 flex items-center justify-between gap-2 border-b border-border px-2.5 pb-2">
              <div className="min-w-0">
                <p className="text-xs font-medium text-foreground">切换协作身份</p>
                <p className="mt-0.5 text-[10px] text-muted-foreground">选择要进入的用户或 Bot 身份</p>
              </div>
              {onOpenPermissions ? (
                <Button
                  variant="ghost"
                  size="sm"
                  aria-label="进入协作权限设置"
                  className="h-7 shrink-0 gap-1 px-1.5 text-[10px] text-muted-foreground hover:text-foreground"
                  onClick={() => {
                    setOpen(false);
                    onOpenPermissions();
                  }}
                >
                  <ShieldCheck className="h-3.5 w-3.5 text-primary" aria-hidden />
                  协作权限
                </Button>
              ) : null}
            </div>
            <div className="app-scrollbar max-h-80 space-y-3 overflow-y-auto">
              <IdentitySection
                title="用户身份"
                identities={userIdentities}
                activeId={activeId}
                onSelect={(id) => {
                  onChange(id);
                  setOpen(false);
                }}
                userAvatarUrl={userAvatarUrl}
              />
              <IdentitySection
                title="Bot 身份"
                identities={botIdentities}
                activeId={activeId}
                onSelect={(id) => {
                  onChange(id);
                  setOpen(false);
                }}
                userAvatarUrl={userAvatarUrl}
              />
              {identities.length === 0 ? (
                <p className="px-2.5 py-4 text-center text-xs text-muted-foreground">暂无可协作身份</p>
              ) : null}
            </div>
          </PopoverContent>
        </Popover>
      ) : (
        <div className="rounded-lg border border-dashed border-border px-3 py-3 text-center text-xs text-muted-foreground">
          暂无可协作身份
        </div>
      )}
    </div>
  );
}
