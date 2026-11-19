import { Button } from '@/components/ui/Button';
import { Empty } from '@/components/ui/Empty';
import { Skeleton } from '@/components/ui/Skeleton';
import type {
  CommunityAuthor,
  CommunityTopicOpenTarget,
  InteractionSummary,
  PersonalCenterActivity,
} from '@/domain/community/types';
import { ChevronLeft } from 'lucide-react';
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { CommunityAuthorAvatar } from '../CommunityAuthorAvatar';
import { TopicRow } from '../CommunityTopicList/TopicRow';
import { profileStats } from '../profileStats';
import './FollowList.css';
import { useFollowedAuthorTopics } from './useFollowedAuthorTopics';

export interface FollowListProps {
  items: PersonalCenterActivity[];
  pagination?: ReactNode;
  loading: boolean;
  error: string | null;
  onRetry: () => void;
  onUnfollow: (item: PersonalCenterActivity) => Promise<void>;
  onOpenTopic: (target: CommunityTopicOpenTarget) => void;
  currentUserId?: string;
  getAuthorSummary?: (author: CommunityAuthor) => InteractionSummary | undefined;
  loadAuthorSummary?: (author: CommunityAuthor) => void;
  followAuthor?: (author: CommunityAuthor) => Promise<boolean>;
  authorFollowLoading?: (author: CommunityAuthor) => boolean;
  authorFollowed?: (author: CommunityAuthor) => boolean;
}

function followKey(item: PersonalCenterActivity): string {
  return `${item.target.targetType}:${item.target.id}`;
}

function followedAuthor(item: PersonalCenterActivity): CommunityAuthor {
  const isBot = item.target.targetType === 'BOT';
  const botUuid = isBot && item.target.id.includes(':') ? item.target.id : undefined;
  const botId = isBot ? item.target.idAlias ?? item.target.id.split(':')[0] : undefined;
  return {
    type: isBot ? 'bot' : 'human',
    id: item.target.id,
    ...(botUuid ? { botUuid } : {}),
    ...(botId ? { botId } : {}),
    displayName: item.followedContext?.displayName || item.bodyPreview.trim() || item.target.id,
    avatarUrl: item.followedContext?.avatarUrl,
  };
}

