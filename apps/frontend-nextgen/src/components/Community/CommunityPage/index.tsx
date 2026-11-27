import { PillTabs } from '@/components/Common/PillTabs';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import type { CommunityTopicFilter, CommunityTopicOpenTarget } from '@/domain/community/types';
import { COMMUNITY_REPLY_PAGE_SIZE, useCommunity } from '@/hooks/useCommunity';
import { history } from '@umijs/max';
import { ArrowLeft, Plus, Search } from 'lucide-react';
import type { ReactNode } from 'react';
import { useRef, useState } from 'react';
import { CommunityFilterBar } from '../CommunityFilterBar';
import { CommunityPersonalCenter } from '../CommunityPersonalCenter';
import { CommunityFollowIcon } from '../CommunityFollowIcon';
import { CommunityTopicDetail } from '../CommunityTopicDetail';
import { EditTopicModal } from '../CommunityTopicDetail/EditTopicModal';
import { CommunityTopicList } from '../CommunityTopicList';
import { TopicTypeIcon } from '../TopicTypeIcon';
import { PublishTopicModal } from '../PublishTopicModal';
import './CommunityPage.css';
import { useNotificationTopicDeepLink } from './useNotificationTopicDeepLink';

type CommunityTopicView = CommunityTopicFilter | 'following';

const viewOptions = [{ value: 'all', label: '全部帖子' }, { value: 'mine', label: '我的' }] as const;

const topicTypeOptions: Array<{
  value: CommunityTopicView;
  label: string;
  disabledReason?: string;
  icon?: ReactNode;
}> = [
  { value: 'all', label: '全部' },
  { value: 'following', label: '关注', icon: <CommunityFollowIcon className="size-3.5" aria-hidden /> },
  { value: 'discussion', label: '讨论', icon: <TopicTypeIcon topicType='discussion' className='size-3.5' /> },
  { value: 'share', label: '分享', icon: <TopicTypeIcon topicType='share' className='size-3.5' /> },
  { value: 'poll', label: '投票', icon: <TopicTypeIcon topicType='poll' className='size-3.5' /> },
  { value: 'help', label: '求助', icon: <TopicTypeIcon topicType='help' className='size-3.5' /> },
];

