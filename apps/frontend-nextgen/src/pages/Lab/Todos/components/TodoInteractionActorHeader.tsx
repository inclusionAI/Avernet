import { Avatar } from '@/components/ui/Avatar';
import { BotDisplayAvatar } from '@/components/Workspace/BotDisplayAvatar';
import { formatInteractionTime } from './todoInteractionModel';

interface TodoInteractionActorHeaderProps {
  actor: 'human' | 'bot';
  createdAt: string;
  botId: string;
  botName: string;
  humanName: string;
  humanAvatarUrl?: string;
}

export function TodoInteractionActorHeader({
  actor,
  createdAt,
  botId,
  botName,
  humanName,
  humanAvatarUrl,
}: TodoInteractionActorHeaderProps) {
  const isBot = actor === 'bot';
  return (
    <div className="flex items-center gap-3">
      {isBot ? (
        <BotDisplayAvatar botId={botId} name={botName} size={40} />
      ) : (
        <Avatar name={humanName} src={humanAvatarUrl} size={40} />
      )}
      <div className="flex min-w-0 flex-wrap items-center gap-2">
        <span className="truncate text-sm font-semibold text-foreground">{isBot ? botName : humanName}</span>
        {isBot ? <span className="rounded-md bg-muted px-2 py-0.5 text-xs text-muted-foreground">Bot</span> : null}
        <time className="text-sm tabular-nums text-muted-foreground">{formatInteractionTime(createdAt)}</time>
      </div>
    </div>
  );
}
