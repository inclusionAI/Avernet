import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Empty } from '@/components/ui/Empty';
import { Skeleton } from '@/components/ui/Skeleton';
import type {
  CommunityTopicType,
  PersonalCenterActivity,
  PersonalCenterTopicContext,
} from '@/domain/community/types';
import { Heart, MessageSquare } from 'lucide-react';
import { actorLabel } from './model';

export interface ActivityListProps {
  items: PersonalCenterActivity[];
  loading: boolean;
  error: string | null;
  onRetry: () => void;
  onOpenTopic: (topicId: string) => void;
}

const TOPIC_TYPE_LABELS: Record<CommunityTopicType, string> = {
  discussion: '讨论',
  share: '分享',
  poll: '投票',
  help: '求助',
  notice: '公告',
};

function fallbackTopicContext(item: PersonalCenterActivity): PersonalCenterTopicContext {
  return {
    title: item.bodyPreview.trim() || '社区主题',
    bodyPreview: '',
    topicType: 'discussion',
    author: item.actor,
    authorDisplayName: `${actorLabel(item.actor.actorType)} · ${item.actor.id}`,
    replyCount: 0,
    likeCount: 0,
  };
}

function authorInitial(name: string): string {
  return name.trim().charAt(0).toUpperCase() || '?';
}

function relativeTime(value: string | undefined): string {
  if (!value) return '暂无回复';
  const timestamp = Date.parse(value);
  if (Number.isNaN(timestamp)) return '时间未知';
  const minutes = Math.max(0, Math.floor((Date.now() - timestamp) / 60_000));
  if (minutes < 1) return '刚刚';
  if (minutes < 60) return `${minutes} 分钟前`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} 小时前`;
  const days = Math.floor(hours / 24);
  return `${days} 天前`;
}

function TopicActivityRow({
  item,
  onOpenTopic,
}: {
  item: PersonalCenterActivity;
  onOpenTopic: (topicId: string) => void;
}) {
  const context = item.topicContext ?? fallbackTopicContext(item);
  const latestReply = context.latestReplyAt ? relativeTime(context.latestReplyAt) : '暂无回复';
  return (
    <Button
      variant="ghost"
      className="block h-auto w-full rounded-none border-b border-border/60 px-4 py-3 text-left transition-colors last:border-b-0 hover:bg-muted/40"
      onClick={() => item.topicId && onOpenTopic(item.topicId)}
    >
      <span className="grid min-w-0 gap-3 md:grid-cols-[minmax(0,1fr)_90px_150px_92px_150px] md:items-center">
        <span className="min-w-0">
          <span className="flex min-w-0 items-center gap-2">
            <span className="truncate text-sm font-semibold text-foreground">{context.title}</span>
          </span>
          {context.bodyPreview ? (
            <span className="mt-1 block line-clamp-2 text-xs leading-relaxed text-muted-foreground">
              {context.bodyPreview}
            </span>
          ) : null}
        </span>
        <span className="md:text-center">
          <Badge tone="outline" className="h-6 rounded-full px-2.5 text-[10px]">
            {TOPIC_TYPE_LABELS[context.topicType] ?? context.topicType}
          </Badge>
        </span>
        <span className="flex min-w-0 items-center gap-2">
          <span className="flex size-6 shrink-0 items-center justify-center rounded-full bg-primary text-[10px] font-medium text-primary-foreground">
            {authorInitial(context.authorDisplayName)}
          </span>
          <span className="truncate text-xs text-muted-foreground">
            {context.authorDisplayName}
          </span>
        </span>
        <span className="flex items-center gap-3 text-xs font-medium text-muted-foreground md:justify-end">
          <span className="flex items-center gap-1">
            <MessageSquare className="size-3" aria-hidden />
            <span className="tabular-nums">{context.replyCount}</span>
          </span>
          <span className="flex items-center gap-1">
            <Heart className="size-3" aria-hidden />
            <span className="tabular-nums">{context.likeCount}</span>
          </span>
        </span>
        <span className="min-w-0 truncate text-right text-xs text-muted-foreground">
          最新回复：{latestReply}
        </span>
      </span>
    </Button>
  );
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
              <span className="block line-clamp-3 text-xs leading-relaxed text-foreground/90">
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

export function ActivityList({ items, loading, error, onRetry, onOpenTopic }: ActivityListProps) {
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
      {items.map((item) => (
        <TopicActivityRow
          key={`${item.actor.actorType}:${item.actor.id}:${item.activityType}:${item.target.targetType}:${item.target.id}`}
          item={item}
          onOpenTopic={onOpenTopic}
        />
      ))}
    </div>
  );
}
