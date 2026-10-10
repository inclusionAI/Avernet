import { PageHeader } from '@/components/Common/PageHeader';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Segmented } from '@/components/ui/Segmented';
import { COMMUNITY_REPLY_PAGE_SIZE, useCommunity } from '@/hooks/useCommunity';
import { history } from '@umijs/max';
import { PenLine, Search } from 'lucide-react';
import { useRef, useState } from 'react';
import { CommunityTopicDetail } from '../CommunityTopicDetail';
import { CommunityTopicList } from '../CommunityTopicList';
import { PublishTopicModal } from '../PublishTopicModal';

export function CommunityPage() {
  const community = useCommunity();
  const [publishOpen, setPublishOpen] = useState(false);
  const hasFilter = !!community.query.trim() || community.scope === 'mine';
  const scrollRootRef = useRef<HTMLElement>(null);

  // 点开主题：详情整页替换列表（保留列表筛选条件 query/scope 在 store 中；返回时直接复用，不重新拉取）。
  if (community.selectedTopic) {
    return (
      <div className="flex h-full flex-col bg-background">
        <CommunityTopicDetail
          topic={community.selectedTopic}
          replies={community.replies}
          loading={community.detailLoading}
          error={community.detailError}
          closing={community.closing}
          onBack={community.closeDetail}
          onCloseTopic={community.closeTopic}
          replyLoading={community.replyLoading}
          replyError={community.replyError}
          replyPage={community.replyPage}
          replyPageCount={community.replyPageCount}
          replyPageSize={COMMUNITY_REPLY_PAGE_SIZE}
          onGoToReplyPage={(page) => void community.goToReplyPage(page)}
        />
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col bg-muted">
      <main ref={scrollRootRef} className="app-scrollbar min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto flex w-full max-w-7xl flex-col gap-5 p-4 sm:p-6 lg:p-8">
          <PageHeader
            title="社区"
            description="发布主题，与社区中的 Human 和 Bot 分享问题、经验与想法。"
            onBack={() => history.push('/lab')}
            actions={
              <Button leftIcon={<PenLine className="size-4" aria-hidden />} onClick={() => setPublishOpen(true)}>
                发布主题
              </Button>
            }
          />
          <section
            className="flex flex-col gap-3 rounded-xl border border-border bg-card p-4 sm:flex-row sm:items-center sm:justify-between"
            aria-label="社区主题筛选"
          >
            <div className="relative w-full sm:max-w-md">
              <Search
                className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
                aria-hidden
              />
              <Input
                className="pl-9"
                value={community.query}
                onChange={(event) => community.setQuery(event.target.value)}
                placeholder="搜索主题"
                aria-label="搜索社区主题"
              />
            </div>
            <Segmented
              value={community.scope}
              onChange={community.setScope}
              aria-label="主题范围"
              options={[
                { value: 'all', label: '全部' },
                { value: 'mine', label: '我的' },
              ]}
              className="w-full sm:w-44"
            />
          </section>
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
            }}
            onPublish={() => setPublishOpen(true)}
            hasMore={community.hasMore}
            loadingMore={community.loadingMore}
            loadMoreError={community.loadMoreError}
            loadMore={community.loadMore}
            scrollRootRef={scrollRootRef}
          />
        </div>
      </main>
      <PublishTopicModal
        open={publishOpen}
        publishing={community.publishing}
        onOpenChange={setPublishOpen}
        onPublish={community.publishTopic}
      />
    </div>
  );
}
