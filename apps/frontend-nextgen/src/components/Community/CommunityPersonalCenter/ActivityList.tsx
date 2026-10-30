import { Button } from '@/components/ui/Button';
import { Empty } from '@/components/ui/Empty';
import { Skeleton } from '@/components/ui/Skeleton';
import { personalCenterActivityToTopic } from '@/domain/community/mapper';
import type {
  CommunityAuthor,
  CommunityTopic,
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
  onOpenTopic: (topicId: string) => void;
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

function ReplyActivityGroup({
  items,
  onOpenTopic,
}: {
  items: PersonalCenterActivity[];
  onOpenTopic: (topicId: string) => void;
}) {
  const topicId = items[0]?.topicId;
  const title =
    items[0]?.topicContext?.title ?? items[0]?.bodyPreview ?? `话题 #${topicId ?? ''}`;
  return (
    <Button
      variant="outline"
      className="block h-auto w-full rounded-lg p-3 text-left transition-colors hover:bg-muted/40"
      onClick={() => topicId && onOpenTopic(topicId)}
    >
      <span className="block w-full">
        <span className="block truncate text-sm font-semibold text-foreground">{title}</span>
        <span className="mt-2 block space-y-2">
          {items.map((item) => (
            <span key={item.postId ?? `${item.target.targetType}:${item.target.id}`} className="block rounded-md bg-muted/40 p-2.5">
              {item.repliedToPost ? (
                <span className="mb-1 block truncate text-[11px] text-muted-foreground">
                  回复 @{item.repliedToPost.authorDisplayName}：{item.repliedToPost.bodyPreview}
                </span>
              ) : null}
              <span className="block truncate text-xs leading-relaxed text-foreground/90">
                {item.bodyPreview}
              </span>
            </span>
          ))}
        </span>
      </span>
    </Button>
  );
}

function groupReplyActivities(items: PersonalCenterActivity[]): PersonalCenterActivity[][] {
  const groups = new Map<string, PersonalCenterActivity[]>();
  items.forEach((item) => {
    const key = item.topicId ?? item.target.id;
    groups.set(key, [...(groups.get(key) ?? []), item]);
  });
  return [...groups.values()];
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

  if (items[0]?.activityType === 'REPLIED') {
    return (
      <div className="space-y-3" aria-label="我的回贴列表">
        {groupReplyActivities(items).map((group, index) => (
          <ReplyActivityGroup
            key={group[0]?.topicId ?? `reply-group-${index}`}
            items={group}
            onOpenTopic={onOpenTopic}
          />
        ))}
      </div>
    );
  }

  return (
    <div className="overflow-hidden rounded-lg border border-border bg-card" aria-label="个人中心内容列表">
      {items.map((item) => {
        const topic = activityTopic(item);
        if (!topic) return null;
        return (
          <TopicRow
            key={`${item.actor.actorType}:${item.actor.id}:${item.activityType}:${item.target.targetType}:${item.target.id}`}
            topic={topic}
            titleTooltip={
              item.activityType === 'LIKED' && item.target.targetType === 'POST'
                ? item.bodyPreview
                : undefined
            }
            onOpen={() => onOpenTopic(topic.id)}
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
