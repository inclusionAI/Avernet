import { Avatar } from '@/components/ui/Avatar';
import { Button } from '@/components/ui/Button';
import { Pagination } from '@/components/ui/Pagination';
import type { CommunityAuthor, CommunityTopicOpenTarget, InteractionSummary, PersonalCenterActivityType } from '@/domain/community/types';
import { usePersonalCenter } from '@/hooks/usePersonalCenter';
import { ArrowLeft } from 'lucide-react';
import { CommunityFilterBar } from '../CommunityFilterBar';
import { ActivityList } from './ActivityList';
import { FollowList } from './FollowList';
import { personalCenterActivityOptions } from './model';

export function CommunityPersonalCenter({
  onBack,
  onOpenTopic,
  currentUserId,
  getAuthorSummary,
  loadAuthorSummary,
  followAuthor,
  authorFollowLoading,
  authorFollowed,
}: {
  onBack?: () => void;
  onOpenTopic: (target: CommunityTopicOpenTarget) => void;
  currentUserId?: string;
  getAuthorSummary?: (author: CommunityAuthor) => InteractionSummary | undefined;
  loadAuthorSummary?: (author: CommunityAuthor) => void;
  followAuthor?: (author: CommunityAuthor) => Promise<boolean>;
  authorFollowLoading?: (author: CommunityAuthor) => boolean;
  authorFollowed?: (author: CommunityAuthor) => boolean;
}) {
  const center = usePersonalCenter();

  const pagination = (
    <div className="mt-4">
      <Pagination
        current={center.page}
        pageSize={center.pageSize}
        total={center.activityPage.total}
        onChange={center.goToPage}
      />
    </div>
  );

  const content = (
    <>
      <CommunityFilterBar<PersonalCenterActivityType>
        value={center.activityType}
        onChange={center.setActivityType}
        options={personalCenterActivityOptions}
        aria-label="我的内容"
      />

      <div className="mt-4">
        {center.activityType === 'FOLLOWED' ? (
          <FollowList
            items={center.activityPage.items}
            pagination={pagination}
            loading={center.loading}
            error={center.error}
            onRetry={center.reload}
            onUnfollow={center.unfollow}
            onOpenTopic={onOpenTopic}
            currentUserId={currentUserId}
            getAuthorSummary={getAuthorSummary}
            loadAuthorSummary={loadAuthorSummary}
            followAuthor={followAuthor}
            authorFollowLoading={authorFollowLoading}
            authorFollowed={authorFollowed}
          />
        ) : (
          <ActivityList
            items={center.activityPage.items}
            loading={center.loading}
            error={center.error}
            onRetry={center.reload}
            onOpenTopic={onOpenTopic}
            currentUserId={currentUserId}
            getAuthorSummary={getAuthorSummary}
            loadAuthorSummary={loadAuthorSummary}
            followAuthor={followAuthor}
            authorFollowLoading={authorFollowLoading}
            authorFollowed={authorFollowed}
          />
        )}
      </div>

      {center.activityType !== 'FOLLOWED' && pagination}
    </>
  );
  // 在社区「我的」标签内只展示内容，共用外层社区页头。
  if (!onBack) return content;

  return (
    <div className="flex h-full flex-col bg-background">
      <main className="app-scrollbar min-h-0 flex-1 overflow-y-auto">
        <div className="flex w-full flex-col px-4 py-6 sm:px-6 lg:px-10">
          <header className="m-0 flex items-center gap-3">
            <Button variant="ghost" size="icon" onClick={onBack} aria-label="返回">
              <ArrowLeft className="size-4" aria-hidden />
            </Button>
            <div className="flex min-w-0 items-center gap-3">
              <Avatar
                name={center.identity?.displayName ?? center.ownerUserId}
                src={center.identity?.avatarUrl}
                size={40}
              />
              <div className="min-w-0">
                <h2 className="m-0 text-lg font-semibold text-foreground">我的</h2>
                <p className="m-0 mt-0.5 truncate text-xs text-muted-foreground">
                  {center.identity?.displayName ?? center.ownerUserId}
                </p>
              </div>
            </div>
          </header>

          {content}
        </div>
      </main>
    </div>
  );
}
