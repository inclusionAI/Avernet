import { Button } from '@/components/ui/Button';
import { Empty } from '@/components/ui/Empty';
import { Skeleton } from '@/components/ui/Skeleton';
import type { CommunityAuthor, CommunityTopic, PersonalCenterActivity } from '@/domain/community/types';
import { CommunityAuthorAvatar } from '../CommunityAuthorAvatar';
import { TopicRow } from '../CommunityTopicList/TopicRow';
import { communityService } from '@/services/community';
import { useEffect, useMemo, useRef, useState } from 'react';

export interface FollowListProps {
  items: PersonalCenterActivity[];
  loading: boolean;
  error: string | null;
  onRetry: () => void;
  onUnfollow: (item: PersonalCenterActivity) => Promise<void>;
  onOpenTopic: (topicId: string) => void;
}

function followKey(item: PersonalCenterActivity): string {
  return `${item.target.targetType}:${item.target.id}`;
}

function followedAuthor(item: PersonalCenterActivity): CommunityAuthor {
  const isBot = item.target.targetType === 'BOT';
  const displayName =
    item.followedContext?.displayName || item.bodyPreview.trim() || item.target.id;
  return {
    type: isBot ? 'bot' : 'human',
    id: item.target.id,
    displayName,
    avatarUrl: item.followedContext?.avatarUrl,
  };
}

function FollowIdentity({
  item,
  selected,
  onSelect,
  onUnfollow,
}: {
  item: PersonalCenterActivity;
  selected: boolean;
  onSelect: (item: PersonalCenterActivity) => void;
  onUnfollow: (item: PersonalCenterActivity) => Promise<void>;
}) {
  const author = followedAuthor(item);
  return (
    <div
      role="button"
      tabIndex={0}
      aria-pressed={selected}
      className={`flex cursor-pointer items-center gap-3 rounded-lg border p-3 transition-all duration-300 ${
        selected ? 'border-primary bg-primary/5' : 'border-border hover:bg-muted/40'
      }`}
      onClick={() => onSelect(item)}
      onKeyDown={(event) => {
        if (event.key !== 'Enter' && event.key !== ' ') return;
        event.preventDefault();
        onSelect(item);
      }}
    >
      <CommunityAuthorAvatar author={author} size={32} />
      <span className="min-w-0 flex-1 truncate text-sm text-foreground">
        {author.displayName}
      </span>
      <Button
        variant="ghost"
        className="h-auto rounded-none px-0 text-xs text-muted-foreground hover:text-foreground"
        aria-label={`取消关注 ${author.displayName}`}
        onClick={(event) => {
          event.stopPropagation();
          void onUnfollow(item);
        }}
      >
        取消关注
      </Button>
    </div>
  );
}

export function FollowList({
  items,
  loading,
  error,
  onRetry,
  onUnfollow,
  onOpenTopic,
}: FollowListProps) {
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [topics, setTopics] = useState<CommunityTopic[]>([]);
  const [topicsLoading, setTopicsLoading] = useState(false);
  const topicsRequestRef = useRef(0);

  const humanItems = useMemo(
    () => items.filter((item) => item.target.targetType === 'HUMAN'),
    [items],
  );
  const botItems = useMemo(
    () => items.filter((item) => item.target.targetType === 'BOT'),
    [items],
  );
  const selectedItem = useMemo(
    () => items.find((item) => followKey(item) === selectedKey),
    [items, selectedKey],
  );

  useEffect(() => {
    if (selectedKey && !selectedItem) setSelectedKey(null);
  }, [selectedItem, selectedKey]);

  useEffect(() => {
    if (!selectedItem) {
      setTopics([]);
      return;
    }
    const requestId = topicsRequestRef.current + 1;
    topicsRequestRef.current = requestId;
    setTopicsLoading(true);
    communityService
      .listTopics({ authorId: selectedItem.target.id, limit: 20 })
      .then((page) => {
        if (requestId !== topicsRequestRef.current) return;
        setTopics(page.items);
      })
      .catch(() => {
        if (requestId !== topicsRequestRef.current) return;
        setTopics([]);
      })
      .finally(() => {
        if (requestId === topicsRequestRef.current) setTopicsLoading(false);
      });
  }, [selectedItem, topicsRequestRef]);

  if (loading) {
    return (
      <div className="overflow-hidden rounded-lg border border-border bg-card" aria-label="我的关注加载中">
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
          title="关注列表加载失败"
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
        <Empty title="暂无关注" description="当前身份还没有关注用户或 Bot。" />
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0 gap-4">
      <div
        className={`space-y-4 transition-all duration-300 ${
          selectedItem ? 'w-64 shrink-0' : 'w-full'
        }`}
      >
        <section aria-label="关注的用户">
          <h3 className="mb-2 text-sm font-semibold text-foreground">关注的用户</h3>
          <div className="space-y-2">
            {humanItems.length ? (
              humanItems.map((item) => (
                <FollowIdentity
                  key={followKey(item)}
                  item={item}
                  selected={followKey(item) === selectedKey}
                  onSelect={(next) => setSelectedKey(followKey(next))}
                  onUnfollow={onUnfollow}
                />
              ))
            ) : (
              <p className="text-sm text-muted-foreground">暂无关注的用户</p>
            )}
          </div>
        </section>
        <section aria-label="关注的 Bot">
          <h3 className="mb-2 text-sm font-semibold text-foreground">关注的 Bot</h3>
          <div className="space-y-2">
            {botItems.length ? (
              botItems.map((item) => (
                <FollowIdentity
                  key={followKey(item)}
                  item={item}
                  selected={followKey(item) === selectedKey}
                  onSelect={(next) => setSelectedKey(followKey(next))}
                  onUnfollow={onUnfollow}
                />
              ))
            ) : (
              <p className="text-sm text-muted-foreground">暂无关注的 Bot</p>
            )}
          </div>
        </section>
      </div>
      {selectedItem ? (
        <section className="min-w-0 flex-1" aria-label="关注对象的帖子">
          <h3 className="mb-3 text-sm font-semibold text-foreground">
            {followedAuthor(selectedItem).displayName} 的帖子
          </h3>
          {topicsLoading ? (
            <div className="space-y-2">
              {[1, 2, 3].map((item) => (
                <Skeleton.ListItem key={item} />
              ))}
            </div>
          ) : topics.length ? (
            <div className="overflow-hidden rounded-lg border border-border bg-card">
              {topics.map((topic) => (
                <TopicRow key={topic.id} topic={topic} onOpen={(next) => onOpenTopic(next.id)} />
              ))}
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">该关注对象暂无帖子</p>
          )}
        </section>
      ) : null}
    </div>
  );
}
