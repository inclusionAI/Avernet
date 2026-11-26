import { Badge } from '@/components/ui/Badge';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import { communityBodyPreview } from '@/domain/community/bodyPreview';
import type { CommunityTopic, CommunityTopicType, InteractionSummary } from '@/domain/community/types';
import { Eye, Heart, Lock, MessageCircle, Star } from 'lucide-react';
import type { KeyboardEvent } from 'react';
import { CommunityAuthorAvatar } from '../CommunityAuthorAvatar';
import { TopicTypeIcon } from '../TopicTypeIcon';

const TOPIC_AUTHOR_AVATAR_SIZE = 24;

const TOPIC_TYPE_LABELS: Record<CommunityTopicType, string> = {
  discussion: '讨论',
  share: '分享',
  poll: '投票',
  help: '求助',
  notice: '通知',
};

const tagStyles: Record<CommunityTopicType, string> = {
  discussion: 'bg-primary/10 text-primary',
  share: 'bg-violet-500/10 text-violet-600',
  help: 'bg-orange-500/10 text-orange-600',
  poll: 'bg-muted text-foreground',
  notice: 'bg-teal-50 text-teal-700 dark:bg-teal-400/15 dark:text-teal-300',
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
  currentUserId?: string;
  getAuthorSummary?: (author: CommunityTopic['author']) => InteractionSummary | undefined;
  loadAuthorSummary?: (author: CommunityTopic['author']) => void;
  followAuthor?: (author: CommunityTopic['author']) => Promise<boolean>;
  titleTooltip?: string;
  hideAuthor?: boolean;
  authorFollowLoading?: (author: CommunityTopic['author']) => boolean;
  authorFollowed?: (author: CommunityTopic['author']) => boolean;
}

export function TopicRow({
  topic,
  getTopicSummary,
  onOpen,
  currentUserId,
  getAuthorSummary,
  loadAuthorSummary,
  followAuthor,
  authorFollowLoading,
  authorFollowed,
  titleTooltip,
  hideAuthor = false,
}: TopicRowProps) {
  const handleKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    if (event.target !== event.currentTarget || (event.key !== 'Enter' && event.key !== ' ')) return;
    event.preventDefault();
    onOpen(topic);
  };

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
      className="flex items-start gap-3.5 border-b border-divider py-5 outline-none transition-colors last:border-b-0 hover:bg-muted/40 focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset"
    >
      {!hideAuthor && (
        <div className="shrink-0">
          <CommunityAuthorAvatar
            author={topic.author}
            size={TOPIC_AUTHOR_AVATAR_SIZE}
            currentUserId={currentUserId}
            summary={getAuthorSummary?.(topic.author)}
            onLoadSummary={() => loadAuthorSummary?.(topic.author)}
            onFollow={() => void followAuthor?.(topic.author)}
            followLoading={authorFollowLoading?.(topic.author) === true}
            followed={authorFollowed?.(topic.author) === true}
          />
          <span className="sr-only">{topic.author.displayName}</span>
        </div>
      )}
      <div className="flex min-w-0 flex-1 flex-col gap-3">
        <div className="flex items-center gap-2">
          <span className={`inline-flex h-5 shrink-0 items-center rounded px-2 text-xs ${tagStyles[topic.topicType]}`}>
            #{TOPIC_TYPE_LABELS[topic.topicType]}
          </span>
          <TooltipProvider delayDuration={200}>
            <Tooltip>
              <TooltipTrigger asChild>
                <h3 className="m-0 min-w-0 truncate text-[13px] font-medium text-foreground">{topic.title}</h3>
              </TooltipTrigger>
              {/* 点赞动态的正文预览等补充说明（原原生 title 展示型迁移，unify-tooltip-antd） */}
              {titleTooltip ? <TooltipContent>{titleTooltip}</TooltipContent> : null}
            </Tooltip>
          </TooltipProvider>
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
          {topic.status === 'closed' && topic.topicType === 'help' ? <Badge tone="success">已解决</Badge> : null}
          {topic.status === 'closed' && topic.topicType !== 'help' ? (
            <>
              <Lock className="size-3.5 shrink-0 text-muted-foreground" aria-hidden />
              <span className="sr-only">已结帖</span>
            </>
          ) : null}
        </div>
        <p className="m-0 line-clamp-2 break-words text-sm leading-5 text-muted-foreground">
          {communityBodyPreview(topic.body)}
        </p>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs font-medium text-muted-foreground tabular-nums">
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

          <span className="truncate text-xs text-muted-foreground tabular-nums">
            {activityPrefix}
            {formatRelative(activityAt)}
          </span>
        </div>
      </div>
    </article>
  );
}
