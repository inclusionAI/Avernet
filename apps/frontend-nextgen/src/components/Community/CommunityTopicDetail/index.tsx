import { Avatar } from '@/components/ui/Avatar';
import { Button } from '@/components/ui/Button';
import { Empty } from '@/components/ui/Empty';
import { Skeleton } from '@/components/ui/Skeleton';
import type {
  CommunityAuthor,
  CommunityPostLocation,
  CommunityReply,
  CommunityTopic,
  ForumTargetRef,
  InteractionSummary,
} from '@/domain/community/types';
import { Heart, MessageCircle, Star } from 'lucide-react';
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { Floor } from './Floor';
import { ReplyComposer, type ReplyComposerPayload, type ReplyComposerTarget } from './ReplyComposer';
import { TopicDetailHeader } from './TopicDetailHeader';
import { TopicFirstFloor } from './TopicFirstFloor';
import { acceptsTopicReply, buildReplyGroups } from './model';

export interface CommunityTopicDetailProps {
  topic: CommunityTopic | null;
  replies: CommunityReply[];
  replyTotal?: number;
  loading: boolean;
  error: string | null;
  closing: boolean;
  deleting: boolean;
  helpfulPostId: string | null;
  onBack: () => void;
  currentUserId?: string;
  viewer?: { displayName: string; avatarUrl?: string };
  targetSummary?: InteractionSummary;
  /** Actor-target summary used only to render the current viewer's liked/favorited state. */
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
  getReplySummary?: (postId: string) => InteractionSummary | undefined;
  getReplyViewerSummary?: (postId: string) => InteractionSummary | undefined;
  replyLikePending?: (postId: string) => boolean;
  onLikeReply?: (postId: string) => void;
  onCloseTopic: () => Promise<boolean>;
  onEditTopic: () => void;
  onDeleteTopic: () => Promise<boolean>;
  onSetPostHelpful: (postId: string, helpful: boolean) => Promise<boolean>;
  // 连续回帖窗口；页码只用于后端请求和楼层偏移
  replyLoading: boolean;
  replyError: string | null;
  replySaving?: boolean;
  onSubmitReply?: (payload: ReplyComposerPayload) => Promise<boolean>;
  firstReplyPage: number;
  lastReplyPage: number;
  failedReplyPage?: number | null;
  replyPageCount: number;
  replyPageSize: number;
  onGoToReplyPage: (page: number) => Promise<void>;
  /** Initial navigation target resolved by the backend for personal-center links. */
  pendingPostLocation?: CommunityPostLocation | null;
  onClearPendingPostLocation?: () => void;
}

