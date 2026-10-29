import { Button } from '@/components/ui/Button';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import type { ForumTargetRef, InteractionStats } from '@/domain/community/types';
import { cn } from '@/utils/cn';
import { Eye, Heart, Pencil, Star } from 'lucide-react';
import { formatDateTime } from '../CommunityTopicDetail/model';

export interface TopicInteractionBarProps {
  topicId: string;
  topicStats: InteractionStats;
  publishedAt: string;
  liked?: boolean;
  favorited?: boolean;
  likePending?: boolean;
  favoritePending?: boolean;
  canEdit?: boolean;
  onLike: (target: ForumTargetRef) => void;
  onFavorite: (target: ForumTargetRef) => void;
  onEdit?: () => void;
}

/**
 * Main-topic action row. As in the product design, actions live under the body,
 * share a row with the published time, and do not introduce an extra divider.
 */
export function TopicInteractionBar({
  topicId,
  topicStats,
  publishedAt,
  liked = false,
  favorited = false,
  likePending = false,
  favoritePending = false,
  canEdit = false,
  onLike,
  onFavorite,
  onEdit,
}: TopicInteractionBarProps) {
  const target: ForumTargetRef = { targetType: 'TOPIC', id: topicId };

  return (
    <section aria-label="主题互动" className="mt-2 flex items-center justify-between gap-3">
      <div className="flex items-center gap-3">
        <span className="flex items-center gap-1 text-xs text-muted-foreground">
          <Eye className="size-3.5" aria-hidden />
          <span aria-label={`浏览 ${topicStats.viewCount} 次`}>{topicStats.viewCount}</span>
        </span>
        <Button
          variant="ghost"
          size="sm"
          disabled={likePending}
          aria-label="点赞主题"
          aria-pressed={liked}
          onClick={() => onLike(target)}
          className={cn(
            'h-auto min-h-0 gap-1 px-0 text-xs font-normal transition-colors disabled:cursor-not-allowed disabled:opacity-40',
            liked ? 'text-destructive' : 'text-muted-foreground hover:text-foreground',
          )}
        >
          <Heart className={cn('size-3.5', liked && 'fill-current')} aria-hidden />
          <span aria-label={`点赞 ${topicStats.likeCount} 次`}>{topicStats.likeCount}</span>
        </Button>
        <Button
          variant="ghost"
          size="sm"
          disabled={favoritePending}
          aria-label="收藏主题"
          aria-pressed={favorited}
          onClick={() => onFavorite(target)}
          className={cn(
            'h-auto min-h-0 gap-1 px-0 text-xs font-normal transition-colors disabled:cursor-not-allowed disabled:opacity-40',
            favorited ? 'text-destructive' : 'text-muted-foreground hover:text-foreground',
          )}
        >
          <Star className={cn('size-3.5', favorited && 'fill-current')} aria-hidden />
          <span aria-label={`收藏 ${topicStats.favoriteCount} 次`}>{topicStats.favoriteCount}</span>
        </Button>
        {onEdit ? (
          <TooltipProvider>
            <Tooltip>
              <TooltipTrigger asChild>
                <span className="inline-flex">
                  <Button
                    variant="ghost"
                    size="sm"
                    disabled={!canEdit}
                    aria-label="编辑"
                    className="h-auto min-h-0 gap-1 px-0 text-xs font-normal text-muted-foreground disabled:cursor-not-allowed disabled:opacity-40"
                    onClick={onEdit}
                  >
                    <Pencil className="size-3.5" aria-hidden />
                    编辑
                  </Button>
                </span>
              </TooltipTrigger>
              <TooltipContent>{canEdit ? '编辑主题' : '仅发布人可编辑开放的帖子'}</TooltipContent>
            </Tooltip>
          </TooltipProvider>
        ) : null}
      </div>
      <span className="text-xs text-muted-foreground">发表于：{formatDateTime(publishedAt)}</span>
    </section>
  );
}
