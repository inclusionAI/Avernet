import { Button } from '@/components/ui/Button';
import { Empty } from '@/components/ui/Empty';
import { Skeleton } from '@/components/ui/Skeleton';
import { TopicFirstFloor } from './TopicFirstFloor';
import type {
  CommunityAuthor,
  CommunityReply,
  CommunityTopic,
  ForumTargetRef,
  InteractionSummary,
} from '@/domain/community/types';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Floor } from './Floor';
import { ReplyComposer, type ReplyComposerPayload } from './ReplyComposer';
import { TopicDetailHeader } from './TopicDetailHeader';
import { buildReplyGroups } from './model';

export interface CommunityTopicDetailProps {
  topic: CommunityTopic | null;
  replies: CommunityReply[];
  loading: boolean;
  error: string | null;
  closing: boolean;
  deleting: boolean;
  helpfulPostId: string | null;
  onBack: () => void;
  currentUserId?: string;
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
  replyFavoritePending?: (postId: string) => boolean;
  onLikeReply?: (postId: string) => void;
  onFavoriteReply?: (postId: string) => void;
  onCloseTopic: () => Promise<boolean>;
  onEditTopic: () => void;
  onDeleteTopic: () => Promise<boolean>;
  onSetPostHelpful: (postId: string, helpful: boolean) => Promise<boolean>;
  // 回帖分页
  replyLoading: boolean;
  replyError: string | null;
  replySaving?: boolean;
  onSubmitReply?: (payload: ReplyComposerPayload) => Promise<boolean>;
  replyPage: number;
  replyPageCount: number;
  replyPageSize: number;
  onGoToReplyPage: (page: number) => void;
}

export function CommunityTopicDetail({
  topic,
  replies,
  loading,
  error,
  closing,
  deleting,
  helpfulPostId,
  onBack,
  currentUserId,
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
  replyFavoritePending,
  onLikeReply,
  onFavoriteReply,
  onCloseTopic,
  onEditTopic,
  onDeleteTopic,
  onSetPostHelpful,
  replyLoading,
  replyError,
  replySaving = false,
  onSubmitReply,
  replyPage,
  replyPageCount,
  replyPageSize,
  onGoToReplyPage,
}: CommunityTopicDetailProps) {
  const [expandedReplyIds, setExpandedReplyIds] = useState<Record<string, boolean>>({});
  const replyElementsRef = useRef(new Map<string, HTMLElement | null>());

  const replyOffset = (replyPage - 1) * replyPageSize;
  const floorGroups = useMemo(() => buildReplyGroups(replies, replyOffset + 1), [replies, replyOffset]);
  const registerReplyElement = useCallback(
    (postId?: string) => (element: HTMLElement | null) => {
      if (postId) replyElementsRef.current.set(postId, element);
    },
    [],
  );

  const locateTargetReply = useCallback((postId: string) => {
    const element = replyElementsRef.current.get(postId);
    if (!element) return;
    element.scrollIntoView({ block: 'center', behavior: 'smooth' });
    element.classList.add('bg-primary/10');
    window.setTimeout(() => element.classList.remove('bg-primary/10'), 1200);
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

  // 楼中楼是楼层页面内的局部展开状态；翻页后必须回到默认展示前 2 条。
  useEffect(() => {
    setExpandedReplyIds({});
  }, [replyPage]);

  return (
    <div className="flex h-full flex-col bg-background">
      <TopicDetailHeader
        topic={topic}
        closing={closing}
        deleting={deleting}
        replyPage={replyPage}
        replyPageCount={replyPageCount}
        replyLoading={replyLoading}
        onBack={onBack}
        onCloseTopic={onCloseTopic}
        onEditTopic={onEditTopic}
        onDeleteTopic={onDeleteTopic}
        onGoToReplyPage={onGoToReplyPage}
      />
      <main className="app-scrollbar min-h-0 flex-1 overflow-y-auto">
        <div className="flex w-full flex-col px-4 pb-6 sm:px-6 lg:px-10">
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
              {/* 楼主楼层仅在第 1 页显示：分页语义本身一直以一级楼层为单位。 */}
              {replyPage === 1 ? (
                <TopicFirstFloor
                  topic={topic}
                  currentUserId={currentUserId}
                  targetSummary={targetSummary}
                  viewerTopicSummary={viewerTopicSummary}
                  getAuthorSummary={getSummaryFor}
                  loadAuthorSummary={loadSummaryFor}
                  followAuthor={followFor}
                  authorFollowLoading={authorFollowLoading}
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
                  replyFavoritePending={replyFavoritePending}
                  onLike={onLikeReply}
                  onFavoriteReply={onFavoriteReply}
                  onToggleHelpful={() => {
                    void onSetPostHelpful(reply.id, !reply.helpful);
                  }}
                  threadReplies={children}
                  expanded={!!expandedReplyIds[reply.id]}
                  onToggleThreadReplies={() => toggleThreadReplies(reply.id)}
                  onLocateTarget={locateTargetReply}
                  registerElement={registerReplyElement}
                  currentUserId={currentUserId}
                  canReply={topic.topicType === 'discussion' || topic.topicType === 'share'}
                  replySaving={replySaving}
                  onSubmitReply={onSubmitReply}
                  getAuthorSummary={getSummaryFor}
                  loadAuthorSummary={loadSummaryFor}
                  followAuthor={followFor}
                  authorFollowLoading={authorFollowLoading}
                  authorFollowed={authorFollowed}
                />
              ))}
              {!replies.length && !replyLoading && (
                <p className="py-6 text-center text-sm text-muted-foreground">暂时还没有回复</p>
              )}
              {replyLoading && (
                <p aria-live="polite" className="py-3 text-center text-xs text-muted-foreground">
                  回复加载中...
                </p>
              )}
              {replyError && (
                <div className="mt-4 flex items-center justify-center gap-3 py-2">
                  <p className="m-0 text-sm text-muted-foreground">{replyError}</p>
                  <Button variant="secondary" size="sm" onClick={() => onGoToReplyPage(replyPage)}>
                    重试
                  </Button>
                </div>
              )}
              {topic.status === 'open' && currentUserId && topic.topicType !== 'notice' ? (
                <ReplyComposer
                  topic={topic}
                  submitLabel="回复主题"
                  saving={replySaving}
                  onSubmit={(payload) => onSubmitReply?.(payload) ?? Promise.resolve(false)}
                />
              ) : null}
            </>
          ) : null}
        </div>
      </main>
    </div>
  );
}
