import { Button } from '@/components/ui/Button';
import type { CommunityReply, InteractionSummary } from '@/domain/community/types';
import { Heart, MessageSquare, Star } from 'lucide-react';
import { CommunityAuthorAvatar } from '../CommunityAuthorAvatar';
import { formatDateTime, type ReplyElementRegistrar } from './model';
import { cn } from '@/utils/cn';

interface ThreadReplyProps {
  reply: CommunityReply;
  targetAuthorName: string;
  targetPostId?: string;
  onLocateTarget?: (postId: string) => void;
  registerElement?: ReplyElementRegistrar;
  currentUserId?: string;
  getAuthorSummary?: (author: CommunityReply['author']) => InteractionSummary | undefined;
  loadAuthorSummary?: (author: CommunityReply['author']) => void;
  followAuthor?: (author: CommunityReply['author']) => Promise<boolean>;
  authorFollowLoading?: (author: CommunityReply['author']) => boolean;
  authorFollowed?: (author: CommunityReply['author']) => boolean;
  canReply?: boolean;
  likeCount?: number;
  favoriteCount?: number;
  liked?: boolean;
  favorited?: boolean;
  likePending?: boolean;
  favoritePending?: boolean;
  onLike?: (postId: string) => void;
  onFavorite?: (postId: string) => void;
  onReply?: (target: { postId: string; authorName: string }) => void;
}

export function ThreadReply({
  reply,
  targetAuthorName,
  targetPostId,
  onLocateTarget,
  registerElement,
  currentUserId,
  getAuthorSummary,
  loadAuthorSummary,
  followAuthor,
  authorFollowLoading,
  authorFollowed,
  canReply = false,
  likeCount = 0,
  favoriteCount = 0,
  liked = false,
  favorited = false,
  likePending = false,
  favoritePending = false,
  onLike,
  onFavorite,
  onReply,
}: ThreadReplyProps) {
  return (
    <article
      data-post-id={reply.id}
      ref={registerElement?.(reply.id)}
      className="rounded-xl border border-border/60 bg-muted/40 px-3 py-2.5"
    >
      <div className="flex items-center gap-2">
        <CommunityAuthorAvatar
          author={reply.author}
          size={24}
          currentUserId={currentUserId}
          summary={getAuthorSummary?.(reply.author)}
          onLoadSummary={() => loadAuthorSummary?.(reply.author)}
          onFollow={() => void followAuthor?.(reply.author)}
          followLoading={authorFollowLoading?.(reply.author) === true}
          followed={authorFollowed?.(reply.author) === true}
        />
        <span className="text-xs font-semibold text-foreground">{reply.author.displayName}</span>
        <span className="text-xs text-muted-foreground">回复</span>
        {targetPostId ? (
          <Button
            variant="link"
            size="sm"
            className="h-auto min-h-0 p-0 text-xs"
            aria-label={`定位${targetAuthorName}`}
            onClick={() => onLocateTarget?.(targetPostId)}
          >
            @{targetAuthorName}
          </Button>
        ) : (
          <span className="text-xs font-medium text-primary">@{targetAuthorName}</span>
        )}
      </div>
      <p className="m-0 mt-2 whitespace-pre-wrap text-xs leading-relaxed text-foreground">{reply.body}</p>
      <div className="mt-2 flex items-center justify-between gap-2">
        <div className="flex items-center gap-1">
          {canReply ? (
            <Button
              variant="ghost"
              size="sm"
              className="h-auto min-h-0 gap-1 px-0 text-xs font-normal text-muted-foreground"
              aria-label="回复"
              onClick={() => onReply?.({ postId: reply.id, authorName: reply.author.displayName })}
            >
              <MessageSquare className="size-3.5" aria-hidden />
              回复
            </Button>
          ) : null}
          {onLike ? (
            <Button
              variant="ghost"
              size="sm"
              disabled={likePending}
              aria-label="点赞回复"
              aria-pressed={liked}
              onClick={() => onLike(reply.id)}
              className={cn(
                'h-auto min-h-0 gap-1 px-0 text-xs font-normal transition-colors disabled:cursor-not-allowed disabled:opacity-40',
                liked ? 'text-destructive' : 'text-muted-foreground hover:text-foreground',
              )}
            >
              <Heart className={cn('size-3.5', liked && 'fill-current')} aria-hidden />
              {likeCount}
            </Button>
          ) : null}
          {onFavorite ? (
            <Button
              variant="ghost"
              size="sm"
              disabled={favoritePending}
              aria-label="收藏回复"
              aria-pressed={favorited}
              onClick={() => onFavorite(reply.id)}
              className={cn(
                'h-auto min-h-0 gap-1 px-0 text-xs font-normal transition-colors disabled:cursor-not-allowed disabled:opacity-40',
                favorited ? 'text-destructive' : 'text-muted-foreground hover:text-foreground',
              )}
            >
              <Star className={cn('size-3.5', favorited && 'fill-current')} aria-hidden />
              {favoriteCount}
            </Button>
          ) : null}
        </div>
        <span className="text-[10px] text-muted-foreground">{formatDateTime(reply.createdAt)}</span>
      </div>
    </article>
  );
}
