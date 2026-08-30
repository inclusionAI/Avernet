import { Badge, Button, IconButton, Popover, PopoverContent, PopoverTrigger } from '@/components/ui';
import type { Identity, IdentityReachability, IdentityStatus } from '@/services/workspace/workspaceModel';
import { cn } from '@/utils/cn';
import { ChevronDown } from 'lucide-react';
import { useState, type ReactNode } from 'react';

interface IdentityBarProps {
  identities: Identity[];
  activeId: string | null;
  onChange: (id: string) => void;
  /** 右侧附加槽：如 <lg 打开二级会话列表抽屉的 IconButton（调用方用 lg:hidden 自行门控）。 */
  trailing?: ReactNode;
}

function isAvatarUrl(avatar: string): boolean {
  return avatar.startsWith('http');
}

function IdentityAvatar({ avatar, name }: { avatar: string; name: string }) {
  if (isAvatarUrl(avatar)) {
    return <img src={avatar} alt={name} className="h-8 w-8 shrink-0 rounded-full object-cover" loading="lazy" />;
  }
  return <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-sm">{avatar}</span>;
}

/**
 * bot 的可聊天状态行：标签文案来自 status（online→可聊天，hidden→不可聊天），
 * 圆点颜色来自 reachability（reachable→绿，unreachable→红）。
 */
function BotStatusRow({
  chatStatus,
  reachability,
}: {
  chatStatus?: IdentityStatus;
  reachability?: IdentityReachability;
}) {
  const label = chatStatus === 'hidden' ? '不可聊天' : '可聊天';
  const dotColor = reachability === 'unreachable' ? 'bg-[var(--color-error)]' : 'bg-[var(--color-success)]';
  return (
    <small className="mt-0.5 flex items-center gap-1 text-[10px] text-[var(--color-muted)]">
      <i className={cn('h-1.5 w-1.5 rounded-full', dotColor)} />
      {label}
    </small>
  );
}

interface IdentityCardProps {
  identity: Identity;
  active: boolean;
  onSelect: (id: string) => void;
  /** 布局变体：tab 为当前身份横排卡片（min-w + 不收缩）；list 为下拉内全款卡片。 */
  variant?: 'tab' | 'list';
  trailing?: ReactNode;
}

function IdentityCard({ identity, active, onSelect, variant = 'tab', trailing }: IdentityCardProps) {
  return (
    <div
      className={cn(
        'flex items-center gap-2 rounded-xl border px-2.5 py-1.5 transition-colors',
        variant === 'list' ? 'w-full shrink' : 'w-64 shrink-0',
        active
          ? 'border-[var(--color-primary-weak)] bg-[var(--color-primary-soft)]'
          : 'border-[var(--color-border)] bg-white',
      )}
    >
      <Button
        aria-label={`切换到${identity.name}`}
        variant="ghost"
        className="h-auto min-w-0 flex-1 justify-start gap-2 border-0 p-0 hover:bg-transparent"
        onClick={() => onSelect(identity.id)}
      >
        <span
          className={cn(
            'flex h-8 w-8 shrink-0 items-center justify-center overflow-hidden rounded-full',
            active ? 'bg-[var(--color-primary)] text-white' : 'bg-[var(--color-panel-strong)]',
          )}
        >
          <IdentityAvatar avatar={identity.avatar} name={identity.name} />
        </span>
        <span className="min-w-0 text-left">
          <span className="flex items-center gap-1.5">
            <b className="truncate text-xs text-[var(--color-fg)]">{identity.name}</b>
            <Badge tone={identity.kind === 'user' ? 'primary' : 'neutral'}>
              {identity.kind === 'user' ? '用户' : 'Bot'}
            </Badge>
          </span>
          {identity.kind === 'bot' && (
            <BotStatusRow chatStatus={identity.chatStatus} reachability={identity.reachability} />
          )}
        </span>
      </Button>
      {trailing}
    </div>
  );
}

export function IdentityBar({ identities, activeId, onChange, trailing }: IdentityBarProps) {
  const [open, setOpen] = useState(false);
  const activeIdentity = identities.find((i) => i.id === activeId) ?? identities[0] ?? null;
  return (
    <div className="flex h-16 items-center gap-3 border-b border-[var(--color-border)] bg-white px-4">
      <span className="hidden shrink-0 text-xs font-medium text-[var(--color-muted)] xl:inline">当前身份</span>
      {activeIdentity && (
        <div className="flex min-w-0 flex-1 items-center">
          <Popover open={open} onOpenChange={setOpen}>
            <PopoverTrigger asChild>
              <div className="w-64">
                <IdentityCard
                  identity={activeIdentity}
                  active
                  onSelect={onChange}
                  trailing={<IconButton size="sm" label="切换身份" icon={<ChevronDown className="h-3.5 w-3.5" />} />}
                />
              </div>
            </PopoverTrigger>
            <PopoverContent align="start" className="w-64 p-2">
              {identities.length === 0 ? (
                <p className="px-2 py-3 text-center text-xs text-[var(--color-muted)]">暂无可协作身份</p>
              ) : (
                <div className="app-scrollbar max-h-80 space-y-2 overflow-y-auto p-1">
                  {identities.map((identity) => (
                    <IdentityCard
                      key={identity.id}
                      identity={identity}
                      active={activeId === identity.id}
                      onSelect={(id) => {
                        onChange(id);
                        setOpen(false);
                      }}
                      variant="list"
                    />
                  ))}
                </div>
              )}
            </PopoverContent>
          </Popover>
        </div>
      )}
      {trailing ? <div className="flex items-center">{trailing}</div> : null}
    </div>
  );
}
