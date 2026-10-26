import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Segmented } from '@/components/ui/Segmented';
import { COMMUNITY_REPLY_PAGE_SIZE, useCommunity } from '@/hooks/useCommunity';
import { history } from '@umijs/max';
import { ArrowLeft, Plus, Search } from 'lucide-react';
import { useRef, useState } from 'react';
import { CommunityTopicDetail } from '../CommunityTopicDetail';
import { CommunityTopicList } from '../CommunityTopicList';
import { PublishTopicModal } from '../PublishTopicModal';

export function CommunityPage() {
  const community = useCommunity();
  const [publishOpen, setPublishOpen] = useState(false);
  const hasFilter = !!community.query.trim() || community.scope === 'mine';
  const scrollRootRef = useRef<HTMLElement>(null);

  if (community.selectedTopic) {
    return (
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
          <div className="relative min-w-[200px] flex-1">
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
          <Button
            className="ml-auto h-9 px-4 text-xs"
            leftIcon={<Plus className="size-3.5" aria-hidden />}
            onClick={() => setPublishOpen(true)}
          >
            发布主题
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
