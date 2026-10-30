import { Button } from '@/components/ui/Button';
import { IconButton } from '@/components/ui/IconButton';
import { Input } from '@/components/ui/Input';
import { Segmented } from '@/components/ui/Segmented';
import type { CommunityTopicFilter } from '@/domain/community/types';
import { COMMUNITY_REPLY_PAGE_SIZE, useCommunity } from '@/hooks/useCommunity';
import { history } from '@umijs/max';
import { ArrowLeft, Bookmark, Plus, Search, UserRound } from 'lucide-react';
import { Avatar } from '@/components/ui/Avatar';
import type { ReactNode } from 'react';
import { useRef, useState } from 'react';
import { CommunityPersonalCenter } from '../CommunityPersonalCenter';
import { CommunityTopicDetail } from '../CommunityTopicDetail';
import { EditTopicModal } from '../CommunityTopicDetail/EditTopicModal';
import { CommunityTopicList } from '../CommunityTopicList';
import { TopicTypeIcon } from '../TopicTypeIcon';
import { PublishTopicModal } from '../PublishTopicModal';

type CommunityTopicView = CommunityTopicFilter | 'following';

const CURRENT_USER_AVATAR_SIZE = 40;

const topicTypeOptions: Array<{
  value: CommunityTopicView;
  label: string;
  disabledReason?: string;
  icon?: ReactNode;
}> = [
  { value: 'all', label: '全部' },
  { value: 'following', label: '关注', icon: <Bookmark className="size-3.5" aria-hidden /> },
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
  const topicView: CommunityTopicView =
    community.scope === 'following' ? 'following' : community.topicType;
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
      onOpenTopic={(topicId) => {
        void community.openTopicById?.(topicId);
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
    <div
      aria-hidden={Boolean(selectedTopic)}
      className={selectedTopic ? 'hidden' : 'contents'}
    >
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
      replyLikePending={(postId) =>
        community.interactions?.isActionPending('LIKE', { targetType: 'POST', id: postId })
      }
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

          <section className="mt-4 flex flex-wrap items-center gap-3" aria-label="社区主题筛选">
            <Segmented<CommunityTopicView>
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
              activeOptionClassName="bg-primary/10 text-primary hover:bg-primary/10"
              className="h-9 w-full bg-background sm:w-2/5 sm:min-w-[240px] sm:max-w-[50%]"
            />
            <div className="relative w-full min-w-[160px] sm:ml-auto sm:w-40 sm:flex-none">
              <Search
                className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
                aria-hidden
              />
              <Input
                className="h-9 rounded-lg border-border bg-muted/30 pl-10 pr-4 text-sm"
                value={community.query}
                onChange={(event) => community.setQuery(event.target.value)}
                placeholder="搜索主题..."
                aria-label="搜索社区主题"
              />
            </div>
            <IconButton
              label="个人中心"
              icon={
                community.currentUser ? (
                  <Avatar
                    name={community.currentUser.displayName}
                    src={community.currentUser.avatarUrl}
                    size={CURRENT_USER_AVATAR_SIZE}
                  />
                ) : (
                  <UserRound className="size-4" aria-hidden />
                )
              }
              className="ml-auto size-10 rounded-full sm:ml-0"
              onClick={() => setShowPersonalCenter(true)}
            />
            <Button
              className="h-9 px-4 text-xs"
              leftIcon={<Plus className="size-3.5" aria-hidden />}
              onClick={() => setPublishOpen(true)}
            >
              发帖
            </Button>
          </section>

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
        </div>
      </main>
    </div>
  );
}
