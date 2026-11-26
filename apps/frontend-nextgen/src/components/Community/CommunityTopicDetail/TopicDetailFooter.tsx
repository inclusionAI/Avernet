import { Avatar } from '@/components/ui/Avatar';
import { Button } from '@/components/ui/Button';
import type { CommunityTopic, ForumTargetRef, InteractionSummary } from '@/domain/community/types';
import { Heart, MessageCircle, Star } from 'lucide-react';
import { ReplyComposer, type ReplyComposerPayload, type ReplyComposerTarget } from './ReplyComposer';
import { acceptsTopicReply } from './model';

interface TopicDetailFooterProps {
  topic: CommunityTopic;
  currentUserId?: string;
  viewer?: { displayName: string; avatarUrl?: string };
  targetSummary?: InteractionSummary;
  viewerTopicSummary?: InteractionSummary;
  likePending?: boolean;
  favoritePending?: boolean;
  onLike?: (target: ForumTargetRef) => void;
  onFavorite?: (target: ForumTargetRef) => void;
  composer: 'topic' | ReplyComposerTarget | null;
  picks: number[];
  setPicks: (picks: number[]) => void;
  replySaving: boolean;
  replyCount: number;
  onOpenComposer: () => void;
  onCancel: () => void;
  onSubmit: (payload: ReplyComposerPayload) => Promise<boolean>;
}

export function TopicDetailFooter({
  topic, currentUserId, viewer, targetSummary, viewerTopicSummary,
  likePending, favoritePending, onLike, onFavorite,
  composer, picks, setPicks, replySaving, replyCount, onOpenComposer, onCancel, onSubmit,
}: TopicDetailFooterProps) {
  const canReplyToTopic = !!currentUserId && acceptsTopicReply(topic);
  return (
    <div
      role="region"
      aria-label="帖子底部互动"
      className="shrink-0 border-t border-divider bg-background px-5 py-4"
    >
      {composer && canReplyToTopic ? (
        <ReplyComposer
          pollSelection={picks}
          onPollChange={setPicks}
          key={composer === 'topic' ? 'topic' : composer.postId}
          topic={topic}
          replyTo={composer === 'topic' ? undefined : composer}
          saving={replySaving}
          submitLabel={composer === 'topic' ? '评论' : '回复'}
          onCancel={onCancel}
          onSubmit={onSubmit}
        />
      ) : null}
      <div className={composer && canReplyToTopic ? 'mt-2 flex items-center gap-2' : 'flex items-center gap-2'}>
        {canReplyToTopic && !composer ? (
          <Button
            variant="ghost"
            className="h-10 min-w-0 flex-1 justify-start rounded-lg bg-muted/50 px-2 font-normal text-muted-foreground"
            onClick={onOpenComposer}
          >
            <Avatar size={24} name={viewer?.displayName || currentUserId} src={viewer?.avatarUrl} />
            说点什么…
          </Button>
        ) : <div className="flex-1" />}
        <Button
          variant="ghost"
          size="sm"
          aria-label="底栏点赞"
          aria-pressed={!!viewerTopicSummary?.likeCount}
          disabled={!currentUserId || likePending || !onLike}
          onClick={() => onLike?.({ targetType: 'TOPIC', id: topic.id })}
        >
          <Heart className="size-4" />
          {targetSummary?.likeCount ?? topic.likeCount ?? 0}
        </Button>
        <Button
          variant="ghost"
          size="sm"
          aria-label="底栏收藏"
          aria-pressed={!!viewerTopicSummary?.favoriteCount}
          disabled={!currentUserId || favoritePending || !onFavorite}
          onClick={() => onFavorite?.({ targetType: 'TOPIC', id: topic.id })}
        >
          <Star className="size-4" />
          {targetSummary?.favoriteCount ?? topic.favoriteCount ?? 0}
        </Button>
        {canReplyToTopic && !composer ? (
          <Button variant="ghost" size="sm" aria-label="发表评论" onClick={onOpenComposer}>
            <MessageCircle className="size-4" aria-hidden />
            {replyCount}
          </Button>
        ) : (
          <span className="inline-flex items-center gap-2 px-3 text-xs text-muted-foreground" aria-label="回帖数">
            <MessageCircle className="size-4" aria-hidden />
            {replyCount}
          </span>
        )}
      </div>
    </div>
  );
}
