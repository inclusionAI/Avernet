import { Button } from '@/components/ui/Button';
import { Empty } from '@/components/ui/Empty';
import { Skeleton } from '@/components/ui/Skeleton';
import { personalCenterActivityToTopic } from '@/domain/community/mapper';
import type {
  CommunityAuthor,
  CommunityTopic,
  CommunityTopicOpenTarget,
  InteractionSummary,
  PersonalCenterActivity,
  PersonalCenterTopicContext,
} from '@/domain/community/types';
import { TopicRow } from '../CommunityTopicList/TopicRow';
import { actorLabel } from './model';

export interface ActivityListProps {
  items: PersonalCenterActivity[];
  loading: boolean;
  error: string | null;
  onRetry: () => void;
  onOpenTopic: (target: CommunityTopicOpenTarget) => void;
  currentUserId?: string;
  getAuthorSummary?: (author: CommunityAuthor) => InteractionSummary | undefined;
  loadAuthorSummary?: (author: CommunityAuthor) => void;
  followAuthor?: (author: CommunityAuthor) => Promise<boolean>;
  authorFollowLoading?: (author: CommunityAuthor) => boolean;
  authorFollowed?: (author: CommunityAuthor) => boolean;
}

function fallbackTopicContext(item: PersonalCenterActivity): PersonalCenterTopicContext {
  return {
    title: item.bodyPreview.trim() || '社区主题',
    bodyPreview: '',
    topicType: 'discussion',
    status: 'open',
    author: item.actor,
    authorDisplayName: `${actorLabel(item.actor.actorType)} · ${item.actor.id}`,
    replyCount: 0,
    likeCount: 0,
    viewCount: 0,
    favoriteCount: 0,
    isHot: false,
    createdAt: item.happenedAt,
  };
}

function activityTopic(item: PersonalCenterActivity): CommunityTopic | null {
  const context = item.topicContext ?? fallbackTopicContext(item);
  const topic = personalCenterActivityToTopic({ ...item, topicContext: context });
  if (!topic || item.activityType !== 'LIKED') return topic;

  if (item.target.targetType === 'POST') {
    const replyContent = item.bodyPreview.trim() || context.title;
    const relatedTopicContent = [context.title, context.bodyPreview]
      .map((value) => value.trim())
      .filter(Boolean)
      .join('，');
    return { ...topic, title: `回复：${replyContent}`, body: relatedTopicContent };
  }

  return { ...topic, title: `帖子：${topic.title}` };
}

export function ActivityList({
  items,
  loading,
  error,
  onRetry,
  onOpenTopic,
  currentUserId,
  getAuthorSummary,
  loadAuthorSummary,
  followAuthor,
  authorFollowLoading,
  authorFollowed,
}: ActivityListProps) {
  if (loading) {
    return (
      <div className="overflow-hidden rounded-lg border border-border bg-card" aria-label="个人中心列表加载中">
        {[1, 2, 3].map((item) => (
          <Skeleton.ListItem key={item} />
        ))}
      </div>
    );
  }
  if (error) {
    return (
      <div className="rounded-lg border border-border bg-card">
        <Empty
          title="列表加载失败"
          description={error}
          action={
            <Button variant="outline" onClick={onRetry}>
              重试
            </Button>
          }
        />
      </div>
    );
  }
  if (!items.length) {
    return (
      <div className="rounded-lg border border-border bg-card">
        <Empty title="暂无内容" description="当前身份在这个分类下还没有社区记录。" />
      </div>
    );
  }

  const replied = items[0]?.activityType === 'REPLIED';
  const visibleItems = replied
    ? items.filter((item, index) => item.topicId && item.topicContext &&
      items.findIndex((entry) => entry.topicId === item.topicId && entry.topicContext) === index)
    : items;

  if (replied && !visibleItems.length) {
    return <Empty title="暂无可展示的帖子" />;
  }

  return (
    <div
      className="bg-card"
      aria-label={replied ? '我的回帖列表' : '个人中心内容列表'}
    >
      {visibleItems.map((item) => {
        const topic = activityTopic(item);
        if (!topic) return null;
        return (
          <TopicRow
            key={replied ? topic.id : `${item.actor.actorType}:${item.actor.id}:${item.activityType}:${item.target.targetType}:${item.target.id}`}
            topic={topic}
            titleTooltip={
              item.activityType === 'LIKED' && item.target.targetType === 'POST'
                ? item.bodyPreview
                : undefined
            }
            onOpen={() =>
              onOpenTopic({
                topicId: topic.id,
                ...(!replied && (item.postId || item.target.targetType === 'POST')
                  ? { postId: item.postId ?? item.target.id }
                  : {}),
              })
            }
            currentUserId={currentUserId}
            getAuthorSummary={getAuthorSummary}
            loadAuthorSummary={loadAuthorSummary}
            followAuthor={followAuthor}
            authorFollowLoading={authorFollowLoading}
            authorFollowed={authorFollowed}
          />
        );
      })}
    </div>
  );
}
