import { Button } from '@/components/ui/Button';
import { Empty } from '@/components/ui/Empty';
import { Skeleton } from '@/components/ui/Skeleton';
import type { CommunityAuthor } from '@/domain/community/types';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Floor } from './Floor';
import type { ReplyComposerPayload, ReplyComposerTarget } from './ReplyComposer';
import { TopicDetailFooter } from './TopicDetailFooter';
import { TopicDetailHeader } from './TopicDetailHeader';
import { TopicFirstFloor } from './TopicFirstFloor';
import { acceptsTopicReply, buildReplyGroups } from './model';
import type { CommunityTopicDetailProps } from './types';
import { useReplyScroll } from './useReplyScroll';

export type { CommunityTopicDetailProps } from './types';

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
  const [expandedReplyIds, setExpandedReplyIds] = useState<Record<string, boolean>>({});
  const [composer, setComposer] = useState<'topic' | ReplyComposerTarget | null>(null);
  const [picks, setPicks] = useState<number[]>([]);
  useEffect(() => {
    setComposer(null);
    setPicks([]);
    setExpandedReplyIds({});
  }, [topic?.id]);

  const replyOffset = (firstReplyPage - 1) * replyPageSize;
  const floorGroups = useMemo(() => buildReplyGroups(replies, replyOffset + 1), [replies, replyOffset]);
  const goToReplyComposer = useCallback(() => {
    setPicks([]);
    setComposer('topic');
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

  const { scrollRoot, registerReplyElement, locateTargetReply, loadReplyPage, onScroll, onWheel } = useReplyScroll({
    firstPostId: floorGroups[0]?.reply.id,
    firstReplyPage, lastReplyPage, replyPageCount, onGoToReplyPage,
    replies, expandedReplyIds, loading, pendingPostLocation, onClearPendingPostLocation,
    edgeLoadingBlocked: !topic || loading || !!error || replyLoading || replySaving ||
      !!helpfulPostId || !!replyError || !!pendingPostLocation,
  });
  const replyLoadError = replyError && (
    <div className="flex items-center justify-center gap-3 py-2">
      <p className="m-0 text-sm text-muted-foreground">{replyError}</p>
      <Button variant="secondary" size="sm" onClick={() => loadReplyPage(failedReplyPage ?? firstReplyPage)}>
        重试
      </Button>
    </div>
  );
  const earlierReplyError = failedReplyPage !== null && failedReplyPage !== undefined && failedReplyPage < firstReplyPage;
  const observedReplyCount = (replyTotal ?? replies.length) + replies.reduce(
    (count, reply) => count + (reply.childReplies?.length ?? 0), 0,
  );
  const replyCount = replyTotal === undefined
    ? topic?.replyCount ?? observedReplyCount
    : Math.max(topic?.replyCount ?? 0, observedReplyCount);

  const resetComposer = () => {
    setComposer(null);
    setPicks([]);
  };
  const submitReply = async (payload: ReplyComposerPayload) => {
    if (!topic || !acceptsTopicReply(topic)) return false;
    const ok = await onSubmitReply?.(payload);
    if (ok) setComposer(null);
    return ok === true;
  };

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
        onScroll={onScroll}
        onWheel={onWheel}
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
        <TopicDetailFooter
          topic={topic}
          currentUserId={currentUserId}
          viewer={viewer}
          targetSummary={targetSummary}
          viewerTopicSummary={viewerTopicSummary}
          likePending={likePending}
          favoritePending={favoritePending}
          onLike={onLike}
          onFavorite={onFavorite}
          composer={composer}
          picks={picks}
          setPicks={setPicks}
          replySaving={replySaving}
          replyCount={replyCount}
          onOpenComposer={goToReplyComposer}
          onCancel={resetComposer}
          onSubmit={submitReply}
        />
      )}
    </div>
  );
}