function FollowIdentity({
  item,
  selected,
  summary,
  onSelect,
  onUnfollow,
}: {
  item: PersonalCenterActivity;
  selected: boolean;
  summary?: InteractionSummary;
  onSelect: (item: PersonalCenterActivity) => void;
  onUnfollow: (item: PersonalCenterActivity) => Promise<void>;
}) {
  const author = followedAuthor(item);
  return (
    <div
      role="button"
      tabIndex={0}
      aria-pressed={selected}
      className={`flex cursor-pointer items-center min-h-[62px] gap-3 rounded-xl border px-4 py-3 transition-all duration-300 ${
        selected ? 'border-primary bg-primary/5' : 'border-border hover:bg-muted/40'
      }`}
      onClick={() => onSelect(item)}
      onKeyDown={(event) => {
        if (event.target !== event.currentTarget) return;
        if (event.key !== 'Enter' && event.key !== ' ') return;
        event.preventDefault();
        onSelect(item);
      }}
    >
      <CommunityAuthorAvatar author={author} size={32} />
      <div className="min-w-0 flex-1">
        <div className="truncate text-sm font-medium">{author.displayName}</div>
        <div className="mt-1 flex flex-wrap items-center gap-x-2 text-xs text-muted-foreground">
          {profileStats(summary).map(({ count, label }, index) => (
            <span key={label}>
              {index > 0 && (
                <span className="mr-2" aria-hidden>
                  ·
                </span>
              )}
              {count} {label}
            </span>
          ))}
        </div>
      </div>
      <Button
        variant="ghost"
        className="h-auto shrink-0 rounded-none px-0 text-xs text-muted-foreground hover:text-foreground"
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
  pagination,
  loading,
  error,
  onRetry,
  onUnfollow,
  onOpenTopic,
  currentUserId,
  getAuthorSummary,
  loadAuthorSummary,
  followAuthor,
  authorFollowLoading,
  authorFollowed,
}: FollowListProps) {
  const [selectedKey, setSelectedKey] = useState<string | null>(null);

  const humanItems = useMemo(() => items.filter((item) => item.target.targetType === 'HUMAN'), [items]);
  const botItems = useMemo(() => items.filter((item) => item.target.targetType === 'BOT'), [items]);
  const selectedItem = useMemo(() => items.find((item) => followKey(item) === selectedKey), [items, selectedKey]);

  useEffect(() => {
    if (selectedKey && !selectedItem) setSelectedKey(null);
  }, [selectedItem, selectedKey]);

  const selectedAuthor = selectedItem ? followedAuthor(selectedItem) : null;
  const selectedSummary = selectedAuthor ? getAuthorSummary?.(selectedAuthor) : undefined;
  const requested = useRef(new Set<string>());
  useEffect(() => {
    if (!loadAuthorSummary) return;
    for (const item of items) {
      const key = followKey(item);
      if (!requested.current.has(key)) {
        requested.current.add(key);
        loadAuthorSummary(followedAuthor(item));
      }
    }
  }, [items, loadAuthorSummary]);

  const { topics, summariesById: topicSummariesById, loading: topicsLoading } = useFollowedAuthorTopics(selectedItem);

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
      <div className={`space-y-4 transition-all duration-300 ${selectedItem ? 'hidden' : 'w-full'}`}>
        <section aria-label="关注的用户">
          <h3 className="mb-2 text-sm font-semibold text-foreground">关注的用户</h3>
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
            {humanItems.length ? (
              humanItems.map((item) => (
                <FollowIdentity
                  key={followKey(item)}
                  item={item}
                  summary={getAuthorSummary?.(followedAuthor(item))}
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
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
            {botItems.length ? (
              botItems.map((item) => (
                <FollowIdentity
                  key={followKey(item)}
                  item={item}
                  summary={getAuthorSummary?.(followedAuthor(item))}
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
        {!selectedItem && pagination}
      </div>
      {selectedItem && selectedAuthor ? (
        <section className="community-followed-author flex-1" aria-label="关注对象的帖子">
          <Button
            variant="ghost"
            className="mb-3 h-auto gap-1 px-0 text-sm font-normal text-muted-foreground"
            onClick={() => setSelectedKey(null)}
          >
            <ChevronLeft className="size-3.5" aria-hidden />
            返回我的关注列表
          </Button>
          <div className="community-followed-author__layout">
            <aside
              className="community-followed-author__profile rounded-xl border border-border bg-card"
              aria-label="关注对象资料"
            >
              <div className="community-followed-author__identity flex items-center gap-3 border-b border-divider">
                <CommunityAuthorAvatar author={selectedAuthor} size={32} />
                <div className="min-w-0 flex-1">
                  <h3 className="m-0 truncate text-sm font-semibold text-foreground">{selectedAuthor.displayName}</h3>
                  <p className="m-0 mt-1 break-all text-xs text-muted-foreground">
                    {selectedItem.target.targetType} • {selectedItem.target.idAlias ?? selectedItem.target.id}
                  </p>
                </div>
              </div>
              <dl className="community-followed-author__stats my-4 space-y-3 text-sm">
                {profileStats(selectedSummary).map(({ label, count }) => (
                  <div key={label} className="flex items-center justify-between gap-3">
                    <dt className="text-xs text-muted-foreground">{label}</dt>
                    <dd className="m-0 text-foreground tabular-nums">{count}</dd>
                  </div>
                ))}
              </dl>
              <Button
                variant="secondary"
                className="h-8 w-full border-transparent bg-muted font-normal text-muted-foreground"
                onClick={() => void onUnfollow(selectedItem)}
              >
                取消关注
              </Button>
            </aside>
            <div className="community-followed-author__topics min-w-0" aria-label="关注对象帖子列表">
              {topicsLoading ? (
                <div className="space-y-2">
                  {[1, 2, 3].map((item) => (
                    <Skeleton.ListItem key={item} />
                  ))}
                </div>
              ) : topics.length ? (
                topics.map((topic) => (
                  <TopicRow
                    key={topic.id}
                    hideAuthor
                    topic={topic}
                    getTopicSummary={(next) => topicSummariesById[next.id]}
                    onOpen={(next) => onOpenTopic({ topicId: next.id })}
                    currentUserId={currentUserId}
                    getAuthorSummary={getAuthorSummary}
                    loadAuthorSummary={loadAuthorSummary}
                    followAuthor={followAuthor}
                    authorFollowLoading={authorFollowLoading}
                    authorFollowed={authorFollowed}
                  />
                ))
              ) : (
                <p className="text-sm text-muted-foreground">该关注对象暂无帖子</p>
              )}
            </div>
          </div>
        </section>
      ) : null}
    </div>
  );
}
