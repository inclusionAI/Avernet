import type {
  CommunityAuthor,
  CommunityTopic,
  ForumTargetRef,
  InteractionStats,
  InteractionSummary,
} from '@/domain/community/types';
import { TopicInteractionBar } from '../TopicInteractionBar';
import { Floor } from './Floor';
import { PollPanel } from './PollPanel';

const EMPTY_STATS: InteractionStats = {
  viewCount: 0,
  likeCount: 0,
  favoriteCount: 0,
  followCount: 0,
  acceptCount: 0,
  topicCount: 0,
  replyCount: 0,
  acceptedReplyCount: 0,
  replyParticipantCount: 0,
  helpfulReplyCount: 0,
};

interface TopicFirstFloorProps {
  topic: CommunityTopic;
  currentUserId?: string;
  targetSummary?: InteractionSummary;
  viewerTopicSummary?: InteractionSummary;
  getAuthorSummary?: (author: CommunityAuthor) => InteractionSummary | undefined;
  loadAuthorSummary?: (author: CommunityAuthor) => void;
  followAuthor?: (author: CommunityAuthor) => Promise<boolean>;
  authorFollowLoading?: (author: CommunityAuthor) => boolean;
  authorFollowed?: (author: CommunityAuthor) => boolean;
  likePending?: boolean;
  favoritePending?: boolean;
  onLike?: (target: ForumTargetRef) => void;
  onFavorite?: (target: ForumTargetRef) => void;
  registerElement: (postId?: string) => (element: HTMLElement | null) => void;
}

export function TopicFirstFloor({
  topic,
  currentUserId,
  targetSummary,
  viewerTopicSummary,
  getAuthorSummary,
  loadAuthorSummary,
  followAuthor,
  authorFollowLoading,
  authorFollowed,
  likePending,
  favoritePending,
  onLike,
  onFavorite,
  registerElement,
}: TopicFirstFloorProps) {
  return (
    <Floor
      topic={topic}
      floor={0}
      author={topic.author}
      body={topic.body}
      createdAt={topic.createdAt}
      contentExtra={
        <PollPanel
          topicType={topic.topicType}
          status={topic.status}
          poll={topic.topicConfig?.poll}
          pollStats={topic.analysis?.pollStats}
        />
      }
      topicActionRow={
        <TopicInteractionBar
          topicId={topic.id}
          topicStats={
            targetSummary
              ? {
                  viewCount: targetSummary.viewCount,
                  likeCount: targetSummary.likeCount,
                  favoriteCount: targetSummary.favoriteCount,
                  followCount: targetSummary.followCount,
                  acceptCount: targetSummary.acceptCount,
                  topicCount: targetSummary.topicCount,
                  replyCount: targetSummary.replyCount,
                  acceptedReplyCount: targetSummary.acceptedReplyCount,
                  replyParticipantCount: targetSummary.replyParticipantCount,
                  helpfulReplyCount: targetSummary.helpfulReplyCount,
                }
              : EMPTY_STATS
          }
          publishedAt={topic.createdAt}
          liked={(viewerTopicSummary?.likeCount ?? 0) > 0}
          favorited={(viewerTopicSummary?.favoriteCount ?? 0) > 0}
          likePending={likePending === true}
          favoritePending={favoritePending === true}
          onLike={(target) => onLike?.(target)}
          onFavorite={(target) => onFavorite?.(target)}
        />
      }
      threadReplies={[]}
      expanded={false}
      onToggleThreadReplies={() => undefined}
      registerElement={registerElement}
      currentUserId={currentUserId}
      getAuthorSummary={getAuthorSummary}
      loadAuthorSummary={loadAuthorSummary}
      followAuthor={followAuthor}
      authorFollowLoading={authorFollowLoading}
      authorFollowed={authorFollowed}
    />
  );
}
