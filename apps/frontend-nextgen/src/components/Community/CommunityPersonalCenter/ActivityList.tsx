import { Button } from '@/components/ui/Button';
import { Empty } from '@/components/ui/Empty';
import { Skeleton } from '@/components/ui/Skeleton';
import { personalCenterActivityToTopic } from '@/domain/community/mapper';
import type {
  CommunityTopic,
  PersonalCenterActivity,
  PersonalCenterTopicContext,
} from '@/domain/community/types';
import { TopicRow } from '../CommunityTopicList/TopicRow';
import { useMemo } from 'react';
import { actorLabel } from './model';
import { useResolvedBackendBotNames } from './useResolvedBackendBotNames';

export interface ActivityListProps {
  items: PersonalCenterActivity[];
  loading: boolean;
  error: string | null;
  onRetry: () => void;
  onOpenTopic: (topicId: string) => void;
}

function fallbackTopicContext(
  item: PersonalCenterActivity,
  botNames: BotNameById,
): PersonalCenterTopicContext {
  return {
    title: item.bodyPreview.trim() || '社区主题',
    bodyPreview: '',
    topicType: 'discussion',
    status: 'open',
    author: item.actor,
    authorDisplayName:
      item.actor.actorType === 'BOT' && botNames[item.actor.id]
        ? `${actorLabel(item.actor.actorType)} · ${botNames[item.actor.id]}`
        : `${actorLabel(item.actor.actorType)} · ${item.actor.id}`,
    replyCount: 0,
    likeCount: 0,
    viewCount: 0,
    favoriteCount: 0,
    isHot: false,
    createdAt: item.happenedAt,
  };
}

function activityTopic(
  item: PersonalCenterActivity,
  botNames: BotNameById,
): CommunityTopic | null {
  const context = item.topicContext ?? fallbackTopicContext(item, botNames);
  return personalCenterActivityToTopic({ ...item, topicContext: context });
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

type BotNameById = Record<string, string>;

function missingBotName(displayName: string | undefined, botId: string, actorType: 'HUMAN' | 'BOT'): boolean {
  if (actorType !== 'BOT') return false;
  const trimmed = displayName?.trim() ?? '';
  return !trimmed || trimmed === botId || trimmed === `${actorLabel('BOT')} · ${botId}`;
}

function activityBotIds(items: PersonalCenterActivity[]): string[] {
  const ids = new Set<string>();
  items.forEach((item) => {
    if (
      missingBotName(
        item.topicContext?.authorDisplayName,
        item.topicContext?.author.id ?? '',
        item.topicContext?.author.actorType ?? 'HUMAN',
      )
    ) {
      ids.add(item.topicContext!.author.id);
    }
    if (missingBotName(item.repliedToPost?.authorDisplayName, item.repliedToPost?.author.id ?? '', item.repliedToPost?.author.actorType ?? 'HUMAN')) {
      ids.add(item.repliedToPost!.author.id);
    }
    if (!item.topicContext && missingBotName('', item.actor.id, item.actor.actorType)) {
      ids.add(item.actor.id);
    }
  });
  return [...ids];
}

function resolvedBotName(existing: string, botId: string, names: BotNameById): string {
  return names[botId] || existing || botId;
}

function withResolvedBotNames(
  item: PersonalCenterActivity,
  names: BotNameById,
): PersonalCenterActivity {
  const topicContext = item.topicContext;
  const nextTopicContext =
    topicContext?.author.actorType === 'BOT'
      ? {
          ...topicContext,
          authorDisplayName: resolvedBotName(
            topicContext.authorDisplayName,
            topicContext.author.id,
            names,
          ),
        }
      : topicContext;
  const repliedToPost = item.repliedToPost;
  const nextRepliedToPost =
    repliedToPost?.author.actorType === 'BOT'
      ? {
          ...repliedToPost,
          authorDisplayName: resolvedBotName(
            repliedToPost.authorDisplayName,
            repliedToPost.author.id,
            names,
          ),
        }
      : repliedToPost;
  return { ...item, topicContext: nextTopicContext, repliedToPost: nextRepliedToPost };
}

export function ActivityList({ items, loading, error, onRetry, onOpenTopic }: ActivityListProps) {
  const botIds = useMemo(() => activityBotIds(items), [items]);
  const { names: botNameById, loading: botNamesLoading } =
    useResolvedBackendBotNames(botIds);

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
  if (botNamesLoading) {
    return (
      <div className="overflow-hidden rounded-lg border border-border bg-card" aria-label="Bot 名称解析中">
        {[1, 2, 3].map((item) => (
          <Skeleton.ListItem key={item} />
        ))}
      </div>
    );
  }

  if (items[0]?.activityType === 'REPLIED') {
    return (
      <div className="space-y-3" aria-label="我的回贴列表">
        {groupReplyActivities(items).map((group, index) => (
          <ReplyActivityGroup
            key={group[0]?.topicId ?? `reply-group-${index}`}
            items={group.map((item) => withResolvedBotNames(item, botNameById))}
            onOpenTopic={onOpenTopic}
          />
        ))}
      </div>
    );
  }

  return (
    <div className="overflow-hidden rounded-lg border border-border bg-card" aria-label="个人中心内容列表">
      {items.map((item) => {
        const topic = activityTopic(withResolvedBotNames(item, botNameById), botNameById);
        if (!topic) return null;
        return (
          <TopicRow
            key={`${item.actor.actorType}:${item.actor.id}:${item.activityType}:${item.target.targetType}:${item.target.id}`}
            topic={topic}
            onOpen={() => onOpenTopic(topic.id)}
          />
        );
      })}
    </div>
  );
}
