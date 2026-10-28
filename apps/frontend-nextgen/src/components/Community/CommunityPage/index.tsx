import { Button } from '@/components/ui/Button';
import { IconButton } from '@/components/ui/IconButton';
import { Input } from '@/components/ui/Input';
import { Segmented } from '@/components/ui/Segmented';
import type { CommunityTopicFilter } from '@/domain/community/types';
import { COMMUNITY_REPLY_PAGE_SIZE, useCommunity } from '@/hooks/useCommunity';
import { history } from '@umijs/max';
import { ArrowLeft, Plus, Search, UserRound } from 'lucide-react';
import { useRef, useState } from 'react';
import { CommunityPersonalCenter } from '../CommunityPersonalCenter';
import { CommunityTopicDetail } from '../CommunityTopicDetail';
import { CommunityTopicList } from '../CommunityTopicList';
import { PublishTopicModal } from '../PublishTopicModal';

const topicTypeOptions: Array<{ value: CommunityTopicFilter; label: string }> = [
  { value: 'all', label: '全部' },
  { value: 'discussion', label: '讨论' },
  { value: 'share', label: '分享' },
  { value: 'poll', label: '投票' },
  { value: 'help', label: '求助' },
];

export function CommunityPage() {
  const community = useCommunity();
  const [publishOpen, setPublishOpen] = useState(false);
  const [showPersonalCenter, setShowPersonalCenter] = useState(false);
  const hasFilter =
    !!community.query.trim() || community.scope === 'mine' || community.topicType !== 'all';
  const scrollRootRef = useRef<HTMLElement>(null);

  if (community.selectedTopic) {
    const topicTarget = { targetType: 'TOPIC' as const, id: community.selectedTopic.id };
    return (
      <CommunityTopicDetail
        currentUserId={community.currentUserId}
        targetSummary={community.targetSummary?.(community.selectedTopic)}
        getAuthorSummary={community.getAuthorSummary}
        loadAuthorSummary={community.loadAuthorSummary}
        followAuthor={community.followAuthor}
        authorFollowLoading={community.authorFollowPending}
        likePending={community.interactions?.isActionPending('LIKE', topicTarget)}
        favoritePending={community.interactions?.isActionPending('FAVORITE', topicTarget)}
        onLike={(target) => community.likeTopic?.(target)}
        onFavorite={(target) => community.favoriteTopic?.(target)}
        topic={community.selectedTopic}
        replies={community.replies}
        loading={community.detailLoading}
        error={community.detailError}
        closing={community.closing}
        helpfulPostId={community.helpfulPostId}
        onBack={community.closeDetail}
        onCloseTopic={community.closeTopic}
        onSetPostHelpful={community.setPostHelpful}
        replyLoading={community.replyLoading}
        replyError={community.replyError}
        replyPage={community.replyPage}
        replyPageCount={community.replyPageCount}
        replyPageSize={COMMUNITY_REPLY_PAGE_SIZE}
        onGoToReplyPage={(page) => void community.goToReplyPage?.(page)}
      />
    );
  }

  if (showPersonalCenter) {
    return (
      <CommunityPersonalCenter
        onBack={() => setShowPersonalCenter(false)}
        onOpenTopic={(topicId) => {
          setShowPersonalCenter(false);
          void community.openTopicById?.(topicId);
        }}
      />
    );
  }

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
            <Segmented<CommunityTopicFilter>
              value={community.topicType}
              onChange={community.setTopicType}
              aria-label="主题类型"
              options={topicTypeOptions}
              className="h-9"
            />
            <Segmented
              value={community.scope}
              onChange={community.setScope}
              aria-label="主题范围"
              options={[
                { value: 'all', label: '全部' },
                { value: 'mine', label: '我的' },
              ]}
              className="h-9"
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
              icon={<UserRound className="size-4" aria-hidden />}
              className="ml-auto size-9 rounded-full sm:ml-0"
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
              scrollRootRef={scrollRootRef}
            />
          </div>
        </div>
      </main>
    </div>
  );
}
