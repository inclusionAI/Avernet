import { Button } from '@/components/ui/Button';
import type { ForumTargetRef, InteractionStats } from '@/domain/community/types';
import { Eye, Heart, Star } from 'lucide-react';

export interface TopicInteractionBarProps {
  topicId: string;
  topicStats: InteractionStats;
  likePending: boolean;
  favoritePending: boolean;
  onLike: (target: ForumTargetRef) => void;
  onFavorite: (target: ForumTargetRef) => void;
}

/**
 * Interaction controls are event buttons, not state toggles. Counts always come
 * from the latest server-side summary; only the in-flight request is disabled.
 */
export function TopicInteractionBar({
  topicId,
  topicStats,
  likePending,
  favoritePending,
  onLike,
  onFavorite,
}: TopicInteractionBarProps) {
  const target: ForumTargetRef = { targetType: 'TOPIC', id: topicId };
  return (
    <section
      aria-label="主题互动"
      className="flex flex-wrap items-center gap-2 rounded-xl border border-border bg-card px-4 py-3"
    >
      <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
        <Eye className="size-4" aria-hidden />
        {topicStats.viewCount} 浏览
      </span>
      <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
        <Heart className="size-4" aria-hidden />
        {topicStats.likeCount} 点赞
      </span>
      <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
        <Star className="size-4" aria-hidden />
        {topicStats.favoriteCount} 收藏
      </span>
      <span className="ml-auto inline-flex items-center gap-2">
        <Button
          variant="outline"
          size="sm"
          className="h-8 px-3 text-xs"
          aria-label="点赞主题"
          loading={likePending}
          onClick={() => onLike(target)}
        >
          点赞
        </Button>
        <Button
          variant="outline"
          size="sm"
          className="h-8 px-3 text-xs"
          aria-label="收藏主题"
          loading={favoritePending}
          onClick={() => onFavorite(target)}
        >
          收藏
        </Button>
      </span>
    </section>
  );
}
