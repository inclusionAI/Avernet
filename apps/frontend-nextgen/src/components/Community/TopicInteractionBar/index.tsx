import { Button } from '@/components/ui/Button';
import type { ForumTargetRef, InteractionStats } from '@/domain/community/types';
import { cn } from '@/utils/cn';
import { Bookmark, Eye, Heart } from 'lucide-react';
import { formatDateTime } from '../CommunityTopicDetail/model';

export interface TopicInteractionBarProps {
  topicId: string;
  topicStats: InteractionStats;
  publishedAt: string;
  liked?: boolean;
  favorited?: boolean;
  likePending?: boolean;
  favoritePending?: boolean;
  onLike: (target: ForumTargetRef) => void;
  onFavorite: (target: ForumTargetRef) => void;
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
  onLike,
  onFavorite,
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
          aria-busy={likePending}
          aria-label="点赞主题"
          aria-pressed={liked}
          onClick={() => onLike(target)}
          className={cn(
            'h-auto min-h-0 gap-1.5 px-0 text-sm font-normal transition-colors',
            liked
              ? 'text-destructive hover:text-destructive'
              : 'text-muted-foreground hover:text-foreground',
          )}
        >
          <Heart className={cn('size-4', liked && 'fill-current')} aria-hidden />
          <span aria-label={`点赞 ${topicStats.likeCount} 次`}>{topicStats.likeCount}</span>
        </Button>
        <Button
          variant="ghost"
          size="sm"
          aria-busy={favoritePending}
          aria-label="收藏主题"
          aria-pressed={favorited}
          onClick={() => onFavorite(target)}
          className={cn(
            'h-auto min-h-0 gap-1.5 px-0 text-sm font-normal transition-colors',
            favorited ? 'text-warning hover:text-warning' : 'text-muted-foreground hover:text-foreground',
          )}
        >
          <Bookmark className={cn('size-4', favorited && 'fill-current')} aria-hidden />
          <span>{favorited ? '已收藏' : '收藏'}</span>
        </Button>
      </div>
      <span className="text-xs text-muted-foreground">发表于：{formatDateTime(publishedAt)}</span>
    </section>
  );
}
