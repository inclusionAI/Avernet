import { Badge, Button } from '@/components/ui';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import { resolveBotFriendPlatform } from '@/domain/botFriendPlatform';
import type { BotIdentityFriendView } from '@/services/workspace/botFriendConversationService';
import { cn } from '@/utils/cn';
import { ChevronDown, ChevronRight } from 'lucide-react';
import type { ReactNode } from 'react';
import { AvatarTile } from '../AvatarTile';
import { SIDEBAR_TAG_CLASS } from '../GroupSidebar/GroupItem.types';

export function getBotFriendDisplayName(friend: BotIdentityFriendView): string {
  const name = friend.displayName.trim();
  const actorId = friend.actorId.trim();
  if (friend.actorType !== 'human' || !actorId || name === actorId) return name || actorId;
  return `${name || actorId}（${actorId}）`;
}

interface BotFriendRowProps {
  friend: BotIdentityFriendView;
  expanded?: boolean;
  onToggle?: (friendUserId: string) => void;
  children?: ReactNode;
}

export function BotFriendRow({ friend, expanded = false, onToggle, children }: BotFriendRowProps) {
  const isHuman = friend.actorType === 'human';
  const platform = isHuman ? null : resolveBotFriendPlatform(friend.actorId);
  const displayName = getBotFriendDisplayName(friend);
  const avatar = (
    <AvatarTile
      label={displayName}
      className={isHuman && expanded ? 'bg-primary/15 text-primary ring-1 ring-primary/30' : undefined}
      fallbackContent={isHuman ? '👤' : <span className="text-[10px] font-semibold tracking-[0.08em]">BOT</span>}
    />
  );
  const content = (
    <div className="min-w-0 flex-1">
      <span className={cn('block truncate text-sm font-medium', isHuman ? 'text-foreground' : 'text-muted-foreground')}>
        {displayName}
      </span>
      {platform ? (
        <div className="mt-1 flex min-w-0 items-center gap-1 truncate text-xs leading-4">
          <Badge tone={platform === 'teamclaw' ? 'success' : 'primary'} className={SIDEBAR_TAG_CLASS}>
            {platform === 'teamclaw' ? '当前平台 Bot' : '外部平台 Bot'}
          </Badge>
        </div>
      ) : null}
    </div>
  );

  return (
    <div>
      <div
        className={cn(
          'group relative flex min-h-16 items-center gap-3 px-4 py-2.5 transition-colors',
          expanded ? 'bg-muted' : isHuman ? 'hover:bg-accent/50' : undefined,
        )}
      >
        {expanded ? (
          <span aria-hidden="true" className="absolute bottom-2 left-0 top-2 w-[3px] rounded-r-sm bg-primary" />
        ) : null}
        {isHuman ? (
          <Button
            variant="ghost"
            aria-label={displayName}
            aria-expanded={expanded}
            onClick={() => onToggle?.(friend.actorId)}
            className="flex h-auto min-w-0 flex-1 items-center justify-start gap-3 rounded-none px-0 py-1 text-left hover:bg-transparent"
          >
            {avatar}
            {content}
          </Button>
        ) : (
          <TooltipProvider delayDuration={0}>
            <Tooltip>
              <TooltipTrigger asChild>
                <div
                  role="button"
                  tabIndex={0}
                  aria-disabled="true"
                  className="flex h-auto min-w-0 flex-1 items-center gap-3 px-0 py-1 text-left opacity-60"
                >
                  {avatar}
                  {content}
                </div>
              </TooltipTrigger>
              <TooltipContent>{friend.disabledReason ?? '暂不支持查看 Bot 好友对话'}</TooltipContent>
            </Tooltip>
          </TooltipProvider>
        )}
        {isHuman ? (
          expanded ? (
            <ChevronDown className="h-3.5 w-3.5 shrink-0 text-primary" aria-hidden="true" />
          ) : (
            <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
          )
        ) : null}
      </div>
      {expanded && isHuman ? children : null}
    </div>
  );
}
