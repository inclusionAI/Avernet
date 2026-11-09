import { Button } from '@/components/ui/Button';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import { getBotEngineLabel } from '@/domain/botEngine';
import { resolveOpenApiUserId } from '@/domain/userIdentity';
import type { Identity } from '@/services/workspace/workspaceModel';
import { cn } from '@/utils/cn';
import { Check, Search } from 'lucide-react';
import { useState } from 'react';
import { IdentityAvatar } from './IdentitySelectorParts';

const SEARCH_PLACEHOLDER = '搜索 Bot 名称、Bot ID 或引擎';

// dmore artboard-002（选择当前身份）：20px 圆头像 + 13px 名称 + 9px「用户」实底标签。
const USER_TAG = 'rounded-[4px] bg-muted px-1 text-[9px] font-medium leading-4 text-muted-foreground';
// Bot 引擎为描边标签（dmore Background+Border：1px #E6E6E7、透明底）。
const ENGINE_TAG = 'rounded-[4px] border border-divider px-1 text-[10px] font-medium leading-[15px] text-muted-foreground';

function SectionLabel({ title }: { title: string }) {
  // dmore 分组标签「用户/Bot」：10px/500、0.117px 字距、弱化灰。
  return <p className="m-0 px-1.5 pb-0.5 pt-1.5 text-[10px] font-medium tracking-[0.117px] text-muted-foreground">{title}</p>;
}

function InlineIdentityOption({
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
  const isOnline = identity.reachability
    ? identity.reachability !== 'unreachable'
    : identity.chatStatus === 'online';
  const engineLabel = identity.kind === 'bot' ? getBotEngineLabel(identity.engine) : undefined;
  return (
    <Button
      variant="ghost"
      size="sm"
      aria-current={active ? 'true' : undefined}
      onClick={() => {
        if (!active) onSelect(identity.id);
      }}
      className={cn(
        'h-auto min-h-9 w-full justify-start gap-2 rounded-lg px-1.5 py-1.5 text-left font-normal',
        active ? 'bg-accent hover:bg-accent' : 'hover:bg-accent/50',
      )}
    >
      <IdentityAvatar identity={identity} size={20} userAvatarUrl={userAvatarUrl} />
      <TooltipProvider delayDuration={200}>
        <Tooltip>
          <TooltipTrigger asChild>
            {/* 名称超长截断，悬浮通过 Tooltip 展示「名称（工号）」。 */}
            <span className="min-w-0 flex-1 truncate text-[13px] font-normal text-foreground">
              {identity.name}
            </span>
          </TooltipTrigger>
          <TooltipContent>
            {identity.name}（{resolveOpenApiUserId(identity.id)}）
          </TooltipContent>
        </Tooltip>
      </TooltipProvider>
      {identity.kind === 'user' ? (
        <span className={cn('shrink-0', USER_TAG)}>用户</span>
      ) : engineLabel ? (
        <span className={cn('shrink-0', ENGINE_TAG)}>{engineLabel}</span>
      ) : null}
      {active ? <Check className="ml-auto h-4 w-4 shrink-0 text-primary" aria-hidden /> : null}
      {identity.kind === 'bot' && isOnline ? (
        <span
          aria-label={`${identity.name} 在线`}
          className={cn('h-1.5 w-1.5 shrink-0 rounded-full bg-success', active ? 'ml-1' : 'ml-auto')}
        />
      ) : null}
    </Button>
  );
}

/**
 * 协作广场「选择当前身份」弹层（dmore artboard-002）：顶部搜索框（按 Bot 名称/Bot ID/引擎过滤）+
 * 「用户」「Bot」分组 + 36px 紧凑选项行；选中项带 Check，在线 Bot 尾部 6px 状态点。
 * 仅用于 {@link WorkspaceIdentitySelector} 的 inline 布局（协作广场模块级身份）。
 */
export function InlineIdentityList({
  userIdentities,
  botIdentities,
  activeId,
  onSelect,
  userAvatarUrl,
}: {
  userIdentities: Identity[];
  botIdentities: Identity[];
  activeId: string | null;
  onSelect: (id: string) => void;
  userAvatarUrl?: string;
}) {
  const [search, setSearch] = useState('');
  const keyword = search.trim().toLowerCase();
  const matches = (identity: Identity) =>
    !keyword ||
    identity.name.toLowerCase().includes(keyword) ||
    identity.id.toLowerCase().includes(keyword) ||
    (identity.kind === 'bot' &&
      (getBotEngineLabel(identity.engine) ?? '').toLowerCase().includes(keyword));
  const users = userIdentities.filter(matches);
  const bots = botIdentities.filter(matches);
  return (
    <div>
      <div className="flex h-8 items-center gap-2 rounded-lg border border-input bg-background px-2 focus-within:ring-1 focus-within:ring-ring">
        <Search aria-hidden className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
        <input
          type="text"
          aria-label={SEARCH_PLACEHOLDER}
          value={search}
          placeholder={SEARCH_PLACEHOLDER}
          onChange={(event) => setSearch(event.target.value)}
          className="h-full min-w-0 flex-1 border-0 bg-transparent text-xs text-foreground outline-none placeholder:text-muted-foreground"
        />
      </div>
      {/* 身份行高度 36px：列表区限高 10 行（360px），超出按协作群身份切换同款滚动展示，不再把弹层顶出页面。 */}
      <div data-testid="inline-identity-scroll" className="app-scrollbar max-h-[360px] overflow-y-auto">
        {users.length > 0 && (
          <section aria-labelledby="inline-identity-section-user">
            <SectionLabel title="用户" />
            <div className="space-y-0.5">
              {users.map((identity) => (
                <InlineIdentityOption
                  key={identity.id}
                  identity={identity}
                  active={identity.id === activeId}
                  onSelect={onSelect}
                  userAvatarUrl={userAvatarUrl}
                />
              ))}
            </div>
          </section>
        )}
        {bots.length > 0 && (
          <section aria-labelledby="inline-identity-section-bot">
            <SectionLabel title="Bot" />
            <div className="space-y-0.5">
              {bots.map((identity) => (
                <InlineIdentityOption
                  key={identity.id}
                  identity={identity}
                  active={identity.id === activeId}
                  onSelect={onSelect}
                  userAvatarUrl={userAvatarUrl}
                />
              ))}
            </div>
          </section>
        )}
        {users.length === 0 && bots.length === 0 && (
          <p className="m-0 px-2 py-6 text-center text-xs text-muted-foreground">
            没有匹配的身份，换个关键词试试。
          </p>
        )}
      </div>
    </div>
  );
}
