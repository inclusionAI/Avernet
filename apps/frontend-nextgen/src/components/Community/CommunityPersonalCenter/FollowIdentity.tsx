import { Button } from '@/components/ui/Button';
import type { CommunityAuthor, InteractionSummary, PersonalCenterActivity } from '@/domain/community/types';
import { CommunityAuthorAvatar } from '../CommunityAuthorAvatar';
import { profileStats } from '../profileStats';

export function followedAuthor(item: PersonalCenterActivity): CommunityAuthor {
  const isBot = item.target.targetType === 'BOT';
  const botUuid = isBot && item.target.id.includes(':') ? item.target.id : undefined;
  const botId = isBot ? item.target.idAlias ?? item.target.id.split(':')[0] : undefined;
  return {
    type: isBot ? 'bot' : 'human',
    id: item.target.id,
    ...(botUuid ? { botUuid } : {}),
    ...(botId ? { botId } : {}),
    displayName: item.followedContext?.displayName || item.bodyPreview.trim() || item.target.id,
    avatarUrl: item.followedContext?.avatarUrl,
  };
}

export function FollowIdentity({
  item,
  selected,
  summary,
  onSelect,
  onUnfollow,
}: {
  item: PersonalCenterActivity;
  selected: boolean;
  summary?: InteractionSummary;
  onSelect: (item: PersonalCenterActivity) => void;
  onUnfollow: (item: PersonalCenterActivity) => Promise<void>;
}) {
  const author = followedAuthor(item);
  return (
    <div
      role="button"
      tabIndex={0}
      aria-pressed={selected}
      className={`flex cursor-pointer items-center min-h-[62px] gap-3 rounded-xl border px-4 py-3 transition-all duration-300 ${
        selected ? 'border-primary bg-primary/5' : 'border-border hover:bg-muted/40'
      }`}
      onClick={() => onSelect(item)}
      onKeyDown={(event) => {
        if (event.target !== event.currentTarget) return;
        if (event.key !== 'Enter' && event.key !== ' ') return;
        event.preventDefault();
        onSelect(item);
      }}
    >
      <CommunityAuthorAvatar author={author} size={32} />
      <div className="min-w-0 flex-1">
        <div className="truncate text-sm font-medium">{author.displayName}</div>
        <div className="mt-1 flex flex-wrap items-center gap-x-2 text-xs text-muted-foreground">
          {profileStats(summary).map(({ count, label }, index) => (
            <span key={label}>
              {index > 0 && (
                <span className="mr-2" aria-hidden>
                  ·
                </span>
              )}
              {count} {label}
            </span>
          ))}
        </div>
      </div>
      <Button
        variant="ghost"
        className="h-auto shrink-0 rounded-none px-0 text-xs text-muted-foreground hover:text-foreground"
        aria-label={`取消关注 ${author.displayName}`}
        onClick={(event) => {
          event.stopPropagation();
          void onUnfollow(item);
        }}
      >
        取消关注
      </Button>
    </div>
  );
}
