import { Avatar } from '@/components/ui/Avatar';
import { Button } from '@/components/ui/Button';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/Popover';
import type { CommunityAuthor } from '@/domain/community/types';
import type { InteractionSummary } from '@/domain/community/types';
import { Bot, User, UserPlus } from 'lucide-react';
import { useCallback, useEffect, useRef, useState, type CSSProperties } from 'react';

export interface CommunityAuthorAvatarProps {
  author: CommunityAuthor;
  size?: number;
  currentUserId?: string;
  summary?: InteractionSummary;
  summaryLoading?: boolean;
  onLoadSummary?: () => void;
  onFollow?: () => void;
  followLoading?: boolean;
  followed?: boolean;
}

function avatarContent(
  author: CommunityAuthor,
  size: number,
): JSX.Element {
  if (author.avatarUrl) return <Avatar name={author.displayName} src={author.avatarUrl} size={size} />;

  const Icon = author.type === 'bot' ? Bot : User;
  const iconSize = Math.round(size * 0.5);
  const style: CSSProperties = { width: size, height: size };
  return (
    <span
      role="img"
      aria-label={author.displayName}
      className="inline-flex shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary"
      style={style}
    >
      <Icon aria-hidden width={iconSize} height={iconSize} />
    </span>
  );
}

/**
 * 社区作者头像：默认只渲染头像；详情页传入统计回调后启用 hover 卡片。
 * 统计数永远是服务端 summary，不在前端做点赞/关注后的本地持久状态。
 */
export function CommunityAuthorAvatar({
  author,
  size = 40,
  currentUserId,
  summary,
  summaryLoading,
  onLoadSummary,
  onFollow,
  followLoading,
  followed = false,
}: CommunityAuthorAvatarProps): JSX.Element {
  const [open, setOpen] = useState(false);
  const pendingCloseRef = useRef<number | undefined>(undefined);
  const cancelPendingClose = useCallback(() => {
    if (pendingCloseRef.current === undefined) return;
    window.clearTimeout(pendingCloseRef.current);
    pendingCloseRef.current = undefined;
  }, []);
  const openCard = useCallback(() => {
    if (open) return;
    setOpen(true);
    if (!summaryLoading) onLoadSummary?.();
  }, [open, onLoadSummary, summaryLoading]);
  const closeCard = useCallback(() => {
    cancelPendingClose();
    setOpen(false);
  }, [cancelPendingClose]);
  const scheduleClose = useCallback(() => {
    cancelPendingClose();
    pendingCloseRef.current = window.setTimeout(() => setOpen(false), 150);
  }, [cancelPendingClose]);
  useEffect(() => cancelPendingClose, [cancelPendingClose]);
  const baseAvatar = avatarContent(author, size);
  if (!onLoadSummary || currentUserId === undefined) return baseAvatar;
  const isSelf = author.id === currentUserId && author.type === 'human';

  return (
    <Popover open={open} onOpenChange={(next) => (next ? openCard() : closeCard())}>
      <PopoverTrigger asChild>
        <Button
          variant="ghost"
          size="icon"
          className="rounded-full p-0 focus-visible:ring-2 focus-visible:ring-primary"
          style={{ width: size, height: size }}
          aria-label={`查看${author.displayName}资料`}
          onMouseEnter={openCard}
          onMouseLeave={scheduleClose}
          onClick={(event) => {
            event.preventDefault();
            event.stopPropagation();
            openCard();
          }}
        >
          {baseAvatar}
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-64" onMouseEnter={cancelPendingClose} onMouseLeave={closeCard}>
        <div className="flex items-center gap-3">
          {avatarContent(author, 40)}
          <div className="min-w-0 flex-1">
            <p className="m-0 truncate text-sm font-semibold text-foreground">{author.displayName}</p>
            <p className="m-0 mt-0.5 truncate text-xs text-muted-foreground">
              {author.type === 'bot' ? 'BOT' : 'HUMAN'} · {author.id}
            </p>
          </div>
        </div>
        {summaryLoading ? (
          <p className="m-0 mt-3 text-xs text-muted-foreground">统计加载中...</p>
        ) : (
          <dl className="m-0 mt-3 grid grid-cols-2 gap-2 text-xs text-muted-foreground">
            <div>
              <dt>关注者</dt>
              <dd className="m-0 text-sm font-semibold text-foreground">{summary?.followCount ?? 0}</dd>
            </div>
            <div>
              <dt>发布主题</dt>
              <dd className="m-0 text-sm font-semibold text-foreground">{summary?.topicCount ?? 0}</dd>
            </div>
            <div>
              <dt>回帖</dt>
              <dd className="m-0 text-sm font-semibold text-foreground">{summary?.replyCount ?? 0}</dd>
            </div>
            <div>
              <dt>被采纳回帖</dt>
              <dd className="m-0 text-sm font-semibold text-foreground">
                {summary?.acceptedReplyCount ?? 0}
              </dd>
            </div>
          </dl>
        )}
        {!isSelf && onFollow ? (
          <Button
            variant="outline"
            size="sm"
            className="mt-3 h-8 w-full justify-start px-3 text-sm"
            leftIcon={<UserPlus className="h-4 w-4" aria-hidden />}
            loading={followLoading}
            aria-pressed={followed}
            onClick={onFollow}
          >
            {followed ? '已关注' : '关注'}
          </Button>
        ) : null}
      </PopoverContent>
    </Popover>
  );
}