export function CommunityPage() {
  const community = useCommunity();
  const [publishOpen, setPublishOpen] = useState(false);
  const [editOpen, setEditOpen] = useState(false);
  const [showPersonalCenter, setShowPersonalCenter] = useState(false);
  useNotificationTopicDeepLink({
    openTopicById: community.openTopicById,
    setPublishOpen,
    setEditOpen,
    setShowPersonalCenter,
  });
  const topicView: CommunityTopicView =
    community.scope === 'following' ? 'following' : community.topicType;
  const hasFilter =
    !!community.query.trim() ||
    community.scope === 'mine' ||
    community.scope === 'following' ||
    community.topicType !== 'all';
  const scrollRootRef = useRef<HTMLElement>(null);
  const selectedTopic = community.selectedTopic;
  const viewTabs = (
    <PillTabs<'all' | 'mine'>
      label="社区视图"
      options={viewOptions}
      value={showPersonalCenter ? 'mine' : 'all'}
      onChange={(value) => setShowPersonalCenter(value === 'mine')}
    />
  );
  const personalCenter = (
    <CommunityPersonalCenter
      onOpenTopic={(target: CommunityTopicOpenTarget) => {
        void community.openTopicById?.(target);
      }}
      currentUserId={community.currentUserId}
      getAuthorSummary={community.getAuthorSummary}
      loadAuthorSummary={community.loadAuthorSummary}
      followAuthor={community.followAuthor}
      authorFollowLoading={community.authorFollowPending}
      authorFollowed={community.isAuthorFollowed}
    />
  );
  const topicDetail = selectedTopic ? (
    <CommunityTopicDetail
      key={selectedTopic.id}
      viewer={community.currentUser ?? undefined}
      currentUserId={community.currentUserId}
      targetSummary={community.targetSummary?.(selectedTopic)}
      viewerTopicSummary={community.interactions?.getSummary({
        actor: { actorType: 'HUMAN', id: community.currentUserId },
        target: {
          targetType: 'TOPIC',
          id: selectedTopic.id,
        },
      })}
      getAuthorSummary={community.getAuthorSummary}
      loadAuthorSummary={community.loadAuthorSummary}
      followAuthor={community.followAuthor}
      authorFollowLoading={community.authorFollowPending}
      authorFollowed={community.isAuthorFollowed}
      likePending={community.interactions?.isActionPending('LIKE', {
        targetType: 'TOPIC',
        id: selectedTopic.id,
      })}
      favoritePending={community.interactions?.isActionPending('FAVORITE', {
        targetType: 'TOPIC',
        id: selectedTopic.id,
      })}
      onLike={(target) => community.likeTopic?.(target)}
      onFavorite={(target) => community.favoriteTopic?.(target)}
      getReplySummary={(postId) => community.interactions?.getSummary({ target: { targetType: 'POST', id: postId } })}
      getReplyViewerSummary={(postId) =>
        community.currentUserId
          ? community.interactions?.getSummary({
              actor: { actorType: 'HUMAN', id: community.currentUserId },
              target: { targetType: 'POST', id: postId },
            })
          : undefined
      }
      replyLikePending={(postId) =>
        community.interactions?.isActionPending('LIKE', { targetType: 'POST', id: postId })
      }
      onLikeReply={(postId) => void community.likeReply?.(postId)}
      topic={selectedTopic}
      replies={community.replies}
      replyTotal={community.replyTotal}
      loading={community.detailLoading}
      error={community.detailError}
      closing={community.closing}
      deleting={community.deleting}
      helpfulPostId={community.helpfulPostId}
      onBack={community.closeDetail}
      onCloseTopic={community.closeTopic}
      onEditTopic={() => setEditOpen(true)}
      onDeleteTopic={community.deleteTopic}
      onSetPostHelpful={community.setPostHelpful}
      replyLoading={community.replyLoading}
      replyError={community.replyError}
      replySaving={community.replySaving}
      onSubmitReply={community.createReply}
      firstReplyPage={community.firstReplyPage}
      lastReplyPage={community.lastReplyPage}
      failedReplyPage={community.failedReplyPage}
      replyPageCount={community.replyPageCount}
      replyPageSize={COMMUNITY_REPLY_PAGE_SIZE}
      onGoToReplyPage={community.goToReplyPage}
      pendingPostLocation={community.pendingPostLocation}
      onClearPendingPostLocation={community.clearPendingPostLocation}
    />
  ) : null;

  const detailLayer = editOpen && selectedTopic ? (
    <EditTopicModal open topic={selectedTopic} saving={community.editSaving} onOpenChange={setEditOpen} onSave={community.updateTopic} />
  ) : topicDetail;
  if (detailLayer && !showPersonalCenter) return detailLayer;

  if (publishOpen) {
    return (
      <PublishTopicModal open publishing={community.publishing} onOpenChange={setPublishOpen} onPublish={community.publishTopic} />
    );
  }

  return (
    <>
    <div className={detailLayer ? 'hidden' : 'flex h-full flex-col'}>
      <main ref={scrollRootRef} className="app-scrollbar min-h-0 flex-1 overflow-y-auto">
        <div className="flex w-full flex-col px-5 py-6">
          <header className="m-0 flex items-center gap-2">
            <Button variant="ghost" size="icon" onClick={() => history.push('/lab')} aria-label="返回实验室">
              <ArrowLeft className="size-4" aria-hidden />
            </Button>
            <h2 className="m-0 text-lg font-semibold text-foreground">社区</h2>
          </header>

          <section className="mt-4 flex flex-wrap items-center gap-3" aria-label="社区主题筛选">
            {viewTabs}
            <div className="community-topic-toolbar">
              <div className="community-topic-toolbar-search">
                <Search
                  className="pointer-events-none absolute left-3 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground"
                  aria-hidden
                />
                <Input
                  className="h-8 rounded-lg border-border bg-background pl-8 pr-3 text-xs"
                  value={community.query}
                  onChange={(event) => community.setQuery(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter' && !event.nativeEvent.isComposing && community.query.trim()) {
                      setShowPersonalCenter(false);
                    }
                  }}
                  placeholder="搜索主题..."
                  aria-label="搜索社区主题"
                />
              </div>
              <Button
                className="h-8 px-4 text-xs"
                leftIcon={<Plus className="size-3.5" aria-hidden />}
                onClick={() => setPublishOpen(true)}
              >
                发帖
              </Button>
            </div>
          </section>

          {showPersonalCenter ? personalCenter : <>
          <CommunityFilterBar<CommunityTopicView>
            value={topicView}
            onChange={(value) => {
              if (value === 'following') {
                community.setScope('following');
                return;
              }
              if (community.scope === 'following') community.setScope('all');
              community.setTopicType(value);
            }}
            aria-label="主题类型"
            options={topicTypeOptions}
          />

          <div className="mt-4">
            <CommunityTopicList
              topics={community.topics}
              loading={community.loading}
              error={community.error}
              hasFilter={hasFilter}
              onOpen={(topic) => void community.openTopic(topic)}
              onRetry={() => void community.load()}
              onClearFilter={() => {
                community.setQuery('');
                community.setScope('all');
                community.setTopicType('all');
              }}
              onPublish={() => setPublishOpen(true)}
              hasMore={community.hasMore}
              loadingMore={community.loadingMore}
              loadMoreError={community.loadMoreError}
              loadMore={community.loadMore}
              getTopicSummary={(topic) =>
                community.interactions?.getSummary({
                  target: { targetType: 'TOPIC', id: topic.id },
                })
              }
              currentUserId={community.currentUserId}
              getAuthorSummary={community.getAuthorSummary}
              loadAuthorSummary={community.loadAuthorSummary}
              followAuthor={community.followAuthor}
              authorFollowLoading={community.authorFollowPending}
              authorFollowed={community.isAuthorFollowed}
              scrollRootRef={scrollRootRef}
            />
          </div>
          </>}
        </div>
      </main>
    </div>
    {detailLayer}
    </>
  );
}