export function CommunityTopicDetail({
  topic,
  replies,
  replyTotal,
  loading,
  error,
  closing,
  deleting,
  helpfulPostId,
  onBack,
  currentUserId,
  viewer,
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
  getReplySummary,
  getReplyViewerSummary,
  replyLikePending,
  onLikeReply,
  onCloseTopic,
  onDeleteTopic,
  onSetPostHelpful,
  replyLoading,
  replyError,
  replySaving = false,
  onSubmitReply,
  firstReplyPage,
  lastReplyPage,
  failedReplyPage,
  replyPageCount,
  replyPageSize,
  onGoToReplyPage,
  pendingPostLocation = null,
  onClearPendingPostLocation,
}: CommunityTopicDetailProps) {
  const scrollRoot = useRef<HTMLElement>(null);
  const scrollPosition = useRef(0);
  const [expandedReplyIds, setExpandedReplyIds] = useState<Record<string, boolean>>({});
  const replyElementsRef = useRef(new Map<string, HTMLElement | null>());
  const [composer, setComposer] = useState<'topic' | ReplyComposerTarget | null>(null);
  const [picks, setPicks] = useState<number[]>([]);
  useEffect(() => {
    setComposer(null);
    setPicks([]);
    setExpandedReplyIds({});
  }, [topic?.id]);

  const replyOffset = (firstReplyPage - 1) * replyPageSize;
  const floorGroups = useMemo(() => buildReplyGroups(replies, replyOffset + 1), [replies, replyOffset]);
  const registerReplyElement = useCallback(
    (postId?: string) => (element: HTMLElement | null) => {
      if (postId) replyElementsRef.current.set(postId, element);
    },
    [],
  );

  const goToReplyComposer = useCallback(() => {
    setPicks([]);
    setComposer('topic');
  }, []);

  const locateTargetReply = useCallback((postId: string, behavior: ScrollBehavior = 'smooth') => {
    const element = replyElementsRef.current.get(postId);
    if (!element) return;
    element.scrollIntoView({ block: 'center', behavior });
    if (behavior === 'instant' && scrollRoot.current) scrollPosition.current = scrollRoot.current.scrollTop;
    element.classList.add('bg-primary/10');
    const clearOnNextClick = () => element.classList.remove('bg-primary/10');
    window.setTimeout(() => window.addEventListener('click', clearOnNextClick, { once: true }), 0);
  }, []);

  const toggleThreadReplies = useCallback((postId: string) => {
    setExpandedReplyIds((previous) => ({ ...previous, [postId]: !previous[postId] }));
  }, []);

  const getSummaryFor = useCallback((author: CommunityAuthor) => getAuthorSummary?.(author), [getAuthorSummary]);
  const loadSummaryFor = useCallback((author: CommunityAuthor) => loadAuthorSummary?.(author), [loadAuthorSummary]);
  const followFor = useCallback(
    (author: CommunityAuthor) => followAuthor?.(author) ?? Promise.resolve(false),
    [followAuthor],
  );
  const isReplyLikePending = useCallback(
    (postId?: string) => (postId ? replyLikePending?.(postId) === true : false),
    [replyLikePending],
  );

  useEffect(() => {
    if (!pendingPostLocation) return;
    if (pendingPostLocation.postId === pendingPostLocation.rootPostId) return;
    setExpandedReplyIds((previous) => ({
      ...previous,
      [pendingPostLocation.rootPostId]: true,
    }));
  }, [pendingPostLocation]);

  useEffect(() => {
    if (!pendingPostLocation || loading) return;
    const element = replyElementsRef.current.get(pendingPostLocation.postId);
    if (!element) return;
    locateTargetReply(pendingPostLocation.postId, 'instant');
    onClearPendingPostLocation?.();
  }, [expandedReplyIds, loading, locateTargetReply, onClearPendingPostLocation, pendingPostLocation, replies]);

  const requestedPage = useRef<number | null>(null);
  const prependAnchor = useRef<{ page: number; postId: string; top: number } | null>(null);
  const loadReplyPage = (page: number) => {
    if (requestedPage.current !== null) return;
    requestedPage.current = page;
    if (page < firstReplyPage) {
      const postId = floorGroups[0]?.reply.id;
      const element = postId ? replyElementsRef.current.get(postId) : null;
      if (postId && element) prependAnchor.current = { page, postId, top: element.getBoundingClientRect().top };
    }
    void onGoToReplyPage(page).finally(() => {
      if (requestedPage.current === page) requestedPage.current = null;
    });
  };

  useLayoutEffect(() => {
    const anchor = prependAnchor.current;
    const root = scrollRoot.current;
    if (!anchor || !root || anchor.page !== firstReplyPage) return;
    prependAnchor.current = null;
    const element = replyElementsRef.current.get(anchor.postId);
    if (!element) return;
    root.scrollTop += element.getBoundingClientRect().top - anchor.top;
    scrollPosition.current = root.scrollTop;
  }, [firstReplyPage, replies]);

  const loadNearEdge = (root: HTMLElement, direction: number) => {
    if (!topic || loading || error || replyLoading || replySaving || helpfulPostId || replyError || pendingPostLocation) return;
    if (direction < 0 && root.scrollTop <= 80 && firstReplyPage > 1) {
      loadReplyPage(firstReplyPage - 1);
    } else if (direction > 0 && root.scrollHeight - root.scrollTop - root.clientHeight <= 80 && lastReplyPage < replyPageCount) {
      loadReplyPage(lastReplyPage + 1);
    }
  };
  const replyLoadError = replyError && (
    <div className="flex items-center justify-center gap-3 py-2">
      <p className="m-0 text-sm text-muted-foreground">{replyError}</p>
      <Button variant="secondary" size="sm" onClick={() => loadReplyPage(failedReplyPage ?? firstReplyPage)}>
        重试
      </Button>
    </div>
  );
  const earlierReplyError = failedReplyPage != null && failedReplyPage < firstReplyPage;
  const observedReplyCount = (replyTotal ?? replies.length) + replies.reduce(
    (count, reply) => count + (reply.childReplies?.length ?? 0), 0,
  );
  const replyCount = replyTotal == null ? topic?.replyCount ?? observedReplyCount : Math.max(topic?.replyCount ?? 0, observedReplyCount);
  const canReplyToTopic = !!topic && !!currentUserId && acceptsTopicReply(topic);

  return (
    <div className="flex h-full flex-col bg-background">
      <TopicDetailHeader
        topic={topic}
        closing={closing}
        deleting={deleting}
        onBack={onBack}
        onCloseTopic={onCloseTopic}
        onDeleteTopic={onDeleteTopic}
      />
      <main
        ref={scrollRoot}
        className="app-scrollbar min-h-0 flex-1 overflow-y-auto"
        onScroll={(event) => {
          const root = event.currentTarget;
          const direction = root.scrollTop - scrollPosition.current;
          scrollPosition.current = root.scrollTop;
          loadNearEdge(root, direction);
        }}
        onWheel={(event) => loadNearEdge(event.currentTarget, event.deltaY)}
      >
        <div className="flex w-full flex-col px-5 pb-6">
          {loading ? (
            <div className="space-y-3 py-4">
              <Skeleton.ListItem />
              <Skeleton.ListItem />
              <Skeleton.ListItem />
            </div>
          ) : error ? (
            <Empty
              title="详情加载失败"
              description={error}
              action={
                <Button variant="outline" onClick={onBack}>
                  返回列表
                </Button>
              }
            />
          ) : topic ? (
            <>
              {earlierReplyError && replyLoadError}
              {firstReplyPage === 1 ? (
                <TopicFirstFloor
                  onPick={
                    currentUserId && onSubmitReply
                      ? (index) => {
                          setPicks((prev) =>
                            topic.topicConfig?.poll?.selection === 'multi'
                              ? prev.includes(index)
                                ? prev.filter((i) => i !== index)
                                : [...prev, index]
                              : [index],
                          );
                          setComposer('topic');
                        }
                      : undefined
                  }
                  topic={topic}
                  currentUserId={currentUserId}
                  targetSummary={targetSummary}
                  viewerTopicSummary={viewerTopicSummary}
                  getAuthorSummary={getSummaryFor}
                  loadAuthorSummary={loadSummaryFor}
                  followAuthor={followAuthor ? followFor : undefined}
                  authorFollowLoading={authorFollowLoading}
                  authorFollowed={authorFollowed}
                  likePending={likePending}
                  favoritePending={favoritePending}
                  onLike={onLike}
                  onFavorite={onFavorite}
                  registerElement={registerReplyElement}
                />
              ) : null}
              {floorGroups.map(({ reply, floorNo, children }) => (
                <Floor
                  topic={topic}
                  key={reply.id}
                  floor={floorNo}
                  postId={reply.id}
                  replyToPostId={reply.replyToPostId}
                  replyToFloorNo={reply.replyToFloorNo}
                  author={reply.author}
                  body={reply.body}
                  createdAt={reply.createdAt}
                  helpful={reply.helpful}
                  canToggleHelpful={topic.topicType === 'help' && topic.isMine}
                  helpfulLoading={helpfulPostId === reply.id}
                  getReplyLikeSummary={getReplySummary}
                  getReplyViewerSummary={getReplyViewerSummary}
                  replyLikePending={isReplyLikePending}
                  onLike={onLikeReply}
                  onToggleHelpful={() => {
                    void onSetPostHelpful(reply.id, !reply.helpful);
                  }}
                  threadReplies={children}
                  expanded={!!expandedReplyIds[reply.id]}
                  onToggleThreadReplies={() => toggleThreadReplies(reply.id)}
                  onLocateTarget={locateTargetReply}
                  registerElement={registerReplyElement}
                  currentUserId={currentUserId}
                  canReply={
                    !!currentUserId &&
                    topic.status === 'open' &&
                    (topic.topicType === 'discussion' || topic.topicType === 'share')
                  }
                  onReplyTarget={setComposer}
                  onSubmitReply={onSubmitReply}
                  getAuthorSummary={getSummaryFor}
                  loadAuthorSummary={loadSummaryFor}
                  followAuthor={followAuthor ? followFor : undefined}
                  authorFollowLoading={authorFollowLoading}
                  authorFollowed={authorFollowed}
                />
              ))}
              {!replies.length && !replyLoading && !replyError && (
                <p className="py-6 text-center text-sm text-muted-foreground">暂时还没有回复</p>
              )}
              {replyLoading && (
                <p aria-live="polite" className="py-3 text-center text-xs text-muted-foreground">
                  回复加载中...
                </p>
              )}
              {!earlierReplyError && replyLoadError}
            </>
          ) : null}
        </div>
      </main>
      {topic && !loading && !error && (
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
              onCancel={() => {
                setComposer(null);
                setPicks([]);
              }}
              onSubmit={async (payload) => {
                if (!acceptsTopicReply(topic)) return false;
                const ok = await onSubmitReply?.(payload);
                if (ok) setComposer(null);
                return ok === true;
              }}
            />
          ) : null}
          <div className={composer && canReplyToTopic ? "mt-2 flex items-center gap-2" : "flex items-center gap-2"}>
            {canReplyToTopic && !composer ? (
              <Button
                variant="ghost"
                className="h-10 min-w-0 flex-1 justify-start rounded-lg bg-muted/50 px-2 font-normal text-muted-foreground"
                onClick={goToReplyComposer}
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
              <Button variant="ghost" size="sm" aria-label="发表评论" onClick={goToReplyComposer}>
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
      )}
    </div>
  );
}
