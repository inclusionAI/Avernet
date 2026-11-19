import { Avatar, Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui';
import type { SessionInitiator } from '@/domain/conversation/sessionInitiator';
import { UserRound } from 'lucide-react';

export function ConversationInitiatorBadge({ initiator }: { initiator?: SessionInitiator }) {
  const name = initiator?.displayName || initiator?.userId || '用户';
  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <span
            aria-label={`发起人：${name}`}
            className="inline-flex max-w-24 shrink-0 items-center gap-1 rounded bg-muted px-1 py-0.5 text-[11px] text-muted-foreground"
          >
            {initiator?.avatarUrl ? (
              <Avatar key={initiator.avatarUrl} name={name} src={initiator.avatarUrl} size={14} />
            ) : (
              <UserRound aria-hidden="true" className="size-3 shrink-0" />
            )}
            <span className="truncate">{name}</span>
          </span>
        </TooltipTrigger>
        <TooltipContent>
          发起人：{name}
          {initiator?.displayName ? `（${initiator.userId}）` : ''}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}
