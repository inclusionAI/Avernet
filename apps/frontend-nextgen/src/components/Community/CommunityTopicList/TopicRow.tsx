import { Badge } from '@/components/ui/Badge';
import type { CommunityTopic, CommunityTopicType, InteractionSummary } from '@/domain/community/types';
import { Eye, Heart, Lock, MessageCircle, Star } from 'lucide-react';
import type { KeyboardEvent } from 'react';
import { TopicTypeIcon } from '../TopicTypeIcon';
import { CommunityAuthorAvatar } from '../CommunityAuthorAvatar';

const TOPIC_TYPE_LABELS: Record<CommunityTopicType, string> = {
  discussion: '讨论',
  share: '分享',
  poll: '投票',
  help: '求助',
  notice: '通知',
};

/** 相对时间（分钟前 / 小时前 / 天前 …），对齐原型。 */
function formatRelative(value: string): string {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const diffMs = Date.now() - date.getTime();
  if (diffMs < 0) return '刚刚';
  const sec = Math.floor(diffMs / 1000);
  if (sec < 60) return '刚刚';
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min} 分钟前`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr} 小时前`;
  const day = Math.floor(hr / 24);
  if (day < 30) return `${day} 天前`;
  const month = Math.floor(day / 30);
  return `${Math.floor(month / 12)} 年前`;
}

export interface TopicRowProps {
  topic: CommunityTopic;
  getTopicSummary?: (topic: CommunityTopic) => InteractionSummary | undefined;
  onOpen: (topic: CommunityTopic) => void;
}

export function TopicRow({
  topic,
  getTopicSummary,
  onOpen,
}: TopicRowProps) {
  const handleKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    if (event.key !== 'Enter' && event.key !== ' ') return;
    event.preventDefault();
    onOpen(topic);
  };

  // 列表行对齐原型 5 列：主题 / 类型 / 发帖人 / 互动数据 / 最新回复。
  const summary = getTopicSummary?.(topic);
  const replyCount = typeof topic.replyCount === 'number' ? topic.replyCount : 0;
  const viewCount = summary?.viewCount ?? topic.viewCount ?? 0;
  const likeCount = summary?.likeCount ?? topic.likeCount ?? 0;
  const favoriteCount = summary?.favoriteCount ?? topic.favoriteCount ?? 0;
  const hasReplies = replyCount > 0;
  const activityAt = hasReplies ? topic.latestActivityAt ?? topic.createdAt : topic.createdAt;
  const activityPrefix = hasReplies ? '最新回复：' : '发帖时间：';

  return (
    <article
      role="button"
      tabIndex={0}
      onClick={() => onOpen(topic)}
      onKeyDown={handleKeyDown}
      className="grid grid-cols-[1fr_100px_140px_150px_176px] items-center gap-3 border-b border-border/60 px-4 py-3 outline-none transition-colors last:border-b-0 hover:bg-muted/40 focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset"
    >
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <h3 className="m-0 min-w-0 truncate text-sm font-semibold text-foreground">{topic.title}</h3>
          {topic.topicType === 'poll' ? (
            <span className="inline-flex shrink-0" role="img" aria-label="投票主题">
              <TopicTypeIcon topicType={topic.topicType} className="size-3.5 text-muted-foreground" />
            </span>
          ) : null}
          {topic.isHot ? (
            <span className="text-sm" aria-hidden>
              🔥
            </span>
          ) : null}
          {topic.isHot ? <span className="sr-only">热榜主题</span> : null}
          {topic.topicType === 'notice' ? <Badge tone="warning">通知</Badge> : null}
          {topic.status === 'closed' && topic.topicType === 'help' ? (
            <Badge tone="success">已解决</Badge>
          ) : null}
          {topic.status === 'closed' && topic.topicType !== 'help' ? (
            <>
              <Lock className="size-3.5 shrink-0 text-muted-foreground" aria-hidden />
              <span className="sr-only">已结帖</span>
            </>
          ) : null}
        </div>
        <p className="mt-0.5 truncate text-xs text-muted-foreground">{topic.body}</p>
      </div>
      <div>
        <span className="inline-flex rounded-full bg-muted px-2.5 py-1 text-xs text-muted-foreground">
          {TOPIC_TYPE_LABELS[topic.topicType]}
        </span>
      </div>
      <div className="flex min-w-0 items-center gap-2">
        <CommunityAuthorAvatar author={topic.author} size={24} />
        <span className="truncate text-xs text-muted-foreground">{topic.author.displayName}</span>
      </div>
      <div className="flex items-center justify-end gap-2 text-xs font-medium text-muted-foreground tabular-nums">
        <span className="inline-flex items-center gap-1" aria-label={`浏览 ${viewCount}`}>
          <Eye className="size-3.5" aria-hidden />
          <span aria-hidden>{viewCount}</span>
        </span>
        <span className="inline-flex items-center gap-1" aria-label={`点赞 ${likeCount}`}>
          <Heart className="size-3.5" aria-hidden />
          <span aria-hidden>{likeCount}</span>
        </span>
        <span className="inline-flex items-center gap-1" aria-label={`收藏 ${favoriteCount}`}>
          <Star className="size-3.5" aria-hidden />
          <span aria-hidden>{favoriteCount}</span>
        </span>
        <span className="inline-flex items-center gap-1" aria-label={`回复 ${replyCount}`}>
          <MessageCircle className="size-3.5" aria-hidden />
          <span aria-hidden>{replyCount}</span>
        </span>
      </div>
      <div className="min-w-0 text-right">
        <span className="truncate text-xs text-muted-foreground tabular-nums">
          {activityPrefix}
          {formatRelative(activityAt)}
        </span>
      </div>
    </article>
  );
}
