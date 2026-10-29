import { Avatar } from '@/components/ui/Avatar';
import { Button } from '@/components/ui/Button';
import { Pagination } from '@/components/ui/Pagination';
import { Segmented } from '@/components/ui/Segmented';
import type { PersonalCenterActivityType } from '@/domain/community/types';
import { usePersonalCenter } from '@/hooks/usePersonalCenter';
import { ArrowLeft, FileText, Heart, History, MessageSquare, Star, UserRoundPlus } from 'lucide-react';
import type { ReactNode } from 'react';
import { ActivityList } from './ActivityList';
import { FollowList } from './FollowList';
import { personalCenterActivityOptions } from './model';

const activityOptions: Array<{
  value: PersonalCenterActivityType;
  label: string;
  icon: ReactNode;
}> = [
  ...personalCenterActivityOptions.map((option) => {
    const icons = {
      AUTHORED: <FileText className="size-3.5" aria-hidden />,
      REPLIED: <MessageSquare className="size-3.5" aria-hidden />,
      FAVORITED: <Star className="size-3.5" aria-hidden />,
      LIKED: <Heart className="size-3.5" aria-hidden />,
      FOLLOWED: <UserRoundPlus className="size-3.5" aria-hidden />,
      VIEWED: <History className="size-3.5" aria-hidden />,
    } as const;
    return { ...option, icon: icons[option.value] };
  }),
];

export function CommunityPersonalCenter({
  onBack,
  onOpenTopic,
}: {
  onBack: () => void;
  onOpenTopic: (topicId: string) => void;
}) {
  const center = usePersonalCenter();

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

          <Segmented<PersonalCenterActivityType>
            value={center.activityType}
            onChange={center.setActivityType}
            options={activityOptions}
            aria-label="我的内容"
            className="mt-4 h-9 w-fit p-1"
          />

          <div className="mt-4">
            {center.activityType === 'FOLLOWED' ? (
              <FollowList
                items={center.activityPage.items}
                loading={center.loading}
                error={center.error}
                onRetry={center.reload}
                onUnfollow={center.unfollow}
                onOpenTopic={onOpenTopic}
              />
            ) : (
              <ActivityList
                items={center.activityPage.items}
                loading={center.loading}
                error={center.error}
                onRetry={center.reload}
                onOpenTopic={onOpenTopic}
              />
            )}
          </div>

          <div className="mt-4">
            <Pagination
              current={center.page}
              pageSize={center.pageSize}
              total={center.activityPage.total}
              onChange={center.goToPage}
            />
          </div>
        </div>
      </main>
    </div>
  );
}
