import { Button } from '@/components/ui/Button';
import type { CommunityTopicOpenTarget } from '@/domain/community/types';
import { COMMUNITY_REPLY_PAGE_SIZE, useCommunity } from '@/hooks/useCommunity';
import { history } from '@umijs/max';
import { ArrowLeft } from 'lucide-react';
import { useRef, useState } from 'react';
import { CommunityPersonalCenter } from '../CommunityPersonalCenter';
import { CommunityTopicDetail } from '../CommunityTopicDetail';
import { EditTopicModal } from '../CommunityTopicDetail/EditTopicModal';
import { CommunityTopicList } from '../CommunityTopicList';
import { CommunityHelpCenter } from '../HelpCenter';
import { PublishTopicModal } from '../PublishTopicModal';
import { CommunityToolbar } from './CommunityToolbar';
import { useNotificationTopicDeepLink } from './useNotificationTopicDeepLink';

export function CommunityPage() {
  const community = useCommunity();
  const [publishOpen, setPublishOpen] = useState(false);
  const [helpActive, setHelpActive] = useState(false);
  const [editOpen, setEditOpen] = useState(false);
  const [showPersonalCenter, setShowPersonalCenter] = useState(false);
  useNotificationTopicDeepLink({
    openTopicById: community.openTopicById,
    setPublishOpen,
    setEditOpen,
    setShowPersonalCenter,
  });
  const hasFilter =
    !!community.query.trim() ||
    community.scope === 'mine' ||
    community.scope === 'following' ||
    community.topicType !== 'all';
  const scrollRootRef = useRef<HTMLElement>(null);
  const selectedTopic = community.selectedTopic;
  const personalCenter = (
    <CommunityPersonalCenter
      onBack={() => setShowPersonalCenter(false)}
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
  // Keep this wrapper in the tree in both detail and personal-center renders.
  // Replacing it with the bare component would remount CommunityPersonalCenter
  // and lose the active tab, page, and followed-author selection state.
  const personalCenterLayer = showPersonalCenter ? (
    <div aria-hidden={Boolean(selectedTopic)} className={selectedTopic ? 'hidden' : 'contents'}>
      {personalCenter}
    </div>
  ) : null;
  const topicDetail = selectedTopic ? (
    <CommunityTopicDetail
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
      replyLikePending={(postId) => community.interactions?.isActionPending('LIKE', { targetType: 'POST', id: postId })}
      onLikeReply={(postId) => void community.likeReply?.(postId)}
      topic={selectedTopic}
      replies={community.replies}
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
      replyPage={community.replyPage}
      replyPageCount={community.replyPageCount}
      replyPageSize={COMMUNITY_REPLY_PAGE_SIZE}
      onGoToReplyPage={(page) => void community.goToReplyPage?.(page)}
      pendingPostLocation={community.pendingPostLocation}
      onClearPendingPostLocation={community.clearPendingPostLocation}
    />
  ) : null;

  if (editOpen && selectedTopic) {
    return (
      <>
        {personalCenterLayer}
        <EditTopicModal
          open
          topic={selectedTopic}
          saving={community.editSaving}
          onOpenChange={setEditOpen}
          onSave={community.updateTopic}
        />
      </>
    );
  }

  if (topicDetail) {
    return (
      <>
        {personalCenterLayer}
        {topicDetail}
      </>
    );
  }

  if (showPersonalCenter) return <>{personalCenterLayer}</>;

  if (publishOpen) {
    return (
      <div className="flex h-full flex-col">
        <main className="app-scrollbar min-h-0 flex-1 overflow-y-auto">
          <div className="flex w-full flex-col px-4 py-6 sm:px-6 lg:px-10">
            <PublishTopicModal
              open
              publishing={community.publishing}
              onOpenChange={setPublishOpen}
              onPublish={community.publishTopic}
            />
          </div>
        </main>
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col">
      <main ref={scrollRootRef} className="app-scrollbar min-h-0 flex-1 overflow-y-auto">
        <div className="flex w-full flex-col px-4 py-6 sm:px-6 lg:px-10">
          <header className="m-0 flex items-center gap-2">
            <Button variant="ghost" size="icon" onClick={() => history.push('/lab')} aria-label="返回实验室">
              <ArrowLeft className="size-4" aria-hidden />
            </Button>
            <h2 className="m-0 text-lg font-semibold text-foreground">社区</h2>
          </header>

          <CommunityToolbar
            community={community}
            helpActive={helpActive}
            onHelpActiveChange={setHelpActive}
            onPersonalCenter={() => setShowPersonalCenter(true)}
            onPublish={() => setPublishOpen(true)}
          />

          <div className="mt-4">
            {helpActive ? (
              <CommunityHelpCenter />
            ) : (
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
            )}
          </div>
        </div>
      </main>
    </div>
  );
}
